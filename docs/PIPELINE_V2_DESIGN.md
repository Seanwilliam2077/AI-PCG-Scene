# 管线 v2 重构评估与目标架构

> **实施状态（2026-08-10）：M0–M5 全部完成。** 实测：内嵌 Python 3.11 编辑器内跑通全管线；
> 资产库 39 模块注册、26 对象命中 25（proxy 1）、decal 7 张；UE 装配 144 actors / 0 errors / 36s；
> 打包体积 447MB（<500MB）；离线回放与 api_calls.jsonl 就位。对比图见
> `output/run_fd6e434f/comparison.png`。已知限制：标定退 60° 先验导致房间偏大
> （fSpy 覆盖已修复可用）、墙面材质取样偏蓝（可经 match_overrides/重跑修正）。

> 目标：满足四项新需求 —— ① 整条管线在 UE5 内运行；② API 在 UE 中显式呈现；③ 以编辑器 Mode 形态存在；④ 3D 资产改用已生成完毕的 40 个模块资产库（不再现场生成）。
> 约束：交付工程包 ≤500MB，评审机只装 UE + 插件（默认无外网、无云端网关访问权限、无 API Key）。
> 本文档基于：对现有代码的两轮核查（见 PIPELINE_REVIEW.md）+ 5 路并行验证（Epic 官方文档核实 ×3、红队审查、匹配环节设计）。

---

## 一、结论：不需要推倒重构，需要一次"换宿主"的中等改造

四项需求逐项对照现状：

| 需求 | 现状 | 缺口 |
|---|---|---|
| ③ Mode 形态 | **已经满足**。插件已是 `UAISceneBuilderEdMode : UBaseLegacyWidgetEdMode` + `FAISceneBuilderToolkit`，自动注册进 Modes 下拉（AISceneBuilderEdMode.cpp:15-20），且用的正是 Epic 推荐的过渡基类 | 无需结构改动，只补面板内容与图标 |
| ② API 显式 | 部分满足。`UAISceneBuilderSettings : UDeveloperSettings` 已在 Project Settings（Python 路径/参考图/配置档/超时），Key 走环境变量+ini 三级回退 | 网关 URL/模型名/Key 状态未上设置面板；无 API 调用记录；Key 配置类有入库风险需拆分 |
| ① 在 UE5 内跑 | **不满足**。s01–s04 依赖外部系统 Python（AISceneBuilderSettings.h:25 注释明说"UE 内置 Python 没有 numpy/cv2"），子进程 + 进度文件轮询 | 核心改造点：执行宿主迁移到 UE 内嵌 Python 3.11.8 |
| ④ 资产库化 | 不满足。s04 现在是"云端 3D 生成（无凭证则 proxy 盒）"，实跑全是 555 字节代理盒 | 新增 s00 资产库注册 + s04 改造为库匹配；40 模块（FBX/GLB/OBJ + 4 张 25MB 级 PBR 贴图，源共 ~20.5GB）需离线预导入并解决体积 |

骨架（阶段拆分、文件 run 目录、指纹 resume、vlm_gateway 请求缓存、Mode 壳、Settings、进度机制）全部保留。改造集中在四个工作包：**执行宿主迁移、资产库匹配、氛围阶段（还原度加分）、离线演示与体积工程**。

---

## 二、已验证的技术前提（均查证 Epic 官方文档/源码/论坛）

| # | 事实 | 结论 | 要点 |
|---|---|---|---|
| 1 | UE5.5 内嵌 Python = **3.11.8**；`.uplugin` 支持 `PythonRequirements` 自动 pip（5.4 起） | ✅ | 装到 `Intermediate/PipInstall/`；需逐 wheel 带 sha256；**评审机防火墙下会失败** |
| 2 | 把 wheel 解包进插件 `Content/Python/Lib/site-packages`（二进制包放 `Content/Python/Win64/Lib/site-packages`）是**官方文档认可**的离线做法，编辑器自动加 sys.path | ✅ | **主方案**：vendor numpy(<2) + opencv(abi3 wheel 兼容 3.11) + PyYAML；PythonRequirements 仅作有网备选 |
| 3 | 内嵌解释器里 `threading.Thread` 跑纯 Python（HTTP/cv2，不碰 unreal.*）可行（5.2–5.5 社区长期实践）；`unreal.*` 是 GameThread-only——5.5 不强制拦截、**5.6 起硬报错** | ⚠️ | 现在就按 GameThread-only 纪律写；回主线程用 `unreal.register_slate_post_tick_callback` + `queue.Queue`（官方 API） |
| 4 | 长任务不冻结编辑器：后台线程干纯 Python 活 + 主线程 tick 分帧干 unreal 活；`ScopedSlowTask` 仅提供进度/取消 UI 不解决阻塞 | ✅ | s05 装配按"每帧时间预算"分帧执行 |
| 5 | 纯面板 Mode 不需要 InteractiveToolsFramework（"必须绑定 ITF"被证伪）；现有 `GetInlineContent()` 返回自绘 Slate 即当前做法 | ✅ | 现有 Mode 壳无需推翻；引擎参考实现：Fracture Mode（同为 UBaseLegacyWidgetEdMode 家族） |
| 6 | GLB 走 Interchange 脚本化导入（`is_automated=True` 无对话框，管线可设合并/碰撞/`build_nanite`/不导材质）；**5.5 里 FBX 默认也被 Interchange 接管**，要走旧 AssetImportTask 需关 `Interchange.FeatureFlags.Import.FBX` | ✅ | 40 模块首选 GLB+Interchange，导入时直开 Nanite |
| 7 | **`max_texture_size`/LODBias 只影响 cook 后数据；未 cook 的 .uasset 存的是原始源像素** —— 4K 25MB PNG 导入后 uasset 仍 ~25MB | ⚠️ 关键 | 压体积必须**导入前**缩源图（PIL/cv2），导入后设置救不了交付体积 |
| 8 | 高面数导入耗时数十秒~分钟/个且同步阻塞 UI；uasset 同小版本引擎下直接拷贝/Migrate 即用 | ✅ | 资产库**离线一次性预导入**，交付 uasset；管线运行时只 `load_asset` |

---

## 三、目标架构 v2

```
┌────────────────────────── UE5.5 编辑器进程 ──────────────────────────┐
│  Modes ▾ [AI Scene Builder]  ← 已有 UAISceneBuilderEdMode（保留）      │
│  ┌─ FModeToolkit 面板 ──────────────────────────────────────────┐   │
│  │ 参考图选择 │ 阶段卡(s01..s06) │ 资产库页签 │ API 页签 │ 生成/刷新 │   │
│  │  API 页签：端点/模型/Key状态(掩码) + 调用记录列表                 │   │
│  │           [阶段|endpoint|模型|cache hit|耗时]  + 离线回放开关 ⬅ │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  内嵌 Python 3.11.8（依赖 vendor 在插件 Content/Python/Win64/…）       │
│  ┌─ 后台 threading.Thread（纯 Python，禁 import unreal）───────────┐  │
│  │  s01 感知/标定 → s02 布局 → s03' 贴花/屏幕纹理 → s04' 库匹配      │  │
│  │  （HTTP→云端 VLM 网关 / cv2 / numpy；进度与结果放 queue.Queue）          │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  主线程 register_slate_post_tick_callback：收割 queue → 刷 UI          │
│  s05' 装配：主线程分帧（load_asset + spawn，每帧时间预算）              │
│  s06 氛围：VLM 估光 → 布光/雾/PPV/Lumen + 相机对位 + 对比截图           │
└──────────────────────────────────────────────────────────────────────┘
        ▲ 一次性离线（作者侧，不在演示路径）
  AssetLib 预构建：源图缩 2K/1K + ORM 合并 → GLB Interchange 导入
  （Nanite、Box 碰撞、MI）→ /Game/AutoScene/AssetLib/*.uasset
  + s00 注册表：VLM 对 image_url.png 打标 → library_registry.json → 面板人工校对
```

阶段变化对照：

| 阶段 | v1（现状） | v2 |
|---|---|---|
| s00 | 无 | **新增**：资产库注册（离线一次性，VLM 打标 + 面板校对） |
| s01/s02 | 外部 python.exe | 逻辑不变，宿主换成内嵌 Python 后台线程 |
| s03 | hero 视图 + 材质 + 贴花全量生成 | **收窄**：只生成库里没有的（贴花/屏幕内容/房间壳材质）；hero turnaround 全部取消（库里已有真模型） |
| s04 | 云端 3D 生成 / proxy 盒 | **改造**：库匹配（四级瀑布），未命中才 proxy；输出仍是 `asset_registry.json` 契约，s05 零改动 |
| s05 | UnrealEditor-Cmd 冷启动 + %TEMP% 旁路 | 进程内直接调 unreal API，主线程分帧；**删除子进程与 %TEMP%/args.json 整套 hack** |
| s06 | 无 | **新增**：氛围还原（对应还原度 2 分加分项） |

**架构红线**：s01–s04 永远不 `import unreal`（加自检脚本 grep 把关）→ CLI 薄包装保持可用，Python 逻辑可在编辑器外回归调试；顺带 3.10→3.11 跑一遍全量回归（现有 `__pycache__` 是 cpython-310）。

---

## 四、资产库匹配环节（v2 的心脏，红队指出原草案此处悬空）

### 4.1 注册表（s00，离线一次性）

40 个模块目录名无语义（"模块 7_Tex"），语义只能来自每个目录里的 `image_url.png`（白底展示图）+ 网格实测：

- **受控词表 taxonomy**：维护一张类别表（crt_monitor / computer_tower / folding_chair / long_table / keyboard / printer / power_strip / mouse / floppy_disk / poster / newspaper / sign / traffic_cone / paper_cup / soda_can …），**s01 感知 prompt 与打标 prompt 引用同一份文件**——两侧 label 天然对齐，运行时匹配退化成字符串相等。
- 每模块经 VLM 打标（走 vlm_gateway 哈希缓存，可复现可交付）得到：`category / variant_desc / is_screen / is_flat_print / poster_text / est_size_cm`；脚本实测得到权威几何：`mesh_bbox_cm / pivot / tri_count`（FBX 单位陷阱：读 unit scale 统一折算 cm）。
- LLM 预估尺寸 vs 实测尺寸交叉校验，比例差异大的打 `needs_review`，在面板"资产库"页签人工校对（`human_verified=true` 后重打标不覆盖）。
- 落盘 `library_registry.json`（含 `category_index` 派生索引）。**这一步本身就是"AI 工具综合应用"的加分素材**（VLM 自动打标 + 人工在环）。

### 4.2 运行时匹配（s04'，四级瀑布）

```
L0 人工覆盖（match_overrides.json，面板改指，语义同 calib_manual：存在即覆盖）
L1 精确类别命中 → L2 同类变体确定性轮换（6 种 CRT 在 10 台显示器上均匀轮流，
   按实例 id 排序 round-robin，复跑字节一致）；候选先按三轴形状相似度排序
L3 VLM 图像相似度兜底：参考图该物体的 bbox crop vs 未覆盖候选的编号拼图，
   confidence ≥ 0.6 才采纳（走 vlm_gateway 缓存）
L4 降级 proxy 盒（尺寸永远正确，场景比例不塌），match_report.json 标红
```

### 4.3 尺寸对齐与畸形保护

权威尺寸只认 `mesh_bbox_cm` 实测；`uniform` 模式取三轴比中位数（抗单轴离群），可拉伸类（桌/凳/线缆）允许 `per_axis`。保护阈值：缩放比 [0.5,2] 外 `SCALE_WARN`、[0.2,5] 外 `SCALE_DANGER` 进复核队列、10 倍级 `SCALE_ABSURD` 判定误匹配→自动走 L3 重匹配一次→仍畸形则 proxy。屏幕类保持屏幕面两轴等比（4:3 CRT 不许拉成宽屏）。

### 4.4 海报/报纸类（is_flat_print）

不走网格：优先从库资产直接取 `T_*_BC.png` 做 DecalActor 贴图（平面资产 UV 通常整片 0~1，校验 UV 覆盖率 >90%）；回退用 `image_url.png` 抠像+透视矫正。**个体级匹配**：s01 读出的海报文字 vs 注册表 `poster_text` 先做文本匹配（参考图里那张具体的报纸回到它该在的墙上），失配再类别轮换。

### 4.5 面板交互（"资产库"两个页签）

- **注册表校对**：表格（缩略图/目录名/类别下拉(仅词表)/实测尺寸/屏幕类/印刷品/启用/已校对），needs_review 置顶标黄；双击在视口按真实尺寸 spawn 预览 + 1m 参照方块（FBX 单位错一眼现形）。
- **匹配复核**：每行 [参考图 crop → 命中模块缩略图 | method 徽章 | scale | flags]，proxy/危险缩放置顶标红；行内改指写 `output/run_*/match_overrides.json`，"应用重摆"只重跑 s04'+s05。

---

## 五、体积预算（红队一票否决项之一，必须先算账再动手）

源资产 ~20.5GB（单模块 ~309MB）。三个关键事实：**未 cook 的 uasset 存原始源像素（导入后设 max_texture_size 无用）**；2K BC7 含 mip ≈ 5.3MB/张，四张全 2K×40 模块仅贴图 cooked 就 ≈ 850–900MB（超标）；1M 面 Nanite cooked ≈ 14MB/个 ×40 ≈ 550MB（超标）。

| 措施 | 效果 |
|---|---|
| **导入前**用脚本把源图缩到：BC 2K、N 2K、metallic+roughness 合并 ORM 1K（4 张→3 张） | 贴图 uasset ≈ 10–12MB/模块 |
| 网格用低模版本：源工具已能导出 retopo（现存 `01_RTP_10K.fbx` 即 1 万面版），全量模块申请 RTP 导出；拿不到就 Interchange 导入前离线减面到 10–20 万 | 网格 uasset ≈ 1–10MB/个 |
| **只打包演示参考图实际命中的模块子集**（约 20–25 个），其余在报告说明 | 总量再砍 40% |
| 打包脚本白名单收集（uproject/Config/Content/Plugins 含预编译 Binaries），显式排除 Saved/Intermediate/DDC/源资产目录/OBJ/GLB 源文件，产出后打印体积构成表 | 防手工拖拽污染 |

按"25 模块 × (12MB 贴图 + 5MB 网格) ≈ 425MB + 插件/地图/Binaries"估算，500MB **可达但紧**，必须每个里程碑跑一次 Size Map 审计。

---

## 六、离线演示策略（红队一票否决项之二）

评审机默认无外网、无 云端网关访问权限、无 Key —— pip、HTTP、鉴权三连挂。对策把**离线回放升级为一等公民**：

1. 依赖 vendor 进插件（见 §二#2），零 pip。
2. `cache/vlm_gateway/` 随工程附带（本来就是"产物级可复现"的设计，计入体积预算，约 70MB——必要时只留当前参考图命中的条目）；面板加 **Offline/Replay 开关**：开启时 GatewayClient 只许缓存命中，miss 即显式报错而不是发请求。
3. API 调用记录列表逐条标注 cache hit —— 评审看得见"哪一步用了什么 AI、离线时从缓存回放"，同时满足题面"AI 使用标注"。
4. 真实联网调用画面放进演示视频；报告写明"联网实跑（视频）+ 离线复现（评审机）"双口径。
5. 交付含匹配 5.5 精确小版本的 Win64 预编译 Binaries（评审机可能没有 MSVC）；README 写明首开 shader 编译等待预期。
6. 验收：在一台**无网冷机**上完整彩排"解压 → 打开 → 离线跑通管线"并计时。

## 七、API 显式化细节

- Settings 拆两个类：端点/模型名/超时/并发 → `defaultconfig`（入库无害，评审可见）；**Key 单独一类** `Config=EditorPerProjectUserSettings`（落 Saved/，绝不入库）。面板显示掩码 + reveal。
- 打包脚本加一步：grep Config/ 目录检出任何 key 形态字符串即中止打包。
- 面板 API 页签 = 端点/模型/Key 状态 + 逐调用记录（阶段、endpoint、模型、tokens、缓存命中、耗时），数据来源 vlm_gateway 的 stats + 调用日志（写 JSONL，随 run 目录留档）。

---

## 八、里程碑与降级路线（合计 ~7.5 天）

| 里程碑 | 内容 | 降级路线 |
|---|---|---|
| M0（0.5d）技术穿刺 | vendor wheels 进插件，编辑器内嵌 Python 跑通 s01（smoke 档、温缓存） | 若 cv2 在编辑器进程内 import 失败：评估以 numpy/PIL 替换 cv2 用点（s01 标定与 s03 修缝），或退回外部 python 子进程但把解释器随插件分发 |
| M1（2d）宿主迁移 | 后台线程 + queue + tick 收割；s05 进程内分帧；删子进程与 %TEMP% 旁路；CLI 薄包装保留；3.11 回归 | 保留现有子进程代码路径为 feature flag，可一键切回 |
| M2（2d）资产库 | 预导入脚本（缩图/ORM/GLB+Nanite）；s00 注册+打标；s04' 匹配；面板两页签 | 页签 UI 若时间紧：注册表校对退化为直接编辑 JSON + 只读展示 |
| M3（1d）氛围 s06 | VLM 估光（主光方向/色温/时段）→ DirectionalLight/SkyLight/HeightFog/PPV/Lumen；相机对位；参考图 vs 截图并排对比产出 | 估光失败时退回现有 compile_layout 灯光规则 |
| M4（1d）API 显式 + 离线 | Settings 拆分、调用记录、Offline 开关、缓存瘦身 | — |
| M5（1d）交付工程 | 打包脚本 + 体积审计 + 冷机彩排；顺手修 PIPELINE_REVIEW 里仍相关的 P0（compile_layout 静默丢弃上面板警告、run_summary 合并、--stages 校验） | — |

## 九、验证方案

- **每里程碑**：温缓存 `smoke.yaml` 端到端（零 API 成本）；并行/串行 `gen2d_manifest.json` 字节一致。
- **M2**：匹配报告审计——参考图 35 个对象的命中率、proxy 率、缩放 flag 分布；视口 spawn 预览逐模块过一遍单位。
- **M5**：无网冷机彩排（计时）+ Size Map 体积构成表 + grep Key 泄漏检查。
- 升级防御：全部 unreal.* 调用留在主线程（5.6 起强制），自检脚本 grep s01–s04 无 `import unreal`。
