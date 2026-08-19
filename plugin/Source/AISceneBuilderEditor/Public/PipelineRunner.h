// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "HAL/PlatformProcess.h"

class SNotificationItem;

/** 管线的五个步骤。顺序即依赖顺序。 */
enum class EPipelineStage : uint8
{
	Perceive = 0,   // s01 感知：标定 + 云端视觉模型 检测/分割
	Layout,         // s02 布局：地面射线法（纯 numpy，秒级）
	Gen2D,          // s03 二维生成：云端视觉模型 图像（最慢，20–40 分钟）
	Gen3D,          // s04 三维生成：文生 3D / proxy
	Assemble,       // s05 装配：编辑器内 Python
	Count
};

enum class EStageState : uint8
{
	Idle,       // 没跑过
	Running,
	Succeeded,
	Failed,
	Cancelled,
};

struct FStageStatus
{
	EStageState State = EStageState::Idle;
	float Progress = 0.f;          // 0..1
	FString Message;
	FString Error;
	double ElapsedSeconds = 0.0;
};

/**
 * 管线执行器：把长耗时的 Python 阶段跑在**编辑器内嵌 Python 的后台线程**里
 * （ue/embedded_runner.py），仍用 Ticker 轮询进度文件。
 *
 * 为什么不直接用 IPythonScriptPlugin 同步跑：ExecPythonCommandEx 在 GameThread
 * 同步执行，s03 单次几十分钟会把编辑器整个冻死。所以这里只在 GameThread 上执行
 * 一条「启动后台线程」的语句立即返回；s01–s04 是纯 Python（HTTP/cv2），在线程里
 * 跑不碰 unreal.*。s05 需要引擎 API，由 Toolkit 在 GameThread 上另行执行。
 *
 * 依赖（numpy/cv2/yaml）vendor 在插件 Content/Python/Win64/Lib/site-packages，
 * 启动语句显式插 sys.path——评审机零 pip、零外部 Python。
 *
 * 注意：Python 线程无法被强杀。Cancel 只是 UI 不再关注（token 失配注销 ticker），
 * 线程会跑完当前阶段自然结束；embedded_runner.start 对双重启动返回 busy 自保。
 */
class AISCENEBUILDEREDITOR_API FPipelineRunner : public TSharedFromThis<FPipelineRunner>
{
public:
	DECLARE_MULTICAST_DELEGATE_TwoParams(FOnStageChanged, EPipelineStage, const FStageStatus&);

	/**
	 * 模块级单例。执行器的寿命必须长过面板：美术点完「运行」就可以切回 Select 模式
	 * 继续摆关卡，s03 那 20–40 分钟不该因为面板析构而被腰斩。面板只是它的一个观察者。
	 */
	static TSharedRef<FPipelineRunner> Get();
	/** 模块卸载时调用：杀掉在跑的子进程并释放单例。 */
	static void Teardown();

	FPipelineRunner();
	~FPipelineRunner();

	/** 状态变化（开始/进度/结束）都会广播这一个委托。 */
	FOnStageChanged OnStageChanged;

	/** 启动一个阶段（内嵌 Python 后台线程）。已有任务在跑时返回 false。 */
	bool RunStage(EPipelineStage Stage);

	/** 放弃当前任务（线程不可强杀，只是 UI 不再关注）。 */
	void Cancel();

	bool IsBusy() const { return bTaskActive; }
	EPipelineStage GetRunningStage() const { return RunningStage; }
	const FStageStatus& GetStatus(EPipelineStage Stage) const;

	/** 从磁盘产物恢复各步状态——重开编辑器后仍能接着上次继续。 */
	void RefreshFromDisk();

	/** 当前参考图对应的 run 目录（output/run_<hash8>）。参考图未设置时为空。 */
	FString GetRunDir() const;

	static const TCHAR* StageId(EPipelineStage Stage);      // "s01_perceive"
	static FText StageDisplayName(EPipelineStage Stage);
	/** 该阶段的产物是否齐全——用来判成功，也用来恢复状态。 */
	static TArray<FString> StageOutputs(EPipelineStage Stage);

private:
	bool Tick(float DeltaTime);
	void FinishStage(EStageState NewState, const FString& Error);
	void SetStatus(EPipelineStage Stage, const FStageStatus& Status);
	bool ReadProgressFile(FStageStatus& OutStatus);
	bool StageOutputsExist(EPipelineStage Stage) const;

	/** 右下角非模态进度通知。由执行器而非面板持有——保证一次运行只有一条。 */
	void OpenNotification(EPipelineStage Stage);
	void CloseNotification(EStageState FinalState);

	bool bTaskActive = false;               // 内嵌线程是否在（我们关注的）运行中
	FTSTicker::FDelegateHandle TickHandle;
	EPipelineStage RunningStage = EPipelineStage::Count;
	uint64 RunToken = 0;
	double StartTime = 0.0;
	double Deadline = 0.0;
	FString ProgressPath;
	mutable FString CachedRunDir;   // 由进度文件回报，避免在 C++ 里重算哈希
	TSharedPtr<SNotificationItem> Notification;

	FStageStatus Statuses[(uint8)EPipelineStage::Count];
};
