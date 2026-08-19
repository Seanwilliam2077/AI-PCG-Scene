# 评审快速上手（5 分钟）

> 本工程自包含：预编译二进制、Python 依赖、AI 调用缓存全部随包附带。
> **评审机只需 Unreal Engine 5.5.4，无需安装 Python、无需网络、无需 API Key。**

## 1. 打开工程

双击 `NetEast_TA_Test.uproject`（引擎版本须为 **5.5.4**）。
含预编译二进制，不会要求编译；首次打开需等待 shader 编译，约 5–15 分钟（仅第一次）。

## 2. 开启离线回放（无网/无 Key 评审机必开）

**Edit → Project Settings → Plugins → AI Scene Builder → 勾选「离线回放」。**
开启后所有 AI 调用只回放随工程附带的缓存（`Plugins/AISceneBuilder/Python/cache/vlm_gateway/`），
缓存未命中会显式报错而不是发起网络请求——全流程可离线复现。

## 3. 运行管线

1. 菜单 **Tools → AI Scene Builder**（或视口左上 Modes 下拉）打开面板；
2. 面板顶部环境行应显示「✓ 内嵌 Python 就绪」与当前 API 端点/模型；
3. 参考图选择 `Plugins/AISceneBuilder/Python/refs/ref_original.jpg`；
4. 依次点击五个步骤卡片（1 场景理解 → 2 布局求解 → 3 二维素材 → 4 三维资产匹配 → 5 装配到关卡）。
   前四步在后台线程运行，编辑器可正常交互；第五步会短暂无响应（30–70 秒），属正常；
5. 装配完成后打开关卡 `/Game/Maps/GenScene` 查看结果。

## 4. 查验产物

运行产物在 `Plugins/AISceneBuilder/Python/output/run_<图哈希>/`：

| 文件 | 说明 |
|---|---|
| `comparison.png` / `ue_shot.png` | 参考图 vs 重建场景 对比图 / 对齐机位截图 |
| `api_calls.jsonl` | 逐次 AI 调用留痕（阶段/模型/缓存命中/耗时）——AI 使用标注的机器可读版 |
| `match_report.json` | 资产库匹配逐对象报告（命中方法/缩放/畸形旗） |
| `scene_manifest.json` | 装配回执（全部 actor 清单与唯一 id） |
| `pipeline_log.jsonl` / `run_summary.json` | 结构化日志与阶段统计 |

## 5. 人工介入点（可选体验）

- **手动标定**：在 run 目录放 `calib_manual.json`（fSpy 参数）后重跑步骤 1；
- **匹配改指**：编辑 run 目录 `match_overrides.json`（如 `{"by_instance": {"crt_monitor_45": "M12"}}`）后重跑步骤 4–5；
- **资产库核对**：`C:\...\AssetLib_processed\library_registry.json`（工程内为预导入结果）。

技术细节见随包的《技术方案报告.pdf》与 `Plugins/AISceneBuilder/README.md`。
