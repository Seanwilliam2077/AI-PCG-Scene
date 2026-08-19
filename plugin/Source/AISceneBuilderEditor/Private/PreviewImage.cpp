// Copyright (c) TA Pipeline. All Rights Reserved.

#include "PreviewImage.h"

#include "Engine/Texture2D.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"

namespace AISceneBuilder
{
	UTexture2D* LoadTextureFromFile(const FString& AbsolutePath)
	{
		if (AbsolutePath.IsEmpty() || !FPaths::FileExists(AbsolutePath))
		{
			return nullptr;
		}

		TArray<uint8> Compressed;
		if (!FFileHelper::LoadFileToArray(Compressed, *AbsolutePath) || Compressed.Num() == 0)
		{
			return nullptr;
		}

		IImageWrapperModule& Module =
			FModuleManager::LoadModuleChecked<IImageWrapperModule>(FName("ImageWrapper"));

		// 按后缀先猜格式，猜错了再试另一种——管线产出的既有 PNG 也有 JPG
		const FString Ext = FPaths::GetExtension(AbsolutePath).ToLower();
		EImageFormat First = (Ext == TEXT("jpg") || Ext == TEXT("jpeg"))
			? EImageFormat::JPEG : EImageFormat::PNG;
		EImageFormat Second = (First == EImageFormat::PNG)
			? EImageFormat::JPEG : EImageFormat::PNG;

		TSharedPtr<IImageWrapper> Wrapper = Module.CreateImageWrapper(First);
		if (!Wrapper.IsValid() || !Wrapper->SetCompressed(Compressed.GetData(), Compressed.Num()))
		{
			Wrapper = Module.CreateImageWrapper(Second);
			if (!Wrapper.IsValid() || !Wrapper->SetCompressed(Compressed.GetData(), Compressed.Num()))
			{
				return nullptr;
			}
		}

		TArray<uint8> Raw;
		if (!Wrapper->GetRaw(ERGBFormat::BGRA, 8, Raw))
		{
			return nullptr;
		}

		const int32 Width = Wrapper->GetWidth();
		const int32 Height = Wrapper->GetHeight();
		UTexture2D* Texture = UTexture2D::CreateTransient(Width, Height, PF_B8G8R8A8);
		if (!Texture)
		{
			return nullptr;
		}

		void* Dest = Texture->GetPlatformData()->Mips[0].BulkData.Lock(LOCK_READ_WRITE);
		FMemory::Memcpy(Dest, Raw.GetData(), Raw.Num());
		Texture->GetPlatformData()->Mips[0].BulkData.Unlock();
		Texture->UpdateResource();

		// Slate brush 只持弱引用，不保活就会被 GC 掉、预览变黑
		Texture->AddToRoot();
		return Texture;
	}

	void ReleasePreviewTexture(TWeakObjectPtr<UTexture2D>& Texture)
	{
		if (UTexture2D* Raw = Texture.Get())
		{
			Raw->RemoveFromRoot();
		}
		Texture.Reset();
	}
}
