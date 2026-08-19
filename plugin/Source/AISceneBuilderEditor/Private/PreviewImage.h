// Copyright (c) TA Pipeline. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

class UTexture2D;

namespace AISceneBuilder
{
	/**
	 * 从磁盘 PNG/JPG 建一张 transient 纹理供 Slate 预览。
	 *
	 * 返回的纹理已 AddToRoot 保活——Slate brush 只持弱引用，不这么做会被 GC 掉、
	 * 预览变黑。用完必须调 ReleasePreviewTexture 还回去。
	 */
	UTexture2D* LoadTextureFromFile(const FString& AbsolutePath);

	/** 与 LoadTextureFromFile 配对：RemoveFromRoot 并清空句柄。 */
	void ReleasePreviewTexture(TWeakObjectPtr<UTexture2D>& Texture);
}
