// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "PipelineRunner.h"
#include "Toolkits/BaseToolkit.h"

class STextBlock;
class SProgressBar;
class SButton;
class SBox;
class UTexture2D;
struct FSlateBrush;

/** 一张步骤卡片的运行时句柄（面板持有，状态变化时就地刷新）。 */
struct FStepCardWidgets
{
	TSharedPtr<STextBlock> StateText;
	TSharedPtr<STextBlock> MessageText;
	TSharedPtr<SProgressBar> ProgressBar;
	TSharedPtr<SBox> PreviewBox;
	TSharedPtr<FSlateBrush> PreviewBrush;
	TWeakObjectPtr<UTexture2D> PreviewTexture;
};

/**
 * 五步向导面板。照 FCityGenEdModeToolkit 的 Init/GetInlineContent/BuildPanel 三件套，
 * 不走 GetToolPaletteNames()/BuildToolPalette()——那套是给 UInteractiveTool 用的。
 */
class AISCENEBUILDEREDITOR_API FAISceneBuilderToolkit : public FModeToolkit
{
public:
	FAISceneBuilderToolkit();
	virtual ~FAISceneBuilderToolkit() override;

	// FModeToolkit
	virtual void Init(const TSharedPtr<IToolkitHost>& InitToolkitHost,
					  TWeakObjectPtr<UEdMode> InOwningMode) override;
	virtual FName GetToolkitFName() const override { return FName("AISceneBuilder"); }
	virtual FText GetBaseToolkitName() const override;
	virtual TSharedPtr<SWidget> GetInlineContent() const override { return ToolkitWidget; }

private:
	TSharedRef<SWidget> BuildPanel();
	TSharedRef<SWidget> BuildHeader();
	TSharedRef<SWidget> BuildEnvironmentBar();
	TSharedRef<SWidget> BuildStepCard(EPipelineStage Stage);
	TSharedRef<SWidget> BuildStatusBar();

	/** 每步各自的人工干预按钮行。 */
	TSharedRef<SWidget> BuildInterventionRow(EPipelineStage Stage);

	void HandleStageChanged(EPipelineStage Stage, const FStageStatus& Status);
	void SetStatusMessage(const FString& Message, bool bIsError);

	// --- 动作 ---
	FReply OnRunStage(EPipelineStage Stage);
	FReply OnCancel();
	FReply OnPickReferenceImage();
	FReply OnInstallDependencies();
	FReply OnOpenFile(FString RelativePath);      // 用系统默认程序打开 run 目录下的文件
	FReply OnAssembleInEditor();                  // s05：编辑器内 Python
	FReply OnManualCalibration();

	/** 前置条件：上一步是否已成功（分步向导的门控）。 */
	bool IsStageRunnable(EPipelineStage Stage) const;

	/** 刷新某步的预览图（从 run 目录读约定文件名）。 */
	void RefreshPreview(EPipelineStage Stage);
	static const TCHAR* PreviewFileFor(EPipelineStage Stage);

	/** 探测系统 Python 是否装齐 numpy/cv2/yaml/requests。 */
	void CheckEnvironment();

	TSharedPtr<SWidget> ToolkitWidget;
	TSharedPtr<STextBlock> StatusText;
	TSharedPtr<STextBlock> RefPathText;
	TSharedPtr<STextBlock> EnvText;
	TSharedPtr<SButton> InstallButton;

	TSharedPtr<FPipelineRunner> Runner;
	FStepCardWidgets Cards[(uint8)EPipelineStage::Count];

	bool bEnvironmentOk = false;
	FString MissingPackages;
};
