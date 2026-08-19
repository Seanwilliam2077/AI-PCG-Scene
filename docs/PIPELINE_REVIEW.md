# AI 场景自动化管线 —— 现状分析与改进空间报告

> 审阅日期：2026-08-10。范围：`C:\AI Pipeline Test\pipeline`（主管线）、`NetEast_TA_Test\Plugins\AISceneBuilder`（UE5.5 插件及其内嵌管线副本）。
> 所有结论均基于对源码的直接阅读与两次实跑产物（`output\run_a5d94e7d`、`output\run_fd6e434f`）的核对，每条问题附 `文件:行号`。
> 本报告为纯分析，未修改任何代码。

---

## TL;DR

这条管线的**骨架设计是健康的**：注册表驱动的五阶段编排、阶段间纯文件通信、三层缓存、失败逐项容错，都是对的选择，而且有完整的实跑证据（端到端 8–12 分钟出 74 个 actor 的 UE 场景）。但存在三类实质问题：

1. **12 项正确性 bug**，其中 4 项会静默产出错误结果（人工标定覆盖失效、UE 跨 run 复用僵尸资产、compile_layout 静默丢掉 60% 的对象、插件版 s03 的 NameError 回归）；
2. **性能全靠串行硬扛**：s03 图像生成占总时长 90%+（实测 473–740s），其内部三组循环完全独立却逐个调用，并行化可提速约 5 倍；
3. **双份代码已分叉**：插件内嵌的管线副本与主管线有 2 个文件硬分叉且互不兼容（插件 C++ 无条件传 `--progress-json`，而主管线不认识这个参数），继续各自演化会越走越远。

---

## 一、管线如何运行

### 1.1 数据流总览

```
参考图 (refs/*.jpg)
   │  run_id = sha256(图片字节)[:8] → output/run_<id>/
   ▼
┌─ s01_perceive ──── 云端 VLM 网关(Gemini) 场景语义/检测 + 本地消失点标定(LSD/RANSAC)
│     产出 calib.json / perception.json（可被 calib_manual.json 人工覆盖）
├─ s02_layout ────── LayoutSolver 反投影 → scene_layout.json / cameras.json
├─ s03_gen2d ─────── 逐个生成 hero 视图(≤24次调用) / 可平铺材质(含修缝循环) / 贴花
│     产出 gen2d/ + gen2d_manifest.json          ← 耗时大头：473–740s
├─ s04_gen3d ─────── 云端 3D(图生/文生) 或本地 proxy 盒体 → assets_clean/ + asset_registry.json
└─ s05_build_scene ─ compile_layout() 纯函数编译 build_manifest.json
      → 经 %TEMP%\ue_autoscene\args.json 旁路
      → UnrealEditor-Cmd.exe -ExecutePythonScript 冷启动装配
      → UE 内 build_scene.py 导入资产/建材质/摆 actor → 回执 scene_manifest.json
```

关键设计决策（均写在代码注释/docstring 里，有意为之）：

- **阶段间只走文件，不走内存**（`pipeline.py:10-12`）——这是断点续跑与单阶段调试的前提。`ctx` 只带 `run_dir/root/config/skip_ue` 四个键（`pipeline.py:121`）。
- **注册表驱动**：`pipeline.py:45-57` 的 `REGISTRY` 声明每个阶段的输入/输出/关联资源，`importlib` 动态加载，阶段只需暴露 `run(ctx)`。
- **三层缓存**：
  - 阶段指纹（`pipeline.py:60-79`）：代码 + 配置 + 输入文件内容 → `cache/stages/<run_id>.<stage>.<fp>.ok`，`--resume` 命中即跳过；上游真跑过则下游一律失效（`pipeline.py:141`），避免"旧资产配新布局"。
  - 云端 VLM 网关 请求哈希（`vlm_gateway.py:151-156`）：`sha256(model+params+prompt+图片字节)` → `cache/vlm_gateway/`（现存 130 文件 68.85MB，随交付提交，承诺"复跑零成本"）。
  - README 宣称的第三层 `gen3d_state.json` 云任务断点（`README.md:73`）**实际不存在**——全仓库无任何代码引用。
- **UE 调用链有两条**：CLI 链 `UnrealEditor-Cmd -ExecutePythonScript`（`s05_build_scene.py:72-77`，冷启动 ~32s）与插件编辑器内链 `IPythonScriptPlugin::ExecPythonCommandEx`（`AISceneBuilderToolkit_Env.cpp:257-262`）。两条链共用同一个 `build_scene.py` 与 `%TEMP%\ue_autoscene\args.json` 传参旁路（规避 UE 命令行按空格截断路径的问题）。插件驱动 s01–s04 时另起 Python 子进程 + 每 0.5s 轮询进度文件（`PipelineRunner.cpp:244-269`），理由是 `ExecPythonCommandEx` 在 GameThread 同步执行会把编辑器冻死 20–40 分钟。
- **外部 AI 依赖单一入口**：唯一 AI 入口是 OpenAI 兼容的云端 VLM 网关（`vlm_gateway.py:1-14`），转发 `gemini-2.5-flash`（视觉）与 `gemini-3-pro-image`（图像生成）；3D 侧有 4 个 provider（云端文生 3D / 云端图生 3D / 云端减面 / 本地 proxy），凭证缺失时自动降级 proxy（`providers3d.py:427-459`）。
- **凭证管理**：无硬编码密钥；全部走环境变量（`VLM_API_KEY`、`GEN3D_*`、`GEN3D_API_KEY`），带 ini 回退链。

### 1.2 实测运行数据（run_fd6e434f，参考图 ref_original.jpg）

| 阶段 | 耗时 | 产出 |
|---|---|---|
| s01_perceive | ~1 分钟 | 35 对象、calib 置信度 0.12（退回 60° 先验） |
| s02_layout | 秒级 | scene_layout 35 对象、cameras.json |
| **s03_gen2d** | **473.5s** | hero=7 materials=5 decals=3 screens=0 **skipped=5** |
| s04_gen3d | 1.4s | 7 资产全部 proxy 盒体（无云端凭证） |
| s05_build_scene | 32.3s | UE 装配 74 actors，errors=0 |

值得注意的实跑细节：

- 修缝质检 5 套材质里 **3 套没过线**（阈值 1.25，实测 wall 1.77 / ceiling 1.53 / wall_shelf_55 2.07，全部 best-effort 放行）。
- 无云端 3D 凭证时，`assets_clean\*.obj` 全部是 555 字节的代理盒——场景里没有一个真网格，`import_mesh()` 路径从未被真正执行过。
- 标定数据自相矛盾：`calib.json` 说 confidence=0.12 / hfov=60°，`cameras.json` 说 calib_confidence=0.816 / hfov=95°。这是 s02 的先验一致性反解改写了相机参数，但两个文件没有对齐口径，审阅产物时极易误读。
- `README.md` 的实测表描述的是更早那次 run（24 actors / 2 资产），与当前产物（74 actors / 7 资产）已不符。

---

## 二、问题清单

### 2.1 正确性 bug（会产出错误结果或违背文档承诺）

**★ C1. fSpy 人工标定覆盖基本失效** — `stages\s01_perceive.py:226-234`
README 把 `calib_manual.json` 宣传为"全管线唯一的强人工介入点"，但覆盖用的是裸 `setattr`：改 `hfov_deg` 不会重算 `fx/fy`（`solver.py:83-85` 构造 K_inv 用的是 fx/fy），改 `pitch/roll` 不会重算 `up_cam`（`solver.py:86,90` 用的正是它）。四个示例字段里**只有 `cam_height_m` 真正生效**。更糟的是强设 `confidence=1.0`（`:233`）会让 s02 跳过先验一致性反解与尺度校准（`s02_layout.py:360-373`）——用户手标得越认真，结果反而越糟。

**★ C2. UE 资产按名去重，跨 run 静默复用僵尸资产** — `ue\build_scene.py:82-83,110-112,129-131,158-160`
四处导入都是"同名资产已存在 → 直接 `load_asset` 返回"，而命名不含 run 标识（`T_wall_BC`、`SM_{aid}`、`MI_{aid}`）。换参考图重跑，只要 id 撞名，UE 里挂的还是上一轮的贴图，`replace_existing=True` 根本走不到。实证：`Content\AutoScene\Textures` 里混着 8/6 上午（run_a5d94e7d）与下午（run_fd6e434f）两代资产，`T_Decal_poster_19/20/22/28` 等均为当前 manifest 里不存在的僵尸。

**★ C3. compile_layout 静默丢弃 60% 的对象** — `ue\compile_layout.py:107-108`
`if aid not in reg_assets: continue`——asset_registry 只收 s03 产出的 hero（受 `max_hero_assets: 12` 钳制），实跑 35 个布局对象**被无声丢掉 21 个**（椅子、立柱、显示器……），无 warning、不进 errors、回执里看不出来。`:97-106` 对无贴图的 decal 同样静默跳过（7 丢 4）。

**★ C4. 插件版 s03 的 fork 回归：except 块引用未绑定变量** — 插件 `Python\stages\s03_gen2d.py:200`
`bump(f"{bucket[:-1]} …")` 里的 `bucket` 在 try 块内第 194 行才绑定。第一个 decal 在生成调用前/中抛异常 → `NameError` 逃出 except → **整个 s03 崩掉**，而这个 try/except 的设计意图恰是"单件失败不阻塞整批"。主管线版本没有这行，纯属分叉引入。

**C5. 云端 VLM 网关 缓存投毒** — `core\vlm_gateway.py:216-220` + `:223-241`
`chat()` 无条件把响应写入缓存，不管是否合法 JSON。`chat_json()` 命中坏缓存 → 解析失败 → 走修复分支（`use_cache=False`）→ **每次复跑都真调一次 API**，直接违反 README"复跑字节一致、零成本"的承诺。

**C6. 阶段指纹不覆盖 core/** — `pipeline.py:60-79`
指纹只 hash `stages\<name>.py` + 声明的 aux 目录（prompts/、ue/），`core\` 五个模块（vlm_gateway/solver/calib_vp/imaging/providers3d）不在任何阶段的 aux 里。改了 solver 的求解逻辑后 `--resume`，静默复用旧结果——"改了代码但结果没变"的调试陷阱。

**C7. 修缝第 2 轮起绕过缓存** — `stages\s03_gen2d.py:229`
`use_cache=(rounds == 1)`，`seam_max_rounds: 3` 意味着每次复跑最多多花 2 次图像生成且结果不可复现。实测 5 套材质里 3 套走到多轮。

**C8. UE 子进程超时未捕获** — `stages\s05_build_scene.py:76-80`
`subprocess.run(timeout=2400)` 抛 `TimeoutExpired` 时，其后的日志落盘不执行 → `ue_build.log` 全丢，UE 子进程还可能残留。

**C9. 四面墙共用同一个 tag** — `ue\build_scene.py:286`
`GEN_shell_wall` × 4，回执 `scene_manifest.json` 里 74 个 actor 只有 71 个唯一 id——直接否定了代码注释里"闭环增量修补的寻址依据"这一设计意图。

**C10. `--stages`/`--force` 前缀匹配且无校验** — `pipeline.py:128,134`
`--stages s0` 匹配全部 5 个阶段；`--stages s06`（笔误）静默什么都不跑还写出一个空 `run_summary.json`。

**C11. run_summary.json 覆盖写** — `pipeline.py:161-162`
只写本次调用跑过的阶段。实证：`run_fd6e434f\run_summary.json` 只剩 s05 一项，前四阶段的耗时/统计全丢。

**C12. 两个埋雷** — `core\vlm_gateway.py`
① 缓存键不含 endpoint URL（`:151-156`）：切换网关会静默命中旧网关的缓存；② `chat_json` 修复调用 `self.chat(..., use_cache=False, **kw)`（`:237`）：调用方一旦传 `use_cache=` 即 `TypeError`。

### 2.2 性能（实测瓶颈 + 结构性串行）

全仓库**零并发原语**（无 threading / futures / asyncio / multiprocessing）。

| 瓶颈 | 位置 | 量级 |
|---|---|---|
| **s03 逐个调用生成 API** | `s03_gen2d.py:82-124`(hero ≤24 次) `:127-152`(每面材质 ≤4 次) `:155-182`(decal) | **473–740s**，占总时长 90%+；各调用完全独立 |
| 云端 3D 阻塞轮询 | `providers3d.py:249-267,398-423`（`sleep(5)` 循环，超时 900/1200s） | 一旦配上凭证，12 资产最坏 4 小时，且互相零依赖 |
| 云端 VLM 网关 节流是实例字段 | `vlm_gateway.py:161-163`（`self._last_call`，min_interval 1s） | 并行化前必须改成带锁的时隙调度 |
| calib_vp 重复检测 | `calibrate()` 在 `calib_vp.py:144` 跑一次 LSD，`draw_debug()` 在 `:277` **再跑一次完全相同的** | 4K 图上纯浪费 |
| FOV 网格搜索纯 Python 三重循环 | `s02_layout.py:294-320`（210 组，仅低置信路径触发） | 秒级，优先级低 |

**收益估算**：s03 三组循环用 4–8 线程并行 + 全局 1 req/s 节流，墙钟时间可从 ~8 分钟压到 ~2 分钟，API 成本零变化。这是全管线唯一的"改动小、收益大"的性能点。

### 2.3 架构 / 维护性

**★ A1. 双份管线代码已分叉且互不兼容**
插件 `Python\` 是主管线的完整拷贝：19 个代码文件里 17 份逐字节相同，`pipeline.py` 与 `stages\s03_gen2d.py` 硬分叉（插件版加了 ProgressWriter + `--progress-json` + `bump()` 进度打点）。后果：插件 C++（`PipelineRunner.cpp:238-242`）**无条件传 `--progress-json`**，用户一旦在设置里把管线目录指回 `C:\AI Pipeline Test\pipeline`，argparse 直接 `unrecognized arguments` 退出码 2。此外插件目录里还复制了整份 `output\`（含指向另一 checkout 绝对路径的 build_manifest，"自包含"承诺并不成立）和 68.85MB 的 `cache\vlm_gateway`。

**A2. 4 套地/墙/顶分类器并行存在**：`s01_perceive.py:67-78`、`s02_layout.py:28-54`、`s02_layout.py:57-66`、`compile_layout.py:185-187`，词表互不一致；`s03_gen2d.py:23` 还跨阶段导入私有 `_surface_kind`——代码注释自己承认"必须与 s02 同一套规则"，正说明它该被提到 `core\`。

**A3. `%TEMP%\ue_autoscene` 固定共享目录**（`s05_build_scene.py:61-70`、`AISceneBuilderToolkit_Env.cpp:230-253`）：两次运行并发会互相覆盖 `args.json`；写入失败时下游静默读到上一轮的陈旧参数；而且"临时目录无空格"这个前提本身不成立（用户名带空格即失效）——当前能跑只是因为 Windows 恰好给了 8.3 短名（日志里的 `user`）。

**A4. 凭证回退链两套且互不一致**：Python 侧回退到硬编码的机外绝对路径 `C:\LegacyProject\Config\DefaultEditor.ini` 的 `[LegacyProject.Gateway]`（`vlm_gateway.py:33-35`）；插件 C++ 读的是 `[AISceneBuilder.Gateway]`（`AISceneBuilderSettings.cpp:13-15`）。插件面板显示"Key 已配置"不代表 Python 子进程读得到。

**A5. 配置管理**：无 schema 校验（缺字段即裸 `KeyError`，`s01_perceive.py:132-135`）；`pipeline.yaml` 与 `smoke.yaml` 靠散落的 `.get(...)` 兜底隐式同步，极易漂移；`unreal` 段硬编码本机绝对路径**且整个配置字节参与所有阶段的指纹**（`pipeline.py:62`）——改一个 UE 安装路径会血洗 s01–s04 全部缓存。

**A6. 其他**：`Hy3DLowPolyProvider` 整类不可达（`make_provider` 从不选它，`providers3d.py:271-321`）；`_anchor_ratios` 死代码（`s02_layout.py:257-271`）；`requirements.txt` 声明的 Pillow 从未被导入；`PCG_Desk` tag 打了但 Content 下没有任何 PCG 资产消费它——断头路。

### 2.4 工程化缺口

- **零 logging**：全仓库只有 `print()`；`core\` 五个模块完全静默——云端 VLM 网关 重试不可见、缓存命中不可见、云端 3D 轮询 15–20 分钟毫无输出（看起来像卡死）。阶段内部的 warn（如 s03 的 skipped 项）只 print 不进 `pipeline_log.jsonl`，事后无法结构化统计失败率。
- **零测试**：`compile_layout.py` docstring 自称"确定性纯函数、可单测"，但没有任何测试；也无 lint / 类型检查配置（代码有完整 type hints 却无人校验）。
- **异常信息被截断丢弃**：多处 `str(e)[:120~200]`，traceback 全丢，事后排查只剩半句话。
- **文档漂移**：README 的 `gen3d_state.json`（不存在）、"凭证一律走环境变量不入库"（yaml 里留有明文密钥字段且 configs/ 不在 .gitignore）、实测数据表（描述的是上一次 run）。
- **产物卫生**：`assets_raw\` 与 `assets_clean\` 在 proxy 后端下逐字节重复；`ref_x2.png`（2.8MB）每次重算重写；修缝中间产物 `*_offset_*.png` 不清理。

---

## 三、改进路线图（按风险/收益分层，供将来采纳）

> 分层原则：先收敛分叉（否则每个修复都要做两遍），再修正确性，再提速，最后工程化。每层独立可交付、可回滚。

| 层 | 内容 | 工作量 | 关键点 |
|---|---|---|---|
| **第 0 层：fork 收敛** | 把插件版的 ProgressWriter/`--progress-json`/`bump()` 合入主管线（无参数时空操作，CLI 行为零变化）；合并时顺手修 C4（`bucket` 上移到 try 外）；新增 `tools\sync_plugin.py` 白名单同步脚本（排除 output/cache），插件副本从此是同步产物而非第二真相源 | 0.5 天 | 保住插件 README 的"自包含交付"承诺，同时消灭双头维护 |
| **第 1 层：正确性** | C1 新增 `apply_manual_override()` 重算 fx/fy/up_cam、不再强设 confidence=1.0；C2 资产改内容哈希命名（`T_wall_<hash8>_BC`）+ 装配前清理未引用资产；C3 丢弃对象收进 `manifest["warnings"]` 并计数上报；C5 修复成功后回写主缓存槽；C6 指纹加 `GLOBAL_AUX=("core",)`；C7 修缝改 `use_cache=True`（各轮输入字节不同，键天然不同）；C8 改 Popen+communicate，超时 taskkill 进程树并保留部分日志；C9 墙 tag 按朝向唯一化；C10 显式别名表+精确匹配，未知名报错；C11 读旧文件合并写；C12 缓存键加非默认 URL、`kw.pop("use_cache")` | 2 天 | 指纹算法变更会一次性失效全部缓存——温缓存下重跑代价≈0，属预期 |
| **第 2 层：s03 并发** | GatewayClient 节流改锁保护的时隙调度（睡在锁外）、缓存写改 tmp+`os.replace` 原子化；s03 三组循环各抽纯 worker 函数（try/except 隔离搬进 worker），`ThreadPoolExecutor(workers=6)` 提交、主线程按原始序号排序收割（保证 manifest 字节可复现、`bump()` 进度单调）；`gen2d.workers: 1` 即回退串行；s04 云端轮询同模式并行（当前无凭证属休眠路径）；calib_vp 的 segs 存进 CalibResult 供 draw_debug 复用 | 1.5 天 | 验证锚点：温缓存下并行/串行的 `gen2d_manifest.json` 必须字节一致 |
| **第 3 层：架构统一** | 新增 `core\taxonomy.py` 收编 4 套分类器（合并词表需温缓存 diff 把关归类漂移）；`GatewayClient.from_config()` 工厂；ini 回退链与 C++ 侧对齐 `[AISceneBuilder.Gateway]`；`%TEMP%` 旁路改 `mkdtemp` 唯一目录 + 短路径防御（唯一需动 C++ 的小改动） | 1.5 天 | — |
| **第 4 层：工程化** | 标准库 logging（Console INFO + 每 run 文件 DEBUG，WARNING+ 镜像进 JSONL）；手写轻量配置校验器（不引 pydantic，保持依赖极简）+ `extends:` 防两份 yaml 漂移；`unreal` 段移出全局指纹（仅 s05 拼入）；pytest 基线覆盖 compile_layout / solver 纯函数 / 指纹逻辑 / vlm_gateway 缓存语义 | 1.5 天 | — |

**贯穿性验证配方**（`cache\vlm_gateway` 已温，全部零 API 成本）：

```bash
set VLM_API_KEY=... && python pipeline.py --ref refs/server_room.jpg --config configs/smoke.yaml --skip-ue --resume
```

全链路 UE 验收（需 `C:/Program Files/Epic Games/UE_5.5`）：去掉 `--skip-ue` 跑一次，核对 `scene_manifest.json` 回执 actor 集与 `Content\AutoScene` 资产无僵尸。

---

## 附：目录里与管线无关的内容

`C:\AI Pipeline Test` 根目录混入了另一个独立项目：`cgai\`（GaTech CS 8803 课程 starter）、`capture\`（该课程期末作业的 puppeteer 截帧装置）、`submission\`（作业交付物）、`cgai-final-zxiao316.zip` 均与管线无关。`Asset\` 是 8/10 经浏览器下载的一批贴图 zip（6 个 0 字节 + `.crswap` 残留，下载未完成），未被任何代码引用。根目录无 README、无运行脚本、非 git 仓库——若要长期维护，建议把管线独立成仓并补一份根级说明。
