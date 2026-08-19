# AI Scene Builder — UE5 编辑器模式插件

一张参考图 → UE5 里摆好、打好光、调好色的关卡。五步向导，每步可见、可单独重跑、可人工干预。

## 安装

插件已在 `AISceneBuilderDemo/Plugins/` 下，编辑器启动时自动发现，`.uproject` 无需改动。
换到别的工程只要整个 `AISceneBuilder/` 目录拷过去即可——管线 Python 副本、prompt 模板、
云端 VLM 网关 请求缓存都在插件内，是自包含的。

打开方式：**Modes 工具栏 → AI Scene Builder**，或菜单 **Tools → AI Scene Builder**。

## 环境要求（v2：零外部 Python）

| 项 | 说明 |
|---|---|
| UE 5.5 | 已启用 PythonScriptPlugin / EditorScriptingUtilities / PCG。**整条管线跑在编辑器内嵌 Python 3.11 里**，评审机不需要装任何 Python |
| vendor 依赖 | `numpy` / `opencv-python` / `PyYAML` 已解包在插件 `Content/Python/Win64/Lib/site-packages`；缺失时面板「安装依赖」按钮用**引擎自带 Python** 补齐（需网络） |
| 云端 VLM 网关 API Key | 联网实跑时必需；**离线回放模式下不需要**（见下） |
| 资产库 | `AssetLib_processed/`（40 个预生成模块 + `library_registry.json`），已预导入为 `/Game/AutoScene/AssetLib` 下的 uasset |

**离线回放**：Project Settings → AI Scene Builder → 「离线回放」。开启后 AI 调用
只允许命中随插件附带的 `Python/cache/vlm_gateway/`，缓存 miss 显式报错而不发请求——
评审机没有云端网关访问权限也能整条复跑。逐调用记录（阶段/模型/缓存命中/耗时）
写在 `output/run_*/api_calls.jsonl`，面板环境行也会显示当前 API 端点与模型。

### API Key 配置

三级回退，命中即止。**Key 永远不写进插件配置文件**：

1. 环境变量 `VLM_API_KEY` / `GEN3D_API_KEY`（每台机器/CI 各自覆盖，推荐）
2. `Config/DefaultEditor.ini`：
   ```ini
   [AISceneBuilder.Gateway]
   ApiKey=<vlm_gateway key>
   Gen3DApiKey=<gen3d key>
   ```
3. 直读工程的 `Config/DefaultEditor.ini`（新克隆的仓库可能残留旧的生成态 Editor.ini，这一级兜底）

## 五个步骤（v2）

| 步骤 | 做什么 | 在哪跑 | 典型耗时 |
|---|---|---|---|
| 1 场景理解 | OpenCV 消失点标定（无 AI）+ 云端视觉模型 检测/分割 | 内嵌 Python 后台线程 | 1–3 min（温缓存秒级） |
| 2 布局求解 | 地面射线法分层求解，纯 numpy | 内嵌 Python 后台线程 | 秒级 |
| 3 二维素材生成 | 云端视觉模型：无缝材质 / 海报文字 / **氛围估计**（库模式下 hero/turnaround 关闭） | 内嵌 Python 后台线程 | 2–8 min（温缓存秒级） |
| 4 三维资产匹配 | **资产库四级瀑布匹配**（未命中降级代理盒），产出 match_report.json | 内嵌 Python 后台线程 | 秒级 |
| 5 装配到关卡 | 库资产 load_asset 快路径、摆放、材质、灯光、贴花、相机 | **编辑器内 GameThread** | 10–60 s |

前四步跑在**编辑器内嵌 Python 的后台线程**里（纯 Python，不碰 unreal.*），编辑器
全程可交互；进度仍靠轮询 `output/_ui_progress.json` 更新。注意：Python 线程无法
被强杀，「取消」只是 UI 不再关注，线程会跑完当前阶段自然结束（重复点「运行」会被
busy 自保挡住）。

第五步必须在编辑器进程内跑——它要操作关卡和资产。这一步会让编辑器短暂无响应，属正常。

### 每步的人工干预

- **步骤 1**：「查看标定图」看线段分族与地平线是否合理；标定不可信时点「手动标定」，
  会生成 `calib_manual.json` 模板，填好 hfov/pitch/机位高后重跑步骤 2 即可生效
  （该文件存在时**无条件覆盖**自动标定，是全管线唯一的强人工介入点）。
- **步骤 2**：「查看布局图」是俯视平面 + 立面，两秒就能看出桌上物是否真在桌面高度、
  海报是否贴在墙上；也可以直接「编辑 scene_layout.json」改完再往下走。
- **步骤 3**：打开素材目录逐张看；清单里有每套材质的接缝分数与跳过原因。
- **步骤 4**：资产清单标明每件走的是文生 3D 还是代理盒、有没有降级。
- **步骤 5**：装配回执给 actor 数与错误数；「查看对齐机位截图」可与参考图对照。

分步向导有门控：上一步没成功，下一步的按钮是灰的。重开编辑器后各步状态会从
`output/run_*/` 的产物自动恢复，可以接着上次继续。

## 目录

```
AISceneBuilder/
├── AISceneBuilder.uplugin
├── Python/                   管线自包含副本
│   ├── pipeline.py           编排器（--progress-json 供插件轮询）
│   ├── core/                 vlm_gateway / calib_vp / solver / imaging / providers3d
│   ├── stages/               s01–s05
│   ├── ue/                   compile_layout.py（纯标准库）+ build_scene.py（编辑器内）
│   ├── prompts/  configs/  tools/
│   └── cache/vlm_gateway/          请求哈希缓存 —— 产物级可复现的载体，勿删
└── Source/AISceneBuilderEditor/
    ├── AISceneBuilderEdMode         编辑器模式（UBaseLegacyWidgetEdMode）
    ├── AISceneBuilderToolkit        五张步骤卡片的面板
    ├── PipelineRunner               模块级单例：子进程 + Ticker 轮询 + 取消 + 通知
    ├── PipelineNotification         右下角非模态进度通知（INotificationWidget）
    ├── AISceneBuilderSettings       路径设置 + API Key 三级回退
    └── PreviewImage                 磁盘图片 → Slate 预览
```

## 实现上的几个硬约束（改代码前请先读）

- **管线代码只在 `C:\AI Pipeline Test\pipeline`（canonical）修改**，本插件的
  `Python/` 目录是 `tools/sync_plugin.py` 的同步产物（见 `Python/SYNCED_FROM.md`）。
- **`IPythonScriptPlugin::ExecPythonCommandEx` 在 GameThread 同步执行。**
  所以长任务的启动语句只做一件事：spawn 一个纯 Python 后台线程立即返回
  （`ue/embedded_runner.py`）。s01–s04 的一切代码**禁止 import unreal**——
  unreal.* 是 GameThread-only，UE5.6 起跨线程直接硬报错（`tools/check_no_unreal.py` 把关）。
- **UE 的 `-ExecutePythonScript` 按空格切「脚本+参数」。** 编辑器内链已改用
  ExecuteStatement + `ASB_ARGS_JSON` 环境变量传参（无空格问题、无共享临时目录）；
  命令行冷启动链仍走 `mkdtemp` 唯一暂存目录 + `args.json` 旁路。
- **`ExecPythonCommandEx` 返回 true 不代表脚本没抛异常。** 必须同时检查
  `CommandResult` 与 `LogOutput`，并嗅探 Traceback 之类的痕迹。
- **UE5 的 `UEdMode` 子类由 `UAssetEditorSubsystem` 自动扫描注册**，不需要
  `FEditorModeRegistry::RegisterMode`（那是旧式 `FEdMode` 才要的）。
- **模块 Shutdown 里反激活模式要判 `IsEngineExitRequested()`**，否则引擎退出过程中
  会因为子系统已关闭而 fatal。
- 预览纹理必须 `AddToRoot()` 保活（Slate brush 只持弱引用），析构时 `RemoveFromRoot()`。
- **进度通知里的属性委托只能捕获 `TWeakPtr<FPipelineRunner>`，不能捕获 Toolkit。**
  通知由 Slate 的通知管理器持有，寿命长过面板；捕获面板就是野指针。
  同理，Toolkit 析构时只退订委托、**不要** `Cancel()`——那会把切个模式当成中止任务。
- 长任务别用 `FScopedSlowTask`：它是模态的，会把编辑器锁住几十分钟。

管线本身的坑（材质连线输出名、截图前等着色器编译、Rotator 参数顺序等）见
`Python/README.md`。
