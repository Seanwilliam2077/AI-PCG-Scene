// Copyright (c) TA Pipeline. All Rights Reserved.

#include "PipelineRunner.h"

#include "AISceneBuilderSettings.h"
#include "PipelineNotification.h"

#include "Dom/JsonObject.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Notifications/NotificationManager.h"
#include "HAL/FileManager.h"
#include "IPythonScriptPlugin.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Widgets/Notifications/SNotificationList.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

DEFINE_LOG_CATEGORY_STATIC(LogAISceneBuilder, Log, All);

namespace
{
	constexpr float kTickIntervalSeconds = 0.5f;

	/** 模块级单例的存放处。见 FPipelineRunner::Get() 的注释。 */
	TSharedPtr<FPipelineRunner> GRunner;

	/** 各阶段的超时。s03 由设置决定（云生成最慢），其余给宽裕的固定值。 */
	double StageTimeoutSeconds(EPipelineStage Stage)
	{
		const UAISceneBuilderSettings& Settings = UAISceneBuilderSettings::Get();
		switch (Stage)
		{
		case EPipelineStage::Perceive: return 15 * 60.0;
		case EPipelineStage::Layout:   return 5 * 60.0;
		case EPipelineStage::Gen2D:    return FMath::Max(5, Settings.GenerationTimeoutMinutes) * 60.0;
		case EPipelineStage::Gen3D:    return FMath::Max(5, Settings.GenerationTimeoutMinutes) * 60.0;
		default:                       return 30 * 60.0;
		}
	}

	/** vendor 的三方依赖目录（numpy/cv2/yaml 的 cp311 wheel 解包处）。 */
	FString VendorSitePackagesDir()
	{
		if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AISceneBuilder")))
		{
			return FPaths::ConvertRelativePathToFull(FPaths::Combine(
				Plugin->GetBaseDir(), TEXT("Content/Python/Win64/Lib/site-packages")));
		}
		return FString();
	}

	/** 路径 → Python 原始字符串字面量。反斜杠在 r'' 里原样保留；路径里不含引号。 */
	FString PyRawStr(const FString& In)
	{
		FString S = In;
		S.ReplaceInline(TEXT("'"), TEXT(""));       // 防御：Windows 路径本不该有单引号
		return FString::Printf(TEXT("r'%s'"), *S);
	}
}

TSharedRef<FPipelineRunner> FPipelineRunner::Get()
{
	if (!GRunner.IsValid())
	{
		GRunner = MakeShared<FPipelineRunner>();
	}
	return GRunner.ToSharedRef();
}

void FPipelineRunner::Teardown()
{
	if (GRunner.IsValid())
	{
		GRunner->Cancel();      // 析构里也会调，但这里显式一次，语义更清楚
		GRunner.Reset();
	}
}

const TCHAR* FPipelineRunner::StageId(EPipelineStage Stage)
{
	switch (Stage)
	{
	case EPipelineStage::Perceive: return TEXT("s01_perceive");
	case EPipelineStage::Layout:   return TEXT("s02_layout");
	case EPipelineStage::Gen2D:    return TEXT("s03_gen2d");
	case EPipelineStage::Gen3D:    return TEXT("s04_gen3d");
	case EPipelineStage::Assemble: return TEXT("s05_build_scene");
	default:                       return TEXT("");
	}
}

FText FPipelineRunner::StageDisplayName(EPipelineStage Stage)
{
	switch (Stage)
	{
	case EPipelineStage::Perceive: return NSLOCTEXT("AISB", "S1", "1. 场景理解");
	case EPipelineStage::Layout:   return NSLOCTEXT("AISB", "S2", "2. 布局求解");
	case EPipelineStage::Gen2D:    return NSLOCTEXT("AISB", "S3", "3. 二维素材生成");
	case EPipelineStage::Gen3D:    return NSLOCTEXT("AISB", "S4", "4. 三维资产生成");
	case EPipelineStage::Assemble: return NSLOCTEXT("AISB", "S5", "5. 装配到关卡");
	default:                       return FText::GetEmpty();
	}
}

TArray<FString> FPipelineRunner::StageOutputs(EPipelineStage Stage)
{
	switch (Stage)
	{
	case EPipelineStage::Perceive: return { TEXT("calib.json"), TEXT("perception.json") };
	case EPipelineStage::Layout:   return { TEXT("scene_layout.json"), TEXT("cameras.json") };
	case EPipelineStage::Gen2D:    return { TEXT("gen2d_manifest.json") };
	case EPipelineStage::Gen3D:    return { TEXT("asset_registry.json") };
	case EPipelineStage::Assemble: return { TEXT("scene_manifest.json") };
	default:                       return {};
	}
}

FPipelineRunner::FPipelineRunner() = default;

FPipelineRunner::~FPipelineRunner()
{
	Cancel();
}

FString FPipelineRunner::GetRunDir() const
{
	// run 目录名是「参考图内容 sha256 前 8 位」，由 Python 侧决定。
	// 与其在 C++ 里重算一遍（要和 hashlib 逐位一致，脆弱），不如让 Python 把
	// 实际用的 run_dir 写进进度文件，这里直接读——单一真相源。
	if (!CachedRunDir.IsEmpty() && FPaths::DirectoryExists(CachedRunDir))
	{
		return CachedRunDir;
	}

	// 尚未跑过任何阶段（例如刚重开编辑器）：取 output/ 下最新的 run_* 目录。
	const FString OutputRoot = FPaths::Combine(
		UAISceneBuilderSettings::Get().ResolvePipelineRoot(), TEXT("output"));
	TArray<FString> Dirs;
	IFileManager::Get().FindFiles(Dirs, *(OutputRoot / TEXT("run_*")), false, true);

	FString Newest;
	FDateTime NewestTime = FDateTime::MinValue();
	for (const FString& Dir : Dirs)
	{
		const FString Full = FPaths::Combine(OutputRoot, Dir);
		const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Full);
		if (Stamp > NewestTime)
		{
			NewestTime = Stamp;
			Newest = Full;
		}
	}
	return Newest;
}

const FStageStatus& FPipelineRunner::GetStatus(EPipelineStage Stage) const
{
	static const FStageStatus Empty;
	const uint8 Index = (uint8)Stage;
	return Index < (uint8)EPipelineStage::Count ? Statuses[Index] : Empty;
}

void FPipelineRunner::SetStatus(EPipelineStage Stage, const FStageStatus& Status)
{
	const uint8 Index = (uint8)Stage;
	if (Index >= (uint8)EPipelineStage::Count)
	{
		return;
	}
	Statuses[Index] = Status;
	OnStageChanged.Broadcast(Stage, Status);
}

bool FPipelineRunner::StageOutputsExist(EPipelineStage Stage) const
{
	const FString RunDir = GetRunDir();
	if (RunDir.IsEmpty())
	{
		return false;
	}
	for (const FString& Out : StageOutputs(Stage))
	{
		if (!FPaths::FileExists(FPaths::Combine(RunDir, Out)))
		{
			return false;
		}
	}
	return true;
}

void FPipelineRunner::RefreshFromDisk()
{
	for (uint8 i = 0; i < (uint8)EPipelineStage::Count; ++i)
	{
		const EPipelineStage Stage = (EPipelineStage)i;
		if (RunningStage == Stage && bTaskActive)
		{
			continue;   // 正在跑的别覆盖
		}
		FStageStatus Status;
		if (StageOutputsExist(Stage))
		{
			Status.State = EStageState::Succeeded;
			Status.Progress = 1.f;
			Status.Message = TEXT("产物已存在");
		}
		SetStatus(Stage, Status);
	}
}

bool FPipelineRunner::RunStage(EPipelineStage Stage)
{
	if (bTaskActive)
	{
		UE_LOG(LogAISceneBuilder, Warning, TEXT("已有阶段在运行，忽略本次请求"));
		return false;
	}

	const UAISceneBuilderSettings& Settings = UAISceneBuilderSettings::Get();
	const FString Root = Settings.ResolvePipelineRoot();
	const FString Ref = Settings.ReferenceImage.FilePath;

	FStageStatus Status;
	Status.State = EStageState::Failed;

	IPythonScriptPlugin* PythonPlugin = IPythonScriptPlugin::Get();
	if (!PythonPlugin || !PythonPlugin->IsPythonAvailable())
	{
		Status.Error = TEXT("内嵌 Python 不可用：请确认 Python Editor Script Plugin 已启用。");
		SetStatus(Stage, Status);
		return false;
	}
	if (Ref.IsEmpty() || !FPaths::FileExists(Ref))
	{
		Status.Error = TEXT("请先选择参考图。");
		SetStatus(Stage, Status);
		return false;
	}
	const FString Runner = FPaths::Combine(Root, TEXT("ue"), TEXT("embedded_runner.py"));
	if (!FPaths::FileExists(Runner))
	{
		Status.Error = FString::Printf(TEXT("找不到 embedded_runner.py：%s"), *Runner);
		SetStatus(Stage, Status);
		return false;
	}

	// 进度文件放在 output/ 根下的固定位置：插件在启动前并不知道 run 目录叫什么
	// （那是参考图哈希），所以不能把进度文件放进去。Python 会把实际 run_dir
	// 写进这个文件，插件再回读。文件由 Python 侧原子写（tmp + rename）。
	const FString OutputRoot = FPaths::Combine(Root, TEXT("output"));
	IFileManager::Get().MakeDirectory(*OutputRoot, /*Tree=*/true);
	ProgressPath = FPaths::Combine(OutputRoot, TEXT("_ui_progress.json"));
	IFileManager::Get().Delete(*ProgressPath, false, true, true);

	// 在 GameThread 上执行一条「启动后台线程」的语句，立即返回。
	// embedded_runner.start 内部 spawn threading.Thread 跑 s01–s04（纯 Python），
	// 编辑器不冻结；进度仍走 _ui_progress.json（协议与子进程时代完全一致）。
	const FString Vendor = VendorSitePackagesDir();
	const FString Config = FPaths::Combine(Root, TEXT("configs"), Settings.ConfigProfile);
	const FString Statement = FString::Printf(
		TEXT("import sys\n")
		TEXT("for _p in (%s, %s):\n")
		TEXT("    if _p not in sys.path: sys.path.insert(0, _p)\n")
		TEXT("from ue import embedded_runner as _er\n")
		TEXT("print('ASB_START:' + _er.start(ref=%s, stages='%s', config=%s, progress=%s, vendor=%s, offline=%s))\n"),
		*PyRawStr(Root), *PyRawStr(Vendor),
		*PyRawStr(Ref), StageId(Stage), *PyRawStr(Config), *PyRawStr(ProgressPath),
		*PyRawStr(Vendor), Settings.bOfflineReplay ? TEXT("True") : TEXT("False"));

	FPythonCommandEx Command;
	Command.Command = Statement;
	Command.ExecutionMode = EPythonCommandExecutionMode::ExecuteStatement;
	Command.FileExecutionScope = EPythonFileExecutionScope::Public;
	const bool bExecOk = PythonPlugin->ExecPythonCommandEx(Command);

	bool bStarted = false;
	bool bBusy = false;
	for (const FPythonLogOutputEntry& Entry : Command.LogOutput)
	{
		if (Entry.Output.Contains(TEXT("ASB_START:started")))
		{
			bStarted = true;
		}
		else if (Entry.Output.Contains(TEXT("ASB_START:busy")))
		{
			bBusy = true;
		}
	}

	if (!bExecOk || !bStarted)
	{
		if (bBusy)
		{
			Status.Error = TEXT("上一个管线线程仍在后台运行（取消不等于终止），请等它结束。");
		}
		else
		{
			FString Tail;
			for (const FPythonLogOutputEntry& Entry : Command.LogOutput)
			{
				Tail += Entry.Output + TEXT("\n");
			}
			Status.Error = FString::Printf(
				TEXT("内嵌 Python 启动失败，详见 Output Log。\n%s"), *Tail.Right(600));
		}
		SetStatus(Stage, Status);
		return false;
	}

	++RunToken;
	bTaskActive = true;
	RunningStage = Stage;
	StartTime = FPlatformTime::Seconds();
	Deadline = StartTime + StageTimeoutSeconds(Stage);

	Status.State = EStageState::Running;
	Status.Progress = 0.f;
	Status.Message = TEXT("启动中…");
	Status.Error.Reset();
	SetStatus(Stage, Status);

	OpenNotification(Stage);

	TickHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateSP(this, &FPipelineRunner::Tick), kTickIntervalSeconds);

	UE_LOG(LogAISceneBuilder, Log, TEXT("内嵌启动 %s（root=%s）"), StageId(Stage), *Root);
	return true;
}

bool FPipelineRunner::ReadProgressFile(FStageStatus& OutStatus)
{
	FString Raw;
	if (!FFileHelper::LoadFileToString(Raw, *ProgressPath))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Json;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Raw);
	// 解析失败多半是撞上了半写状态；跳过本次 tick 即可，下一次就好了
	if (!FJsonSerializer::Deserialize(Reader, Json) || !Json.IsValid())
	{
		return false;
	}

	double Pct = 0.0;
	Json->TryGetNumberField(TEXT("pct"), Pct);
	OutStatus.Progress = FMath::Clamp((float)Pct, 0.f, 1.f);
	Json->TryGetStringField(TEXT("msg"), OutStatus.Message);
	Json->TryGetStringField(TEXT("error"), OutStatus.Error);

	FString RunDirFromFile;
	if (Json->TryGetStringField(TEXT("run_dir"), RunDirFromFile) && !RunDirFromFile.IsEmpty())
	{
		CachedRunDir = RunDirFromFile;      // Python 报告的实际 run 目录
	}

	FString State;
	Json->TryGetStringField(TEXT("state"), State);
	if (State == TEXT("done"))
	{
		OutStatus.State = EStageState::Succeeded;
	}
	else if (State == TEXT("failed"))
	{
		OutStatus.State = EStageState::Failed;
	}
	else
	{
		OutStatus.State = EStageState::Running;
	}
	return true;
}

bool FPipelineRunner::Tick(float /*DeltaTime*/)
{
	const uint64 MyToken = RunToken;
	if (!bTaskActive)
	{
		return false;                       // 已被取消/结束：注销 ticker
	}

	const double Now = FPlatformTime::Seconds();

	// 进度：读得到就更新，读不到（尚未写/半写）就沿用上次
	FStageStatus Status = GetStatus(RunningStage);
	Status.ElapsedSeconds = Now - StartTime;
	FStageStatus FromFile;
	const bool bHasProgress = ReadProgressFile(FromFile);
	if (bHasProgress)
	{
		FromFile.ElapsedSeconds = Status.ElapsedSeconds;
		Status = FromFile;
	}

	// 没有进程句柄可查——进度文件的 done/failed 就是终态信号
	// （embedded_runner 对任何异常都会写 failed，兜底见 _write_fail）。
	const bool bTerminal = bHasProgress &&
		(Status.State == EStageState::Succeeded || Status.State == EStageState::Failed);

	if (!bTerminal)
	{
		if (Now >= Deadline)
		{
			FinishStage(EStageState::Failed,
				FString::Printf(TEXT("超时（%.0f 分钟）。线程仍在后台，完成后可点「刷新」恢复状态。"),
					(Now - StartTime) / 60.0));
			return false;
		}
		Status.State = EStageState::Running;
		SetStatus(RunningStage, Status);
		return MyToken == RunToken;         // token 变了说明被取代，自行注销
	}

	// 终态：以「产物是否齐全」为最终判据，进度文件只作参考
	const bool bOutputsOk = StageOutputsExist(RunningStage);
	if (bOutputsOk && Status.State != EStageState::Failed)
	{
		FinishStage(EStageState::Succeeded, FString());
	}
	else
	{
		FString Error = Status.Error;
		if (Error.IsEmpty())
		{
			Error = bOutputsOk ? TEXT("阶段报告失败")
			                   : TEXT("阶段已结束但产物不完整，详见 output/embedded_run.log 与 pipeline_log.jsonl");
		}
		FinishStage(EStageState::Failed, Error);
	}
	return false;
}

void FPipelineRunner::FinishStage(EStageState NewState, const FString& Error)
{
	// 内嵌线程不可强杀：这里只是 UI 层面的收尾。双重启动由
	// embedded_runner.start 的 busy 自保挡住。
	bTaskActive = false;
	if (TickHandle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
		TickHandle.Reset();
	}

	FStageStatus Status = GetStatus(RunningStage);
	Status.State = NewState;
	Status.Error = Error;
	Status.ElapsedSeconds = FPlatformTime::Seconds() - StartTime;
	if (NewState == EStageState::Succeeded)
	{
		Status.Progress = 1.f;
		if (Status.Message.IsEmpty())
		{
			Status.Message = TEXT("完成");
		}
	}
	SetStatus(RunningStage, Status);

	UE_LOG(LogAISceneBuilder, Log, TEXT("%s 结束：%s %s"),
		StageId(RunningStage),
		NewState == EStageState::Succeeded ? TEXT("成功") : TEXT("失败"), *Error);

	// 收通知要放在 SetStatus 之后：通知里的文本是绑到 Statuses[] 上的属性委托，
	// 先收就会定格在旧文本上。
	CloseNotification(NewState);

	RunningStage = EPipelineStage::Count;
}

// ------------------------------------------------------------ 进度通知

void FPipelineRunner::OpenNotification(EPipelineStage Stage)
{
	if (Notification.IsValid() || !FSlateApplication::IsInitialized())
	{
		return;     // 无头模式（-run=pythonscript）下没有 Slate，直接跳过
	}

	// 只捕获自身的弱引用。通知由 Slate 的通知管理器持有，寿命不受本对象约束；
	// 捕获裸 this 会在模块卸载后变成野指针。
	TWeakPtr<FPipelineRunner> WeakSelf = AsShared();

	const TSharedRef<SPipelineNotification> Content = SNew(SPipelineNotification)
		.Title(FText::Format(LOCTEXT("NotifTitle", "AI Scene Builder — {0}"), StageDisplayName(Stage)))
		.Detail_Lambda([WeakSelf, Stage]()
		{
			const TSharedPtr<FPipelineRunner> Self = WeakSelf.Pin();
			if (!Self.IsValid())
			{
				return FText::GetEmpty();
			}
			const FStageStatus& S = Self->GetStatus(Stage);
			const FString Text = S.Error.IsEmpty() ? S.Message : S.Error;
			return FText::FromString(FString::Printf(TEXT("%s  （%.0fs）"),
				Text.IsEmpty() ? TEXT("运行中…") : *Text, S.ElapsedSeconds));
		})
		.Percent_Lambda([WeakSelf, Stage]()
		{
			const TSharedPtr<FPipelineRunner> Self = WeakSelf.Pin();
			// 空 TOptional = 进度未知，进度条自动走不确定态动画
			return Self.IsValid() ? TOptional<float>(Self->GetStatus(Stage).Progress)
								  : TOptional<float>();
		})
		.OnCancel(FSimpleDelegate::CreateLambda([WeakSelf]()
		{
			if (const TSharedPtr<FPipelineRunner> Self = WeakSelf.Pin())
			{
				Self->Cancel();
			}
		}));

	FNotificationInfo Info(StaticCastSharedRef<INotificationWidget>(Content));
	Info.bFireAndForget = false;        // 何时消失由 CloseNotification 决定
	Info.FadeOutDuration = 1.0f;
	Info.ExpireDuration = 4.0f;

	Notification = FSlateNotificationManager::Get().AddNotification(Info);
	if (Notification.IsValid())
	{
		Notification->SetCompletionState(SNotificationItem::CS_Pending);
	}
}

void FPipelineRunner::CloseNotification(EStageState FinalState)
{
	if (!Notification.IsValid())
	{
		return;
	}
	Notification->SetCompletionState(FinalState == EStageState::Succeeded
		? SNotificationItem::CS_Success : SNotificationItem::CS_Fail);
	Notification->ExpireAndFadeout();
	Notification.Reset();
}

void FPipelineRunner::Cancel()
{
	if (!bTaskActive)
	{
		return;
	}
	++RunToken;                             // 让还在飞的 ticker 自行退出
	FinishStage(EStageState::Cancelled, TEXT("已放弃关注（后台线程会跑完当前阶段自然结束）"));
}

#undef LOCTEXT_NAMESPACE
