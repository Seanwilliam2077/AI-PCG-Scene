// Copyright (c) TA Pipeline. All Rights Reserved.

#include "AISceneBuilderSettings.h"

#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Paths.h"

namespace
{
	const TCHAR* const KeySection = TEXT("AISceneBuilder.Gateway");
	const TCHAR* const GatewayKeyName = TEXT("ApiKey");
	const TCHAR* const Gen3DKeyName = TEXT("Gen3DApiKey");

	/** 依次读三级来源，命中即返回。 */
	FString ReadKeyWithFallback(const TCHAR* EnvVar, const TCHAR* IniKeyName)
	{
		FString Key = FPlatformMisc::GetEnvironmentVariable(EnvVar);
		Key.TrimStartAndEndInline();
		if (!Key.IsEmpty())
		{
			return Key;
		}

		if (GConfig)
		{
			GConfig->GetString(KeySection, IniKeyName, Key, GEditorIni);
			Key.TrimStartAndEndInline();

			if (Key.IsEmpty())
			{
				const FString ProjectDefaultEditorIni =
					FPaths::Combine(FPaths::ProjectConfigDir(), TEXT("DefaultEditor.ini"));
				GConfig->GetString(KeySection, IniKeyName, Key, ProjectDefaultEditorIni);
				Key.TrimStartAndEndInline();
			}
		}
		return Key;
	}
}

const TCHAR* FAISceneBuilderKeys::GatewayEnvVar = TEXT("VLM_API_KEY");
const TCHAR* FAISceneBuilderKeys::Gen3DEnvVar = TEXT("GEN3D_API_KEY");

FString FAISceneBuilderKeys::ReadGatewayKey()
{
	return ReadKeyWithFallback(GatewayEnvVar, GatewayKeyName);
}

FString FAISceneBuilderKeys::ReadGen3DKey()
{
	return ReadKeyWithFallback(Gen3DEnvVar, Gen3DKeyName);
}

// ---------------------------------------------------------------------------

UAISceneBuilderSettings::UAISceneBuilderSettings()
	: ConfigProfile(TEXT("pipeline.yaml"))
	, GenerationTimeoutMinutes(45)
{
}

TArray<FString> UAISceneBuilderSettings::GetConfigProfileOptions() const
{
	TArray<FString> Options;
	const FString ConfigDir = FPaths::Combine(ResolvePipelineRoot(), TEXT("configs"));

	TArray<FString> Found;
	IFileManager::Get().FindFiles(Found, *(ConfigDir / TEXT("*.yaml")), true, false);
	for (const FString& Name : Found)
	{
		// 下划线开头的是临时档，不给用户选
		if (!Name.StartsWith(TEXT("_")))
		{
			Options.Add(Name);
		}
	}
	if (Options.Num() == 0)
	{
		Options.Add(TEXT("pipeline.yaml"));
	}
	return Options;
}

FString UAISceneBuilderSettings::ResolvePipelineRoot() const
{
	const FString Override = PipelineRootOverride.Path.TrimStartAndEnd();
	if (!Override.IsEmpty() && FPaths::DirectoryExists(Override))
	{
		return FPaths::ConvertRelativePathToFull(Override);
	}

	// 默认用插件自带的副本，这样插件是可移植的
	if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AISceneBuilder")))
	{
		return FPaths::ConvertRelativePathToFull(
			FPaths::Combine(Plugin->GetBaseDir(), TEXT("Python")));
	}
	return FString();
}

FString UAISceneBuilderSettings::ResolvePythonExecutable() const
{
	IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();

	// 1) 设置里手填的优先
	const FString Configured = PythonExecutable.FilePath.TrimStartAndEnd();
	if (!Configured.IsEmpty() && PlatformFile.FileExists(*Configured))
	{
		return FPaths::ConvertRelativePathToFull(Configured);
	}

	// 2) PATH 里的 python —— 用 where 一次问清楚，避免逐个猜
	{
		int32 ReturnCode = -1;
		FString StdOut, StdErr;
		FPlatformProcess::ExecProcess(TEXT("where"), TEXT("python"), &ReturnCode, &StdOut, &StdErr);
		if (ReturnCode == 0)
		{
			TArray<FString> Lines;
			StdOut.ParseIntoArrayLines(Lines);
			for (FString& Line : Lines)
			{
				Line.TrimStartAndEndInline();
				// Windows Store 的 python.exe 是个 0 字节的 App Execution Alias，跑不了
				if (!Line.IsEmpty() && PlatformFile.FileExists(*Line)
					&& PlatformFile.FileSize(*Line) > 1024
					&& !Line.Contains(TEXT("WindowsApps")))
				{
					return Line;
				}
			}
		}
	}

	// 3) 常见安装位置兜底
	const FString LocalAppData = FPlatformMisc::GetEnvironmentVariable(TEXT("LOCALAPPDATA"));
	TArray<FString> Candidates;
	for (const TCHAR* Ver : { TEXT("313"), TEXT("312"), TEXT("311"), TEXT("310"), TEXT("39") })
	{
		Candidates.Add(FString::Printf(TEXT("C:/Python%s/python.exe"), Ver));
		if (!LocalAppData.IsEmpty())
		{
			Candidates.Add(FString::Printf(
				TEXT("%s/Programs/Python/Python%s/python.exe"), *LocalAppData, Ver));
		}
	}
	for (const FString& Candidate : Candidates)
	{
		if (PlatformFile.FileExists(*Candidate))
		{
			return FPaths::ConvertRelativePathToFull(Candidate);
		}
	}
	return FString();
}

const UAISceneBuilderSettings& UAISceneBuilderSettings::Get()
{
	return *GetDefault<UAISceneBuilderSettings>();
}

UAISceneBuilderSettings& UAISceneBuilderSettings::GetMutable()
{
	return *GetMutableDefault<UAISceneBuilderSettings>();
}
