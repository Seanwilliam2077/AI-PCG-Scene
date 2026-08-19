// Copyright (c) TA Pipeline. All Rights Reserved.

#include "AISceneBuilderEdMode.h"
#include "PipelineRunner.h"

#include "EditorModeManager.h"
#include "Editor.h"
#include "Modules/ModuleManager.h"
#include "ToolMenus.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

class FAISceneBuilderEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		// UEdMode 子类由 UAssetEditorSubsystem 自动扫描注册，这里只加菜单入口。
		UToolMenus::RegisterStartupCallback(
			FSimpleMulticastDelegate::FDelegate::CreateStatic(&FAISceneBuilderEditorModule::RegisterMenus));
	}

	virtual void ShutdownModule() override
	{
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);

		// 执行器是模块级单例（寿命长过面板），这里负责杀掉还在跑的子进程。
		FPipelineRunner::Teardown();

		// 引擎退出过程中各子系统正在关闭，此时反激活模式会触发
		// "Object is not packaged" 之类的 fatal。只在正常卸载时才收。
		if (GEditor && !IsEngineExitRequested())
		{
			if (GLevelEditorModeTools().IsModeActive(UAISceneBuilderEdMode::EM_AISceneBuilderEdModeId))
			{
				GLevelEditorModeTools().DeactivateMode(UAISceneBuilderEdMode::EM_AISceneBuilderEdModeId);
			}
		}
	}

private:
	static void RegisterMenus()
	{
		FToolMenuOwnerScoped OwnerScoped(TEXT("AISceneBuilder"));
		UToolMenu* Menu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
		if (!Menu)
		{
			return;
		}
		FToolMenuSection& Section = Menu->FindOrAddSection(
			TEXT("AISceneBuilder"), LOCTEXT("SectionLabel", "AI Scene Builder"));
		Section.AddMenuEntry(
			TEXT("OpenAISceneBuilder"),
			LOCTEXT("OpenLabel", "AI Scene Builder"),
			LOCTEXT("OpenTooltip", "从一张参考图生成 UE5 场景（五步向导）"),
			FSlateIcon(),
			FUIAction(FExecuteAction::CreateLambda([]()
			{
				GLevelEditorModeTools().ActivateMode(UAISceneBuilderEdMode::EM_AISceneBuilderEdModeId);
			})));
	}
};

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FAISceneBuilderEditorModule, AISceneBuilderEditor)
