---
name: ai-pcg-scene
description: 评审辅助 Skill——把本文件整体丢给任意 coding agent（Claude Code / Cursor / Copilot 等），它就能带你完成本工程包的解压校验、UE 打开、离线复跑整条 AI 场景管线、以及产物核对。人工直接照做也完全可行。
---

# AI 场景建构管线 · 评审一键上手

> 你是一个 coding agent，用户刚拿到本管线的交付工程包
> `AISceneBuilderDemo_submission.zip`。按下面的步骤帮用户跑通全流程；
> 每步都有"预期结果"，不符时看最后的《故障排查》。
> **全程无需外网、无需任何 API Key**——AI 调用凭随包缓存离线回放。

## 0. 环境要求（先帮用户核对）

| 项 | 要求 | 检查方式 |
|---|---|---|
| Unreal Engine | **5.5.4**（其他 5.5.x 通常兼容；5.4/5.6 不保证） | Epic Games Launcher → Library |
| 操作系统 | Windows 10/11 x64 | — |
| 磁盘 | 解压后约 1.5 GB（含 shader 编译缓存余量） | — |
| 外网 / API Key | **不需要** | — |

工程包内**已含预编译插件二进制**（`Plugins/AISceneBuilder/Binaries/Win64`），
评审机没有 Visual Studio 也能直接打开。

## 1. 解压与校验

```powershell
Expand-Archive AISceneBuilderDemo_submission.zip -DestinationPath .
```

解压后应有 `AISceneBuilderDemo/` 目录，关键内容：

- `AISceneBuilderDemo.uproject` — 工程入口
- `QUICKSTART.md` / `技术方案报告.pdf` — 评审文档
- `Plugins/AISceneBuilder/` — 管线插件（含内嵌 Python 管线 + vendor 依赖 + 离线缓存）
- `Plugins/AISceneBuilder/Python/cache/vlm_gateway/` — **离线回放缓存（约 69MB，生命线）**
- `Plugins/AISceneBuilder/Python/refs/ref_original.jpg` — 参考图输入
- `Content/AutoScene/AssetLib/` — 39 模块资产库（预导入 uasset）

## 2. 打开工程

双击 `AISceneBuilderDemo.uproject`（或 Launcher 里 5.5.4 → Open）。

- 首次打开会编译 shader，**等 5–15 分钟属正常**（右下角有进度）。
- 若弹"插件是新版本引擎构建"之类提示，选择继续加载即可。

## 3. 开启离线回放（关键一步）

Edit → Project Settings → Plugins → **AI Scene Builder** → 勾选 **离线回放 (Offline Replay)**。

含义：所有 AI 调用只允许命中随包缓存，不发任何网络请求——评审机零依赖复跑。
（不开的话缓存未命中的调用会显式报错并提示配置，不会静默失败。）

## 4. 跑管线（编辑器 Mode 面板）

1. 顶部菜单 **Tools → AI Scene Builder**（或 Modes 下拉选 AI Scene Builder）打开面板。
2. 参考图选 `Plugins/AISceneBuilder/Python/refs/ref_original.jpg`。
3. 依次点五个阶段按钮：**s01 感知 → s02 布局 → s03 素材 → s04 资产匹配 → s05 装配**
   （或直接点"一键全跑"）。面板实时显示每阶段进度与 API 调用记录。
4. 预期总耗时：**约 3 分钟**（全缓存命中，s05 装配占大头）。

**预期结果**：自动打开/生成关卡 `/Game/Maps/GenScene`，约 127 个 actor、0 错误；
场景为三排桌骨架（左桌带 + 中央桌岛 + 右桌带）+ CRT 阵列 + 海报墙 + 立柱的
复古机房，与参考图构图对应。

## 5. 核对产物

运行产物在 `Plugins/AISceneBuilder/Python/output/run_*/`：

- `scene_manifest.json` — 装配回执（actor 数、错误数、**穿模检测结果 overlaps 应为 0**）
- `api_calls.jsonl` — 逐条 AI 调用留痕（阶段/模型/缓存命中/耗时），即"AI 使用标注"的机器可读证据
- `comparison.png` — 参考图 vs 重建对比图
- `match_report.json` — 资产库四级瀑布匹配明细

与 `技术方案报告.pdf` 第 1 页的指标对照即可完成验收。

## 故障排查

| 症状 | 原因与处理 |
|---|---|
| 打开工程报插件编译失败 | 评审机装了 VS 但版本不符触发重编译——删 `Plugins/AISceneBuilder/Intermediate/` 后重开，走预编译二进制 |
| 面板报"离线回放模式下缓存未命中" | 确认第 3 步开关已勾选、且 `Python/cache/vlm_gateway/` 完整（约 69MB）；换了参考图会导致缓存失配，请用随包的 `ref_original.jpg` |
| 面板报"未配置网关端点" | 说明离线回放没开。本包**刻意不含任何 API 端点与 Key**（保密要求），评审全程走离线回放即可 |
| 场景发黑/材质丢失 | shader 还没编译完，等右下角编译计数归零；或 Lumen 需要几秒收敛 |
| s05 很慢 | 首次装配要为资产库网格编译 shader，第二次跑显著加快 |
| 想看运行中日志 | Output Log 过滤 `AISceneBuilder`；Python 侧日志同步打在面板进度区 |

## 给 agent 的额外提示

- 不要修改 `cache/vlm_gateway/` 下任何文件——哈希键控缓存，动了就全部 miss。
- 不要尝试补配 API：评审场景零依赖是设计目标，不是缺陷。
- 用户若想换参考图重跑：需要在线模式 + 自配网关与 Key（不在评审范围）。
- 管线源码在 `Plugins/AISceneBuilder/Python/`（stages/s01–s05、ue/、core/），
  架构详见 `技术方案报告.pdf` 第 2 页流程图。
