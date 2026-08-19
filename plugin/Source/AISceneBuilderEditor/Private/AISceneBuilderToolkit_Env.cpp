// Copyright (c) TA Pipeline. All Rights Reserved.
//
// Toolkit 的两块「与运行环境打交道」的逻辑，单独一个 TU 便于阅读：
//   * CheckEnvironment / OnInstallDependencies —— 内嵌 Python 与 vendor 依赖
//   * OnAssembleInEditor                       —— s05 编译 + 装配（GameThread）
//
// v2 起整条管线跑在编辑器内嵌 Python（3.11.8）里：依赖（numpy/cv2/yaml）vendor
// 在插件 Content/Python/Win64/Lib/site-packages，评审机零 pip、零外部解释器。

#include "AISceneBuilderToolkit.h"

#include "AISceneBuilderSettings.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "IPythonScriptPlugin.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "PythonScriptTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

DEFINE_LOG_CATEGORY_STATIC(LogAISceneBuilderEnv, Log, All);

namespace
{
	/** 管线必须的三个第三方包（requests 只有云端 3D 才要，资产库模式不需要）。 */
	const TCHAR* const kRequiredModules = TEXT("numpy,cv2,yaml");

	/**
	 * ExecPythonCommandEx 返回 true 并不代表脚本没抛异常，得自己嗅。
	 * 与本机 CityGenTool 的做法一致。
	 */
	bool LooksLikePythonException(const FString& Text)
	{
		static const TCHAR* Markers[] = {
			TEXT("Traceback"), TEXT("SyntaxError"), TEXT("IndentationError"),
			TEXT("NameError"), TEXT("AttributeError"), TEXT("RuntimeError"),
			TEXT("TypeError"), TEXT("ValueError"), TEXT("KeyError"),
			TEXT("ImportError"), TEXT("ModuleNotFoundError"), TEXT("FileNotFoundError"),
		};
		for (const TCHAR* Marker : Markers)
		{
			if (Text.Contains(Marker))
			{
				return true;
			}
		}
		return false;
	}

	FString VendorSitePackagesDir()
	{
		if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AISceneBuilder")))
		{
			return FPaths::ConvertRelativePathToFull(FPaths::Combine(
				Plugin->GetBaseDir(), TEXT("Content/Python/Win64/Lib/site-packages")));
		}
		return FString();
	}

	FString PyRawStr(const FString& In)
	{
		FString S = In;
		S.ReplaceInline(TEXT("'"), TEXT(""));
		return FString::Printf(TEXT("r'%s'"), *S);
	}

	/** 每条内嵌语句共用的开场白：把 vendor 与管线根插进 sys.path。幂等。 */
	FString PathsPrologue(const FString& Root)
	{
		return FString::Printf(
			TEXT("import sys\n")
			TEXT("for _p in (%s, %s):\n")
			TEXT("    if _p not in sys.path: sys.path.insert(0, _p)\n"),
			*PyRawStr(Root), *PyRawStr(VendorSitePackagesDir()));
	}

	/** 执行内嵌语句并收集日志输出（含 print 与错误行）。 */
	bool ExecEmbedded(const FString& Statement, FString& OutLog, FString& OutError)
	{
		IPythonScriptPlugin* PythonPlugin = IPythonScriptPlugin::Get();
		if (!PythonPlugin || !PythonPlugin->IsPythonAvailable())
		{
			OutError = TEXT("内嵌 Python 不可用：请确认 Python Editor Script Plugin 已启用。");
			return false;
		}
		FPythonCommandEx Command;
		Command.Command = Statement;
		Command.ExecutionMode = EPythonCommandExecutionMode::ExecuteStatement;
		Command.FileExecutionScope = EPythonFileExecutionScope::Public;
		const bool bExecOk = PythonPlugin->ExecPythonCommandEx(Command);

		for (const FPythonLogOutputEntry& Entry : Command.LogOutput)
		{
			OutLog += Entry.Output + TEXT("\n");
			if (Entry.Type == EPythonLogOutputType::Error && OutError.IsEmpty()
				&& LooksLikePythonException(Entry.Output))
			{
				OutError = Entry.Output;
			}
		}
		if (!bExecOk && OutError.IsEmpty())
		{
			OutError = Command.CommandResult.IsEmpty()
				? TEXT("Python 命令执行失败。") : Command.CommandResult;
		}
		if (bExecOk && OutError.IsEmpty() && LooksLikePythonException(Command.CommandResult))
		{
			OutError = Command.CommandResult;
		}
		return bExecOk && OutError.IsEmpty();
	}
}

void FAISceneBuilderToolkit::CheckEnvironment()
{
	const UAISceneBuilderSettings& Settings = UAISceneBuilderSettings::Get();

	if (RefPathText.IsValid() && !Settings.ReferenceImage.FilePath.IsEmpty())
	{
		RefPathText->SetText(FText::FromString(Settings.ReferenceImage.FilePath));
	}

	bEnvironmentOk = false;
	MissingPackages.Reset();

	const FString Root = Settings.ResolvePipelineRoot();

	// 内嵌解释器上逐个探测依赖，缺哪个报哪个。find_spec 是毫秒级的，放主线程无妨。
	// 顺带把生效配置的 API 端点与模型名读出来——「API 显式呈现在 UE 中」的落点之一。
	const FString ConfigPath = FPaths::Combine(Root, TEXT("configs"), Settings.ConfigProfile);
	const FString Probe = PathsPrologue(Root) + FString::Printf(
		TEXT("import importlib.util\n")
		TEXT("print('ASB_MISS:' + ','.join(m for m in '%s'.split(',')")
		TEXT(" if importlib.util.find_spec(m) is None))\n")
		TEXT("try:\n")
		TEXT("    import yaml\n")
		TEXT("    _c = yaml.safe_load(open(%s, encoding='utf-8'))\n")
		TEXT("    print('ASB_API:' + _c['vlm_gateway']['url'] + ' | ' + _c['models']['vision']")
		TEXT(" + '/' + _c['models']['image'])\n")
		TEXT("except Exception as _e:\n")
		TEXT("    print('ASB_API:?')\n"),
		kRequiredModules, *PyRawStr(ConfigPath));

	FString Log, Error;
	const bool bProbeOk = ExecEmbedded(Probe, Log, Error);

	auto ExtractMarker = [&Log](const TCHAR* Marker) -> FString
	{
		const int32 Pos = Log.Find(Marker);
		if (Pos == INDEX_NONE)
		{
			return FString();
		}
		FString Tail = Log.Mid(Pos + FCString::Strlen(Marker));
		int32 NewlinePos;
		if (Tail.FindChar(TEXT('\n'), NewlinePos))
		{
			Tail.LeftInline(NewlinePos);
		}
		return Tail.TrimStartAndEnd();
	};
	const FString Missing = ExtractMarker(TEXT("ASB_MISS:"));
	const FString ApiInfo = ExtractMarker(TEXT("ASB_API:"));
	MissingPackages = Missing;

	const bool b云端 VLM 网关Key = !FAISceneBuilderKeys::Read云端 VLM 网关Key().IsEmpty();

	FString Report;
	FLinearColor Color;
	if (!bProbeOk)
	{
		Report = FString::Printf(TEXT("✗ 内嵌 Python 探测失败：%s"), *Error.Left(160));
		Color = FLinearColor(1.f, 0.45f, 0.45f);
	}
	else if (!Missing.IsEmpty())
	{
		Report = FString::Printf(
			TEXT("✗ vendor 依赖缺失：%s（点「安装依赖」用引擎自带 Python 补齐）"), *Missing);
		Color = FLinearColor(1.f, 0.45f, 0.45f);
	}
	else
	{
		bEnvironmentOk = true;
		Report = FString::Printf(
			TEXT("✓ 内嵌 Python 就绪  |  API：%s  |  Key %s%s\n")
			TEXT("   逐调用记录见 run 目录 api_calls.jsonl（阶段/模型/缓存命中/耗时）"),
			ApiInfo.IsEmpty() || ApiInfo == TEXT("?") ? TEXT("（配置读取失败）") : *ApiInfo,
			b云端 VLM 网关Key ? TEXT("已配置") : TEXT("缺失"),
			Settings.bOfflineReplay ? TEXT("  |  离线回放开") :
				(b云端 VLM 网关Key ? TEXT("") : TEXT(" → 建议开离线回放")));
		Color = (b云端 VLM 网关Key || Settings.bOfflineReplay)
			? FLinearColor(0.4f, 0.85f, 0.45f) : FLinearColor(1.f, 0.75f, 0.35f);
	}

	if (EnvText.IsValid())
	{
		EnvText->SetText(FText::FromString(Report));
		EnvText->SetColorAndOpacity(FSlateColor(Color));
	}
	if (InstallButton.IsValid())
	{
		InstallButton->SetVisibility(
			MissingPackages.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible);
	}
}

FReply FAISceneBuilderToolkit::OnInstallDependencies()
{
	// 用**引擎自带的** Python 解释器往插件 vendor 目录 pip --target：
	// 不依赖系统 Python，装完即被内嵌解释器（同一版 3.11）识别。需要网络；
	// 离线交付形态下 vendor 目录本来就应随插件带全，这个按钮只是开发期自愈。
	const FString EnginePython = FPaths::ConvertRelativePathToFull(FPaths::Combine(
		FPaths::EngineDir(), TEXT("Binaries/ThirdParty/Python3/Win64/python.exe")));
	const FString Vendor = VendorSitePackagesDir();

	if (!FPaths::FileExists(EnginePython) || Vendor.IsEmpty())
	{
		SetStatusMessage(TEXT("找不到引擎自带 Python 或 vendor 目录。"), true);
		return FReply::Handled();
	}
	IFileManager::Get().MakeDirectory(*Vendor, /*Tree=*/true);

	SetStatusMessage(TEXT("正在向 vendor 目录安装依赖（需要网络，首次几分钟）…"), false);

	int32 ReturnCode = -1;
	FString StdOut, StdErr;
	const FString Args = FString::Printf(
		TEXT("-m pip install \"numpy<2\" opencv-python PyYAML --target \"%s\" ")
		TEXT("--disable-pip-version-check --no-warn-script-location"), *Vendor);
	FPlatformProcess::ExecProcess(*EnginePython, *Args, &ReturnCode, &StdOut, &StdErr);

	if (ReturnCode == 0)
	{
		SetStatusMessage(TEXT("依赖安装完成。"), false);
	}
	else
	{
		UE_LOG(LogAISceneBuilderEnv, Error, TEXT("pip 失败：%s"), *StdErr);
		SetStatusMessage(FString::Printf(TEXT("依赖安装失败：%s"), *StdErr.Left(200)), true);
	}
	CheckEnvironment();
	return FReply::Handled();
}

FReply FAISceneBuilderToolkit::OnAssembleInEditor()
{
	const UAISceneBuilderSettings& Settings = UAISceneBuilderSettings::Get();
	const FString Root = Settings.ResolvePipelineRoot();
	const FString RunDir = Runner->GetRunDir();
	const FString Config = FPaths::Combine(Root, TEXT("configs"), Settings.ConfigProfile);

	FStageStatus Status;
	Status.State = EStageState::Failed;

	if (RunDir.IsEmpty())
	{
		Status.Error = TEXT("还没有产物目录，请先完成前四步。");
		HandleStageChanged(EPipelineStage::Assemble, Status);
		return FReply::Handled();
	}

	// --- 1) 编译装配指令：内嵌 Python 上的确定性纯函数，毫秒级 ---
	const FString ManifestPath = FPaths::Combine(RunDir, TEXT("build_manifest.json"));
	{
		const FString Statement = PathsPrologue(Root) + FString::Printf(
			TEXT("from ue import embedded_runner as _er\n")
			TEXT("print('ASB_COMPILE:', _er.compile_manifest(%s, %s))\n"),
			*PyRawStr(RunDir), *PyRawStr(Config));

		FString Log, Error;
		if (!ExecEmbedded(Statement, Log, Error))
		{
			Status.Error = FString::Printf(TEXT("compile_manifest 失败：%s"), *Error.Left(300));
			HandleStageChanged(EPipelineStage::Assemble, Status);
			return FReply::Handled();
		}
	}
	if (!FPaths::FileExists(ManifestPath))
	{
		Status.Error = TEXT("build_manifest.json 未生成。");
		HandleStageChanged(EPipelineStage::Assemble, Status);
		return FReply::Handled();
	}

	// --- 2) 装配：GameThread 上进程内执行。参数经 run 目录里的 assemble_args.json
	// 传递（ASB_ARGS_JSON 环境变量指路），旧的 %TEMP% 拷贝旁路已删除——空格路径、
	// 并发覆盖、陈旧参数三个坑一起消失。关卡路径读配置，不再硬编码。 ---
	const FString SceneManifest = FPaths::Combine(RunDir, TEXT("scene_manifest.json"));
	IFileManager::Get().Delete(*SceneManifest, false, true, true);

	SetStatusMessage(TEXT("正在装配（编辑器会短暂无响应，约 30–60 秒）…"), false);

	FString PythonError;
	{
		const FString Statement = PathsPrologue(Root) + FString::Printf(
			TEXT("from ue import embedded_runner as _er\n")
			TEXT("print('ASB_ASSEMBLE:', _er.assemble(%s, %s))\n"),
			*PyRawStr(RunDir), *PyRawStr(Config));

		FString Log;
		ExecEmbedded(Statement, Log, PythonError);
	}

	// --- 3) 以装配回执为最终判据 ---
	FString ManifestRaw;
	if (FFileHelper::LoadFileToString(ManifestRaw, *SceneManifest))
	{
		TSharedPtr<FJsonObject> Json;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ManifestRaw);
		if (FJsonSerializer::Deserialize(Reader, Json) && Json.IsValid())
		{
			FString State;
			Json->TryGetStringField(TEXT("status"), State);
			const TArray<TSharedPtr<FJsonValue>>* Actors = nullptr;
			const int32 ActorCount = Json->TryGetArrayField(TEXT("actors"), Actors)
				? Actors->Num() : 0;

			if (State == TEXT("ok") || State == TEXT("partial"))
			{
				Status.State = EStageState::Succeeded;
				Status.Progress = 1.f;
				Status.Message = FString::Printf(TEXT("装配完成：%d 个 actor"), ActorCount);
				Status.Error.Reset();
				HandleStageChanged(EPipelineStage::Assemble, Status);
				return FReply::Handled();
			}
			Status.Error = FString::Printf(TEXT("装配报错，回执 status=%s"), *State);
		}
	}

	if (Status.Error.IsEmpty())
	{
		Status.Error = PythonError.IsEmpty()
			? TEXT("未写出装配回执，详见 Output Log。") : PythonError.Left(300);
	}
	HandleStageChanged(EPipelineStage::Assemble, Status);
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
