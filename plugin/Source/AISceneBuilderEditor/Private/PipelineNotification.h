// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/Notifications/INotificationWidget.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "Widgets/SCompoundWidget.h"

/**
 * 右下角的非模态进度通知：标题 + 明细 + 进度条 + 取消。
 *
 * 为什么不用 FScopedSlowTask：它是**模态**的，会把编辑器锁死。s03 单次 20–40 分钟，
 * 美术这段时间还要继续摆关卡，必须非模态。
 *
 * 生命周期上的一个硬要求：本控件的所有属性委托只能捕获 **TWeakPtr<FPipelineRunner>**，
 * 绝不能捕获 Toolkit。通知的存活时间比面板长——美术切走模式后面板就析构了，
 * 而通知还挂在屏幕右下角继续跳进度。捕获面板就是野指针。
 */
class SPipelineNotification : public SCompoundWidget, public INotificationWidget
{
public:
	SLATE_BEGIN_ARGS(SPipelineNotification) {}
		SLATE_ATTRIBUTE(FText, Title)
		SLATE_ATTRIBUTE(FText, Detail)
		SLATE_ATTRIBUTE(TOptional<float>, Percent)
		SLATE_EVENT(FSimpleDelegate, OnCancel)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	// INotificationWidget
	virtual void OnSetCompletionState(SNotificationItem::ECompletionState InState) override
	{
		CompletionState = InState;
	}
	virtual TSharedRef<SWidget> AsWidget() override { return AsShared(); }

private:
	EVisibility GetCancelVisibility() const
	{
		return CompletionState == SNotificationItem::CS_Pending ? EVisibility::Visible
															    : EVisibility::Collapsed;
	}

	SNotificationItem::ECompletionState CompletionState = SNotificationItem::CS_Pending;
	FSimpleDelegate OnCancel;
};
