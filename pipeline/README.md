# AI 驱动的自动化场景建构管线（实现版 v2）

一张参考图 → UE5 里摆好、打好光、调好色的关卡。

**v2 架构（当前）**：整条管线运行在 **UE5 编辑器内嵌 Python 3.11** 里，以编辑器
Mode（Modes 下拉 → AI Scene Builder）形式操作；依赖（numpy/cv2/yaml）vendor 在
插件 `Content/Python/Win64/Lib/site-packages`，评审机**零 pip、零外部 Python**。
s01–s04 跑在后台线程（纯 Python，不碰 unreal.*），s05 装配在 GameThread 进程内
执行。3D 资产来自**外部预生成的资产库**（40 个模块，VLM 打标注册 + 四级瀑布匹配），
不再现场生成。CLI 仍保留为开发调试入口：

```bash
python pipeline.py --ref refs/ref_original.jpg          # 命令行端到端（调试用）
```

**离线回放**：AI 调用带哈希缓存（`cache/vlm_gateway/` 随交付附带）。评审机没有云端
云端网关访问权限时，开 Project Settings → AI Scene Builder → 离线回放，全程只回放缓存、
miss 显式报错。每次运行的逐调用记录在 `output/run_*/api_calls.jsonl`
（阶段/模型/端点/缓存命中/耗时）——这是"AI 使用标注"的机器可读版。

## 与技术方案的对应关系

实现遵循 `D:\作品集\AI场景管线方案\..._v2_全云端.md`，但按本机的真实资源做了三处适配，
每一处都写在代码注释里：

| 方案原设计 | 本实现 | 原因 |
|---|---|---|
| google-genai SDK `client.interactions.create` | **云端 VLM 网关**（OpenAI 兼容 `/v1/chat/completions`） | 本机只有 OpenAI 兼容网关通路，没有直连 Google 的通道 |
| `response_schema` 硬约束结构化输出 | prompt 约定 + 本地校验 + 一次修复重试 | 代理不透传 schema 约束 |
| 云端图生 3D `SubmitImageTo3DJob` 四段链 | **v2 默认 `library`：外部预生成的 40 模块资产库 + VLM 打标 + 四级瀑布匹配**；云端 provider（`cloud_text2gen`/`gen3d_cloud`/`gen3d`）实现保留可切 | 高模资产已离线生成完毕，现场生成改为现场匹配 |

## 凭证现状

| 服务 | 状态 | 位置 |
|---|---|---|
| **云端 VLM 网关**（Gemini 文本/视觉/图像） | ✅ 可用 | `VLM_API_KEY` 环境变量，或工程 `Config/DefaultEditor.ini` 的 `[AISceneBuilder.Gateway] ApiKey`（与插件 C++ 同源；旧 LegacyProject 路径保留垫底） |
| **云端文生 3D** | ❌ 缺凭证 | 需 `GEN3D_API_KEY` + `GEN3D_DOMAIN`；未配置时自动回落 proxy |
| **云端图生 3D 服务**（图生高模/贴图） | ❌ 缺凭证 | 需 `GEN3D_APP_ID` / `GEN3D_SECRET_KEY` / `GEN3D_USER` |
| 云端图生 3D（智能减面） | ❌ 缺 | 需 `GEN3D_API_KEY` |

**文生 3D 是首选路线**（`gen3d.provider: auto` 时只要有 key 就自动启用）。相比图生 3D，
它不需要抠图、不需要多视角一致性，绕开了绿幕/turnaround/跨视角漂移一整串风险；
prompt 由感知层的中文描述 + 材质标签 + 真实尺寸拼成，例如：

```
米白色老式CRT显示器，屏幕发青色光, beige plastic yellowed, about 39x41x40 cm,
single isolated game asset, neutral studio lighting, no background, no base plate, front facing
```

拿到 key 后：`set GEN3D_API_KEY=...` 即可，配置与下游零改动。

**没有 3D 凭证时管线照常跑完**：s04 用 `proxy` 后端按 layout 的真实尺寸生成带贴图的
盒体（pivot 在底部中心、UV 正确、正面贴清理后的资产图），UE 里摆出来是一个尺度、
朝向、材质、灯光都对的完整场景。凭证到位后把 `configs/pipeline.yaml` 的
`gen3d.provider` 改成 `gen3d_cloud` 并填三个环境变量即可，**下游零改动**。

## 阶段（v2）

| 阶段 | 输入 → 输出 | 用到的 AI |
|---|---|---|
| `s00`（离线一次性） | `Asset\` 原始模块 → `AssetLib_processed/` + `library_registry.json` | `tools/build_assetlib.py` 减面/缩图（无 AI）；`tools/register_library.py` **VLM 打标**（受控词表 `taxonomy.yaml`，与 s01 感知共用） |
| `s01_perceive` | `ref.jpg` → `calib.json` `perception.json` | Gemini 解析/检测/分割；**标定是纯 OpenCV，无 AI** |
| `s02_layout` | 上二者 → `scene_layout.json` | 无（纯 numpy 地面射线法） |
| `s03_gen2d` | layout + ref → `gen2d/` | Gemini 图像：无缝材质 / 海报与屏幕文字 / **氛围估计**（主光色温/雾/曝光）。库模式下 hero 清理图与 turnaround 关闭 |
| `s04_gen3d` | layout + 库 → `asset_registry.json` `match_report.json` | **库匹配四级瀑布**：人工覆盖 → 类别命中+变体轮换 → VLM 图像相似度兜底 → proxy 盒。云端生成路径保留可切 |
| `s05_build_scene` | 全部 → `build_manifest.json` + UE 关卡 | 无（UE5 Python 确定性装配；库资产走 load_asset 快路径） |

匹配复核：`match_report.json` 逐对象记录命中模块/方法/缩放/畸形旗（SCALE_WARN/
DANGER/ABSURD、SHAPE_MISMATCH）；改指写 `output/run_*/match_overrides.json`
（`{"by_instance": {"crt_monitor_45": "M12"}}`，存在即覆盖）后重跑 s04+s05。

## 常用命令

```bash
python pipeline.py --ref refs/server_room.jpg --stages s01,s02      # 只跑感知+布局
python pipeline.py --ref refs/server_room.jpg --resume              # 断点续跑
python pipeline.py --ref refs/server_room.jpg --force s03           # 强制重跑某段
python pipeline.py --ref refs/server_room.jpg --skip-ue             # 只编译 manifest，不起编辑器
python pipeline.py --ref refs/server_room.jpg --config configs/smoke.yaml   # 冒烟档（3 hero、无 turnaround）
```

## 缓存

| 缓存 | 位置 | 语义 |
|---|---|---|
| 阶段指纹 | `cache/stages/` | 输入/代码（**含 core/**）/prompt/配置没变就跳过整个阶段；`unreal` 段只进 s05 指纹，`vlm_gateway.offline` 不进指纹 |
| 请求哈希 | `cache/vlm_gateway/` | 同一请求不再花钱；**复跑字节一致**；也是离线回放的数据源 |

Gemini 没有 seed，采样级复现不可能。请求哈希缓存把可复现性兑现在**产物级**：
只要 `cache/vlm_gateway/` 随交付保留，任何机器复跑得到完全相同的资产。**这个目录要一起交付。**

## 标定失败怎么办

`s01` 会打印标定置信度。低于 `calibration.min_confidence`（默认 0.5）时看
`output/run_*/calib_debug.png`（线段按族着色 + 地平线）。确实不准就用 fSpy 手标，
把结果写进 `output/run_*/calib_manual.json`：

```json
{"hfov_deg": 62.0, "pitch_deg": -4.5, "roll_deg": 0.8, "cam_height_m": 1.52}
```

该文件存在时**无条件覆盖**自动标定（v2 起会正确重算 fx/fy/up_cam 派生量）。
这是全管线唯一的强人工介入点。

手标直觉（本参考图实测校准过）：桌面出现在画面中线附近 → 机位是**低机位**
（0.9–1.1m，不是站姿 1.5m）；透视收敛强、近景大 → 广角（hfov 80–90°）。
参数给错的症状很直观：hfov 偏小或机高偏大都会把所有物体的解算距离拉远，
超过 `max_solve_distance_m` 的对象被整批剔除，场景突然变空。

## 环境

**编辑器内运行（正式形态）**：只需 UE 5.5 + 插件。依赖已 vendor 在插件
`Content/Python/Win64/Lib/site-packages`；缺失时面板"安装依赖"按钮会用**引擎
自带的 Python** 往 vendor 目录 pip（需网络）。

**CLI 调试**：`pip install -r requirements.txt`（系统 Python 3.10+）。

改动纪律：管线代码只改本目录（canonical），改完 `python tools/sync_plugin.py`
同步进插件；`python tools/check_no_unreal.py` 把关 s01–s04 永不 import unreal
（它们跑在后台线程，unreal.* 是 GameThread-only，UE5.6 起跨线程硬报错）。

## 目录

```
pipeline/                      ← canonical，改这里，tools/sync_plugin.py 同步进插件
├── configs/pipeline.yaml     全局配置（凭证一律走环境变量/工程 ini，不入库）
├── taxonomy.yaml             受控类别词表：s01 感知 / s00 打标 / s04 匹配共用
├── prompts/                  Gemini prompt 模板 —— 改模板即失效阶段缓存
├── core/
│   ├── vlm_gateway.py              云端 VLM 网关 客户端：哈希缓存 / 离线回放 / api_calls.jsonl
│   ├── libmatch.py           资产库匹配引擎（四级瀑布 + 缩放畸形保护，纯函数）
│   ├── calib_vp.py           消失点标定 + apply_manual_override（fSpy 覆盖）
│   ├── solver.py             地面射线法分层求解 + 尺度校准 + 置信度
│   ├── imaging.py            抠像 / ECC 对齐 / 接缝质检 / PBR 通道推导
│   └── (云端 3D provider 模块不在本仓库，见 README 说明)
├── stages/                   s01–s05，每个暴露 run(ctx)->dict
├── ue/
│   ├── embedded_runner.py    UE 内嵌宿主：后台线程跑 s01–s04 / GameThread 编译与装配
│   ├── compile_layout.py     scene_layout → build_manifest（确定性纯函数）
│   ├── build_scene.py        UE5 编辑器内装配脚本（库资产 load_asset 快路径）
│   └── import_assetlib.py    资产库一次性预导入（作者侧）
├── tools/
│   ├── build_assetlib.py     资产预处理：减面 80k / 贴图 2K/1K / ORM 合并 / Z-up 厘米化
│   ├── register_library.py   VLM 打标 → library_registry.json（人工核对可保护）
│   ├── sync_plugin.py        canonical → 插件 同步（--check 供 CI）
│   ├── check_no_unreal.py    红线自检：s01–s04 禁 import unreal
│   └── package_submission.py 交付打包：白名单 + Key 泄漏扫描 + 体积构成表
├── cache/                    stages/ 与 vlm_gateway/
└── output/run_<图hash>/      每次运行的全部产物与日志（含 api_calls.jsonl / match_report.json）
```

---

## 实测状态（2026-08-06 全链路验证）

用 `refs/server_room.jpg`（Gemini 生成的赛博朋克机房测试图，与原题参考图构图一致）
端到端跑通 s01→s05，UE 关卡已落盘：

| 阶段 | 结果 |
|---|---|
| s01 感知 | 70 个检测实例、11 个语义类；**首跑 270s，命中缓存后 27s** |
| s02 布局 | 69 个实例；四种求解方法都在用（落地 27 / 桌面 24 / 墙面 14 / 天花板 4）；自由摆放物越界 0 |
| s03 二维生成 | 2 hero + 7 套材质 + 9 张海报，0 失败 |
| s04 三维生成 | provider=proxy（无云端凭证），2 个资产 |
| s05 UE 装配 | **status=ok，24 actors，0 errors，35s**；`/Game/Maps/GenScene.umap` + 34 个 uasset |

24 个 actor 的构成：房间壳 6 面 + hero 2 + 灯板 1（StaticMeshActor 9）、海报贴花 9、
RectLight/PointLight/SpotLight 各 1、体积雾 1、后期体积 1、对齐机位相机 1。

### 已知限制（如实记录）

1. **标定退回了 60° 先验**（`calib_confidence=0.12`）。这张图是接近一点透视的正对
   构图且相机基本水平——竖直线在像内平行、第二组水平消失点不可靠，焦距在数学上
   不可观测。方案文档预判过这个失败情形。后果是绝对尺寸系统性偏大，被先验钳制
   收在 +33% 以内（比例正确）。**要精确尺寸就用 fSpy 标一次**，写进
   `calib_manual.json` 即可覆盖。
2. **接缝质检 7 套材质里 3 套过线**（阈值 1.25）。未过的取分数最低一次并留痕，
   房间壳会自动挑分数最低的那套。阈值本身需按实拍标定。
3. **s03 是耗时大头**：每次图像生成约 30s，`max_hero_assets=12` + turnaround 时
   单场景约 20–30 分钟。缓存命中后复跑近乎免费。

### 调试工具

```bash
python tools/preview_layout.py output/run_xxxx/scene_layout.json
```

产出 `layout_preview.png`：上半是俯视平面图（含相机位与朝向、低置信物体压暗），
下半是立面图（看桌上物是否真在桌面高度、海报是否在墙上）。装配前两秒自检。

---

## UE 材质踩坑记录（2026-08-06）

场景一度渲成一片灰，查下来是**两个叠加的静默失败**，都值得记住：

1. **`connect_material_property(node, "RGB", MP_BASE_COLOR)` 返回 False 但不抛异常。**
   贴图采样节点的输出名传 `"RGB"` 在本版本匹配不到，材质照常"编译成功"，
   但 BaseColor 是空的 → 整场纯灰。改用 `""`（默认输出）后正常。
   所有 `connect_*` 现在都检查返回值并记日志——静默失败一次就够了。

2. **截图早于着色器编译完成。** 新建/改过的材质要异步编译着色器；
   `-ExecutePythonScript` 一进来就截图，拿到的是尚未编译完的默认材质，
   表现为"改了三次材质，截图字节完全一致"。`tools/shot.py` 现在先空转
   300 tick 等编译队列排空再截。

另外两个必须的前置：`TextureSampleParameter2D` **必须有默认贴图**否则编译不过；
采样器类型要与默认贴图匹配（给 MASKS 采样器配 sRGB 贴图 = 编译失败）。

引擎 Cube 每面 UV 都是 0~1，与实际尺寸无关——房间壳必须按面尺寸设 `Tiling`，
否则一张 1024 贴图铺满 10 米墙，一块砖 1 米宽。
