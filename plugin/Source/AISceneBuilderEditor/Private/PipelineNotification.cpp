// Copyright (c) TA Pipeline. All Rights Reserved.

#include "PipelineNotification.h"

#include "Styling/AppStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Notifications/SProgressBar.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "AISceneBuilder"

void SPipelineNotification::Construct(const FArguments& InArgs)
{
	OnCancel = InArgs._OnCancel;

	ChildSlot
	[
		SNew(SBox)
		.WidthOverride(360.f)
		.Padding(FMargin(12.f, 10.f))
		[
			SNew(SVerticalBox)

			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock)
				.Font(FAppStyle::GetFontStyle(TEXT("NotificationList.FontBold")))
				.Text(InArgs._Title)
			]

			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f, 0.f, 0.f)
			[
				SNew(STextBlock)
				.Font(FAppStyle::GetFontStyle(TEXT("NotificationList.FontLight")))
				.Text(InArgs._Detail)
				.AutoWrapText(true)
			]

			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f, 0.f, 0.f)
			[
				SNew(SProgressBar)
				.Percent(InArgs._Percent)     // 未知进度时返回空 TOptional → 自动走滚动条动画
			]

			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f, 0.f, 0.f)
			.HAlign(HAlign_Right)
			[
				SNew(SButton)
				.Text(LOCTEXT("NotifCancel", "取消"))
				.ToolTipText(LOCTEXT("NotifCancelTip", "结束当前阶段的 Python 子进程"))
				.Visibility(this, &SPipelineNotification::GetCancelVisibility)
				.OnClicked_Lambda([this]()
				{
					OnCancel.ExecuteIfBound();
					return FReply::Handled();
				})
			]
		]
	];
}

#undef LOCTEXT_NAMESPACE
