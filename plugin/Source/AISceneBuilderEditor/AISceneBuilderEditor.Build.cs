// Copyright (c) TA Pipeline. All Rights Reserved.

using UnrealBuildTool;

public class AISceneBuilderEditor : ModuleRules
{
	public AISceneBuilderEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"UnrealEd",         // UEdMode / FEditorModeTools
			"EditorFramework",  // FEditorModeInfo / UBaseLegacyWidgetEdMode
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"Slate",
			"SlateCore",
			"InputCore",
			"ToolMenus",          // 菜单扩展
			"Projects",           // IPluginManager：定位插件内的 Python/
			"DeveloperSettings",  // UAISceneBuilderSettings
			"PythonScriptPlugin", // s05 在编辑器内执行 build_scene.py
			"Json",
			"JsonUtilities",
			"ImageWrapper",       // 预览图解码
			"RenderCore",
			"RHI",
			"DesktopPlatform",    // 文件选择对话框
			"LevelEditor",
		});

		// 注意：UE5.5 用 FAppStyle，不再需要 EditorStyle。
		// 也不依赖 InteractiveToolsFramework —— 本插件是按钮驱动的批处理面板，
		// 不是视口交互工具，不走 UInteractiveTool / ToolsContext 那一套。
	}
}
