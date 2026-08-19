// Copyright (c) TA Pipeline. All Rights Reserved.

#include "AISceneBuilderToolkit.h"

#include "AISceneBuilderSettings.h"
#include "DesktopPlatformModule.h"
#include "Framework/Application/SlateApplication.h"
#include "IDesktopPlatform.h"
#include "IPythonScriptPlugin.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "PythonScriptTypes.h"
#include "HAL/FileManager.h"
#include "Styling/AppStyle.h"
#include "Styling/SlateBrush.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Notifications/SProgressBar.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#include "PreviewImage.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

DEFINE_LOG_CATEGORY_STATIC(LogAISceneBuilderUI, Log, All);

namespace
{
	const FLinearColor ColorIdle(0.55f, 0.55f, 0.55f);
	const FLinearColor ColorRunning(0.35f, 0.75f, 1.00f);
	const FLinearColor ColorOk(0.40f, 0.85f, 0.45f);
	const FLinearColor ColorFail(1.00f, 0.45f, 0.45f);

	FText StateLabel(EStageState State)
	{
		switch (State)
		{
		case EStageState::Running:   return LOCTEXT("StRunning", "运行中");
		case EStageState::Succeeded: return LOCTEXT("StOk", "完成");
		case EStageState::Failed:    return LOCTEXT("StFail", "失败");
		case EStageState::Cancelled: return LOCTEXT("StCancel", "已取消");
		default:                     return LOCTEXT("StIdle", "待运行");
		}
	}

	FLinearColor StateColor(EStageState State)
	{
		switch (State)
		{
		case EStageState::Running:   return ColorRunning;
		case EStageState::Succeeded: return ColorOk;
		case EStageState::Failed:
		case EStageState::Cancelled: return ColorFail;
		default:                     return ColorIdle;
		}
	}
}

FAISceneBuilderToolkit::FAISceneBuilderToolkit()
{
	// 用模块级单例而非自己 new 一个：面板只是观察者。美术切走本模式后
	// 面板会析构，但在跑的阶段必须继续跑（s03 要 20–40 分钟），
	// 进度改由右下角的非模态通知呈现，回到本模式时状态自动接上。
	Runner = FPipelineRunner::Get();
	Runner->OnStageChanged.AddRaw(this, &FAISceneBuilderToolkit::HandleStageChanged);
}

FAISceneBuilderToolkit::~FAISceneBuilderToolkit()
{
	if (Runner.IsValid())
	{
		Runner->OnStageChanged.RemoveAll(this);   // 只退订，**不** Cancel
	}
	// 预览纹理是 AddToRoot 保活的，这里必须还回去，否则泄漏
	for (FStepCardWidgets& Card : Cards)
	{
		AISceneBuilder::ReleasePreviewTexture(Card.PreviewTexture);
	}
}

FText FAISceneBuilderToolkit::GetBaseToolkitName() const
{
	return LOCTEXT("ToolkitName", "AI Scene Builder");
}

void FAISceneBuilderToolkit::Init(const TSharedPtr<IToolkitHost>& InitToolkitHost,
								  TWeakObjectPtr<UEdMode> InOwningMode)
{
	ToolkitWidget = BuildPanel();               // 先建面板，再走基类 Init
	FModeToolkit::Init(InitToolkitHost, InOwningMode);

	CheckEnvironment();
	Runner->RefreshFromDisk();                  // 重开编辑器后从产物恢复各步状态
	for (uint8 i = 0; i < (uint8)EPipelineStage::Count; ++i)
	{
		RefreshPreview((EPipelineStage)i);
	}

	// 切走本模式再切回来时，可能还有阶段在跑——RefreshFromDisk 刻意不覆盖它的状态，
	// 这里把实时状态补进卡片，否则那张卡会显示成「待运行」。
	if (Runner->IsBusy())
	{
		const EPipelineStage Busy = Runner->GetRunningStage();
		HandleStageChanged(Busy, Runner->GetStatus(Busy));
	}
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildPanel()
{
	TSharedRef<SVerticalBox> Steps = SNew(SVerticalBox);
	for (uint8 i = 0; i < (uint8)EPipelineStage::Count; ++i)
	{
		Steps->AddSlot().AutoHeight().Padding(0, 0, 0, 6)
		[
			BuildStepCard((EPipelineStage)i)
		];
	}

	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()[BuildHeader()]
		+ SVerticalBox::Slot().AutoHeight()[BuildEnvironmentBar()]
		+ SVerticalBox::Slot().AutoHeight().Padding(0, 4)[SNew(SSeparator)]
		+ SVerticalBox::Slot().FillHeight(1.f)
		[
			SNew(SScrollBox) + SScrollBox::Slot().Padding(6, 2)[Steps]
		]
		+ SVerticalBox::Slot().AutoHeight()[BuildStatusBar()];
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildHeader()
{
	return SNew(SBorder)
		.BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder"))
		.Padding(6.f)
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock)
				.Text(LOCTEXT("RefLabel", "参考图"))
				.Font(FAppStyle::GetFontStyle("PropertyWindow.BoldFont"))
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 4, 0, 0)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
				[
					SAssignNew(RefPathText, STextBlock)
					.Text(LOCTEXT("NoRef", "（未选择）"))
					.AutoWrapText(true)
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
				[
					SNew(SButton)
					.Text(LOCTEXT("Browse", "浏览…"))
					.OnClicked(this, &FAISceneBuilderToolkit::OnPickReferenceImage)
				]
			]
		];
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildEnvironmentBar()
{
	return SNew(SBorder)
		.BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder"))
		.Padding(6.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
			[
				SAssignNew(EnvText, STextBlock)
				.Text(LOCTEXT("EnvChecking", "环境检测中…"))
				.AutoWrapText(true)
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
			[
				SAssignNew(InstallButton, SButton)
				.Text(LOCTEXT("Install", "一键安装依赖"))
				.Visibility(EVisibility::Collapsed)
				.OnClicked(this, &FAISceneBuilderToolkit::OnInstallDependencies)
			]
		];
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildStepCard(EPipelineStage Stage)
{
	const uint8 Index = (uint8)Stage;
	FStepCardWidgets& Card = Cards[Index];
	Card.PreviewBrush = MakeShared<FSlateBrush>();

	// s05 走编辑器内 Python，与前四步的子进程路径不同
	const bool bIsAssemble = (Stage == EPipelineStage::Assemble);

	return SNew(SBorder)
		.BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder"))
		.Padding(8.f)
		[
			SNew(SVerticalBox)
			// 标题行：名称 + 状态
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
				[
					SNew(STextBlock)
					.Text(FPipelineRunner::StageDisplayName(Stage))
					.Font(FAppStyle::GetFontStyle("PropertyWindow.BoldFont"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SAssignNew(Card.StateText, STextBlock)
					.Text(StateLabel(EStageState::Idle))
					.ColorAndOpacity(FSlateColor(ColorIdle))
				]
			]
			// 进度条
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 4)
			[
				SNew(SBox).HeightOverride(6.f)
				[
					SAssignNew(Card.ProgressBar, SProgressBar).Percent(0.f)
				]
			]
			// 消息行
			+ SVerticalBox::Slot().AutoHeight()
			[
				SAssignNew(Card.MessageText, STextBlock)
				.Text(FText::GetEmpty())
				.AutoWrapText(true)
				.ColorAndOpacity(FSlateColor(ColorIdle))
			]
			// 操作行
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 0)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SNew(SButton)
					.Text(bIsAssemble ? LOCTEXT("Assemble", "装配到当前关卡")
									  : LOCTEXT("RunStep", "运行此步"))
					.IsEnabled_Lambda([this, Stage]() { return IsStageRunnable(Stage); })
					.OnClicked(bIsAssemble
						? FOnClicked::CreateSP(this, &FAISceneBuilderToolkit::OnAssembleInEditor)
						: FOnClicked::CreateSP(this, &FAISceneBuilderToolkit::OnRunStage, Stage))
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
				[
					SNew(SButton)
					.Text(LOCTEXT("CancelStep", "取消"))
					.IsEnabled_Lambda([this, Stage]()
					{
						return Runner->IsBusy() && Runner->GetRunningStage() == Stage;
					})
					.OnClicked(this, &FAISceneBuilderToolkit::OnCancel)
				]
			]
			// 人工干预
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 4, 0, 0)
			[
				BuildInterventionRow(Stage)
			]
			// 预览图
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 0)
			[
				SAssignNew(Card.PreviewBox, SBox)
				.Visibility(EVisibility::Collapsed)
				.MaxDesiredHeight(180.f)
				[
					SNew(SImage).Image(Card.PreviewBrush.Get())
				]
			]
		];
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildInterventionRow(EPipelineStage Stage)
{
	TSharedRef<SHorizontalBox> Row = SNew(SHorizontalBox);

	auto AddButton = [&Row, this](const FText& Label, const FText& Tip, FString File, bool bManualCalib = false)
	{
		Row->AddSlot().AutoWidth().Padding(0, 0, 4, 0)
		[
			SNew(SButton)
			.Text(Label)
			.ToolTipText(Tip)
			.OnClicked(bManualCalib
				? FOnClicked::CreateSP(this, &FAISceneBuilderToolkit::OnManualCalibration)
				: FOnClicked::CreateSP(this, &FAISceneBuilderToolkit::OnOpenFile, File))
		];
	};

	switch (Stage)
	{
	case EPipelineStage::Perceive:
		AddButton(LOCTEXT("OpenCalibDbg", "查看标定图"),
				  LOCTEXT("OpenCalibDbgTip", "线段按族着色 + 地平线，用来判断标定是否可信"),
				  TEXT("calib_debug.png"));
		AddButton(LOCTEXT("ManualCalib", "手动标定"),
				  LOCTEXT("ManualCalibTip", "写 calib_manual.json 覆盖自动标定（唯一强人工介入点）"),
				  FString(), /*bManualCalib=*/true);
		break;
	case EPipelineStage::Layout:
		AddButton(LOCTEXT("OpenPlan", "查看布局图"),
				  LOCTEXT("OpenPlanTip", "俯视平面 + 立面，装配前两秒自检"),
				  TEXT("layout_preview.png"));
		AddButton(LOCTEXT("OpenLayoutJson", "编辑 scene_layout.json"),
				  LOCTEXT("OpenLayoutJsonTip", "改完直接重跑下一步即可生效"),
				  TEXT("scene_layout.json"));
		break;
	case EPipelineStage::Gen2D:
		AddButton(LOCTEXT("OpenGen2d", "打开素材目录"),
				  LOCTEXT("OpenGen2dTip", "hero / materials / decals / screens"),
				  TEXT("gen2d"));
		AddButton(LOCTEXT("OpenGen2dManifest", "查看清单"),
				  LOCTEXT("OpenGen2dManifestTip", "含每套材质的接缝分数与跳过原因"),
				  TEXT("gen2d_manifest.json"));
		break;
	case EPipelineStage::Gen3D:
		AddButton(LOCTEXT("OpenRegistry", "查看资产清单"),
				  LOCTEXT("OpenRegistryTip", "provider / 尺寸 / 降级标记"),
				  TEXT("asset_registry.json"));
		AddButton(LOCTEXT("OpenAssets", "打开资产目录"), FText::GetEmpty(),
				  TEXT("assets_clean"));
		break;
	case EPipelineStage::Assemble:
		AddButton(LOCTEXT("OpenSceneManifest", "查看装配回执"),
				  LOCTEXT("OpenSceneManifestTip", "actors / errors"),
				  TEXT("scene_manifest.json"));
		AddButton(LOCTEXT("OpenShot", "查看对齐机位截图"), FText::GetEmpty(),
				  TEXT("ue_shot.png"));
		break;
	default:
		break;
	}
	return Row;
}

TSharedRef<SWidget> FAISceneBuilderToolkit::BuildStatusBar()
{
	// 纯色 Brush 作 BorderImage 有时不铺底；WhiteBrush + BorderBackgroundColor 最稳。
	static const FLinearColor BarColor(FColor(0x0B, 0x1C, 0x33));
	return SNew(SBorder)
		.BorderImage(FAppStyle::GetBrush("WhiteBrush"))
		.BorderBackgroundColor(BarColor)
		.Padding(FMargin(8.f, 6.f))
		[
			SAssignNew(StatusText, STextBlock)
			.Text(LOCTEXT("Ready", "就绪"))
			.AutoWrapText(true)
			.ColorAndOpacity(FSlateColor(FLinearColor::White))
		];
}

// ---------------------------------------------------------------- 状态刷新

void FAISceneBuilderToolkit::HandleStageChanged(EPipelineStage Stage, const FStageStatus& Status)
{
	const uint8 Index = (uint8)Stage;
	if (Index >= (uint8)EPipelineStage::Count)
	{
		return;
	}
	FStepCardWidgets& Card = Cards[Index];

	if (Card.StateText.IsValid())
	{
		Card.StateText->SetText(StateLabel(Status.State));
		Card.StateText->SetColorAndOpacity(FSlateColor(StateColor(Status.State)));
	}
	if (Card.ProgressBar.IsValid())
	{
		Card.ProgressBar->SetPercent(Status.Progress);
	}
	if (Card.MessageText.IsValid())
	{
		FString Text = Status.Error.IsEmpty() ? Status.Message : Status.Error;
		if (Status.ElapsedSeconds > 1.0)
		{
			Text += FString::Printf(TEXT("  （%.0fs）"), Status.ElapsedSeconds);
		}
		Card.MessageText->SetText(FText::FromString(Text));
		Card.MessageText->SetColorAndOpacity(FSlateColor(
			Status.Error.IsEmpty() ? ColorIdle : ColorFail));
	}

	if (Status.State == EStageState::Succeeded)
	{
		RefreshPreview(Stage);
		SetStatusMessage(FString::Printf(TEXT("%s 完成"),
			*FPipelineRunner::StageDisplayName(Stage).ToString()), false);
	}
	else if (Status.State == EStageState::Failed)
	{
		SetStatusMessage(Status.Error, true);
	}
}

void FAISceneBuilderToolkit::SetStatusMessage(const FString& Message, bool bIsError)
{
	if (!StatusText.IsValid())
	{
		return;
	}
	StatusText->SetText(FText::FromString(Message));
	StatusText->SetColorAndOpacity(FSlateColor(bIsError ? ColorFail : FLinearColor::White));
}

const TCHAR* FAISceneBuilderToolkit::PreviewFileFor(EPipelineStage Stage)
{
	switch (Stage)
	{
	case EPipelineStage::Perceive: return TEXT("calib_debug.png");
	case EPipelineStage::Layout:   return TEXT("layout_preview.png");
	case EPipelineStage::Assemble: return TEXT("ue_shot.png");
	default:                       return nullptr;      // 这两步没有单张代表图
	}
}

void FAISceneBuilderToolkit::RefreshPreview(EPipelineStage Stage)
{
	const TCHAR* FileName = PreviewFileFor(Stage);
	if (!FileName)
	{
		return;
	}
	const FString RunDir = Runner->GetRunDir();
	if (RunDir.IsEmpty())
	{
		return;
	}
	const FString Path = FPaths::Combine(RunDir, FileName);

	FStepCardWidgets& Card = Cards[(uint8)Stage];
	AISceneBuilder::ReleasePreviewTexture(Card.PreviewTexture);

	UTexture2D* Texture = AISceneBuilder::LoadTextureFromFile(Path);
	if (!Texture || !Card.PreviewBrush.IsValid())
	{
		if (Card.PreviewBox.IsValid())
		{
			Card.PreviewBox->SetVisibility(EVisibility::Collapsed);
		}
		return;
	}
	Card.PreviewTexture = Texture;
	Card.PreviewBrush->SetResourceObject(Texture);
	Card.PreviewBrush->ImageSize = FVector2D(Texture->GetSizeX(), Texture->GetSizeY());
	if (Card.PreviewBox.IsValid())
	{
		Card.PreviewBox->SetVisibility(EVisibility::Visible);
	}
}

bool FAISceneBuilderToolkit::IsStageRunnable(EPipelineStage Stage) const
{
	if (Runner->IsBusy() || !bEnvironmentOk)
	{
		return false;
	}
	if (UAISceneBuilderSettings::Get().ReferenceImage.FilePath.IsEmpty())
	{
		return false;
	}
	// 分步向导的门控：上一步必须已成功（或本身就是第一步）
	if (Stage == EPipelineStage::Perceive)
	{
		return true;
	}
	const EPipelineStage Prev = (EPipelineStage)((uint8)Stage - 1);
	return Runner->GetStatus(Prev).State == EStageState::Succeeded;
}

// ---------------------------------------------------------------- 动作

FReply FAISceneBuilderToolkit::OnRunStage(EPipelineStage Stage)
{
	SetStatusMessage(FString::Printf(TEXT("正在运行 %s…"),
		*FPipelineRunner::StageDisplayName(Stage).ToString()), false);
	Runner->RunStage(Stage);
	return FReply::Handled();
}

FReply FAISceneBuilderToolkit::OnCancel()
{
	Runner->Cancel();
	SetStatusMessage(TEXT("已取消"), false);
	return FReply::Handled();
}

FReply FAISceneBuilderToolkit::OnPickReferenceImage()
{
	IDesktopPlatform* Platform = FDesktopPlatformModule::Get();
	if (!Platform)
	{
		return FReply::Handled();
	}
	const void* ParentHandle = FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr);
	TArray<FString> Files;
	const bool bPicked = Platform->OpenFileDialog(
		ParentHandle, TEXT("选择参考图"), FPaths::ProjectDir(), TEXT(""),
		TEXT("Images (*.jpg;*.jpeg;*.png)|*.jpg;*.jpeg;*.png"), EFileDialogFlags::None, Files);

	if (bPicked && Files.Num() > 0)
	{
		UAISceneBuilderSettings& Settings = UAISceneBuilderSettings::GetMutable();
		Settings.ReferenceImage.FilePath = FPaths::ConvertRelativePathToFull(Files[0]);
		Settings.SaveConfig();
		if (RefPathText.IsValid())
		{
			RefPathText->SetText(FText::FromString(Settings.ReferenceImage.FilePath));
		}
		Runner->RefreshFromDisk();          // 换图 = 换 run 目录，状态要重算
		for (uint8 i = 0; i < (uint8)EPipelineStage::Count; ++i)
		{
			RefreshPreview((EPipelineStage)i);
		}
		SetStatusMessage(TEXT("参考图已更新"), false);
	}
	return FReply::Handled();
}

FReply FAISceneBuilderToolkit::OnOpenFile(FString RelativePath)
{
	const FString RunDir = Runner->GetRunDir();
	if (RunDir.IsEmpty())
	{
		SetStatusMessage(TEXT("还没有产物目录，先跑一步。"), true);
		return FReply::Handled();
	}
	const FString Full = FPaths::Combine(RunDir, RelativePath);
	if (!FPaths::FileExists(Full) && !FPaths::DirectoryExists(Full))
	{
		SetStatusMessage(FString::Printf(TEXT("文件不存在：%s"), *Full), true);
		return FReply::Handled();
	}
	FPlatformProcess::LaunchFileInDefaultExternalApplication(*Full);
	return FReply::Handled();
}

FReply FAISceneBuilderToolkit::OnManualCalibration()
{
	// 一次性写出模板；用户改完数值后重跑 s02 即可生效（calib_manual.json
	// 存在时会**无条件覆盖**自动标定，这是管线里唯一的强人工介入点）。
	const FString RunDir = Runner->GetRunDir();
	if (RunDir.IsEmpty())
	{
		SetStatusMessage(TEXT("请先跑一次「场景理解」，再手动标定。"), true);
		return FReply::Handled();
	}
	const FString Path = FPaths::Combine(RunDir, TEXT("calib_manual.json"));
	if (!FPaths::FileExists(Path))
	{
		const FString Template = TEXT(
			"{\n"
			"  \"hfov_deg\": 74.0,\n"
			"  \"pitch_deg\": 3.0,\n"
			"  \"roll_deg\": 0.0,\n"
			"  \"cam_height_m\": 1.2,\n"
			"  \"_note\": \"改完保存，然后重跑「布局求解」。该文件存在时无条件覆盖自动标定。\"\n"
			"}\n");
		FFileHelper::SaveStringToFile(Template, *Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}
	FPlatformProcess::LaunchFileInDefaultExternalApplication(*Path);
	SetStatusMessage(TEXT("已打开 calib_manual.json，改完重跑「布局求解」。"), false);
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
