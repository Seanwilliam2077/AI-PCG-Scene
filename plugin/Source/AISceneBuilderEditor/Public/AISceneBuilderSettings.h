// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Engine/DeveloperSettings.h"
#include "AISceneBuilderSettings.generated.h"

/**
 * 插件设置。存的都是**机器相关**的路径，所以走 EditorPerProjectUserSettings
 * （落在 Saved/Config/，不会被提交进仓库）。
 *
 * API Key 刻意不在这里 —— 见 FAISceneBuilderKeys::Read云端 VLM 网关Key() 的三级回退。
 */
UCLASS(config = EditorPerProjectUserSettings, meta = (DisplayName = "AI Scene Builder"))
class AISCENEBUILDEREDITOR_API UAISceneBuilderSettings : public UDeveloperSettings
{
	GENERATED_BODY()

public:
	UAISceneBuilderSettings();

	virtual FName GetCategoryName() const override { return TEXT("Plugins"); }

	/** 系统 Python 解释器。s01–s04 需要 numpy/cv2/yaml/requests，UE 内置 Python 没有这些。 */
	UPROPERTY(config, EditAnywhere, Category = "Environment",
		meta = (DisplayName = "Python 解释器", FilePathFilter = "exe"))
	FFilePath PythonExecutable;

	/** 参考图。管线是通用的，换图即可重跑。 */
	UPROPERTY(config, EditAnywhere, Category = "Input",
		meta = (DisplayName = "参考图", FilePathFilter = "Image files (*.jpg;*.png)|*.jpg;*.png"))
	FFilePath ReferenceImage;

	/** 管线根目录。留空则用插件自带的 Python/ （推荐）。 */
	UPROPERTY(config, EditAnywhere, Category = "Environment",
		meta = (DisplayName = "管线目录（留空=插件自带）"))
	FDirectoryPath PipelineRootOverride;

	/** 配置档：pipeline.yaml（完整）或 smoke.yaml（冒烟，3 hero、无 turnaround）。 */
	UPROPERTY(config, EditAnywhere, Category = "Run",
		meta = (DisplayName = "配置档", GetOptions = "GetConfigProfileOptions"))
	FString ConfigProfile;

	/** s03 单场景可能跑 20–40 分钟，超时按阶段分别给。 */
	UPROPERTY(config, EditAnywhere, Category = "Run",
		meta = (DisplayName = "生成阶段超时（分钟）", ClampMin = "5", ClampMax = "180"))
	int32 GenerationTimeoutMinutes;

	/**
	 * 离线回放：AI 调用只允许命中随工程附带的 cache/vlm_gateway，缓存 miss 显式报错
	 * 而不是发请求。评审机没有云端 云端网关访问权限/API Key 时开着它整条复跑。
	 */
	UPROPERTY(config, EditAnywhere, Category = "Run",
		meta = (DisplayName = "离线回放（只用缓存，不发真实 API 请求）"))
	bool bOfflineReplay = false;

	UFUNCTION()
	TArray<FString> GetConfigProfileOptions() const;

	/** 解析出最终生效的管线根目录（Override 为空则回退到插件内 Python/）。 */
	FString ResolvePipelineRoot() const;

	/** 解析 Python 解释器：设置 → PATH → 常见安装位置。找不到返回空串。 */
	FString ResolvePythonExecutable() const;

	static const UAISceneBuilderSettings& Get();
	static UAISceneBuilderSettings& GetMutable();
};

/**
 * API Key 读取。三级回退，与本机 LegacyProject 的 GatewayImageClient 同源：
 *   1. 环境变量（每台机器/CI 各自覆盖，永不入库）
 *   2. GEditorIni 的 [AISceneBuilder.Gateway] ApiKey（DefaultEditor.ini 合并结果）
 *   3. 直读 Config/DefaultEditor.ini —— 新克隆的仓库可能残留旧的生成态 Editor.ini，
 *      这一级保证团队默认值仍能被读到
 */
struct AISCENEBUILDEREDITOR_API FAISceneBuilderKeys
{
	static const TCHAR* GatewayEnvVar;
	static const TCHAR* Gen3DEnvVar;

	static FString Read云端 VLM 网关Key();
	static FString Read云端图生 3DKey();
	static bool Has云端 VLM 网关Key() { return !Read云端 VLM 网关Key().IsEmpty(); }
};
