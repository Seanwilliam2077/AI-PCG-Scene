// Copyright (c) TA Pipeline. All Rights Reserved.

#include "AISceneBuilderEdMode.h"

#include "AISceneBuilderToolkit.h"
#include "Editor.h"
#include "Styling/AppStyle.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

const FEditorModeID UAISceneBuilderEdMode::EM_AISceneBuilderEdModeId = TEXT("EM_AISceneBuilder");

UAISceneBuilderEdMode::UAISceneBuilderEdMode()
{
	Info = FEditorModeInfo(
		EM_AISceneBuilderEdModeId,
		LOCTEXT("ModeName", "AI Scene Builder"),
		FSlateIcon(FAppStyle::GetAppStyleSetName(), "LevelEditor.ViewOptions"),
		/*bVisible=*/true,
		/*PriorityOrder=*/4000);
}

void UAISceneBuilderEdMode::Enter()
{
	UBaseLegacyWidgetEdMode::Enter();
}

void UAISceneBuilderEdMode::Exit()
{
	UBaseLegacyWidgetEdMode::Exit();
}

void UAISceneBuilderEdMode::CreateToolkit()
{
	Toolkit = MakeShared<FAISceneBuilderToolkit>();
}

UWorld* UAISceneBuilderEdMode::GetWorld() const
{
	// UEdMode::GetWorld() 读的是只在 Enter() 里赋值的 ToolsContext；本模式没有
	// ToolsContext，所以直接回退到编辑器世界，避免拿到 nullptr 且静默失效。
	if (GEditor)
	{
		return GEditor->GetEditorWorldContext().World();
	}
	return nullptr;
}

#undef LOCTEXT_NAMESPACE
