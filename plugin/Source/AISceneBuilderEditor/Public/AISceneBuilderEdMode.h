// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Tools/LegacyEdModeWidgetHelpers.h"
#include "AISceneBuilderEdMode.generated.h"

/**
 * "AI Scene Builder" 编辑器模式。
 *
 * 本模式不做任何视口交互（不刷、不画、不选），所以刻意**不**接
 * UEdModeInteractiveToolsContext / UInteractiveTool 那一套——那个框架假定每个
 * "工具"都是 UInteractiveTool，有自己的 Setup/Shutdown/输入路由。我们要的是
 * 一块按钮驱动的批处理面板，所以只用 FModeToolkit::GetInlineContent() 挂自绘 Slate。
 *
 * UE5 的 UEdMode 子类由 UAssetEditorSubsystem 自动扫描注册，无需手动 RegisterMode。
 */
UCLASS()
class AISCENEBUILDEREDITOR_API UAISceneBuilderEdMode : public UBaseLegacyWidgetEdMode
{
	GENERATED_BODY()

public:
	static const FEditorModeID EM_AISceneBuilderEdModeId;

	UAISceneBuilderEdMode();

	virtual void Enter() override;
	virtual void Exit() override;
	virtual void CreateToolkit() override;
	virtual UWorld* GetWorld() const override;

	/** 面板不参与视口变换，关掉变换 gizmo 免得误导用户。 */
	virtual bool UsesTransformWidget() const override { return false; }
};
