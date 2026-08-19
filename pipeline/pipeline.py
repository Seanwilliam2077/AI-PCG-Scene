#!/usr/bin/env python3
"""管线编排器：注册表驱动 + 文件指纹缓存 + JSONL 结构化日志。

    python pipeline.py --ref refs/server_room.jpg              # 全量
    python pipeline.py --ref ... --stages s01,s02              # 只跑指定阶段（短名或全名）
    python pipeline.py --ref ... --resume                      # 指纹命中即跳过
    python pipeline.py --ref ... --force s03                   # 强制重跑（下游自动失效）
    python pipeline.py --ref ... --skip-ue                     # 只编译 build_manifest，不起编辑器

阶段之间**只通过文件通信**——这是断点续跑与单阶段调试的前提。指纹 = 阶段代码 +
core/ 公共代码 + 输入文件内容 + 配置 + 关联资源（prompts/），任一变化即失效；
上游真跑过则下游一律失效，避免「旧资产配新布局」的脏缓存。

配置中的 unreal 段（编辑器路径等机器相关项）只参与 s05 的指纹——换一台机器改
UE 路径不应血洗 s01–s04 的缓存。

本文件也是 UE 内嵌 Python 的执行入口：ue/embedded_runner.py 直接调 run_pipeline()，
所以 s01–s04 的任何代码路径都不得 import unreal（tools/check_no_unreal.py 把关）。

注意 cache/vlm_gateway/ 是**产物性缓存**，不参与阶段指纹：它的语义是「同样的请求不必
重新花钱」，参与指纹会造成「命中缓存反而失效指纹」的悖论。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ProgressWriter:
    """把阶段进度原子写到一个 JSON 文件，供 UE 插件的 Ticker 轮询。

    必须原子写（tmp + os.replace）：读侧每 0.5s 醒一次，撞上半写文件会解析失败。
    C++ 侧对解析失败是「跳过本次 tick」，所以偶发也不致命——但原子写让它根本不发生。

    run_dir 也写进来：插件在启动前并不知道 run 目录叫什么（那是参考图哈希），
    与其让 C++ 重算一遍哈希，不如由这里回报——单一真相源。
    """

    def __init__(self, path: str | None, run_dir: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.run_dir = run_dir
        self.stage = ""
        self.t0 = time.time()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def bind_run_dir(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def _write(self, state: str, pct: float, msg: str, error: str | None = None) -> None:
        if not self.path:
            return
        payload = {
            "stage": self.stage,
            "state": state,
            "pct": round(max(0.0, min(1.0, pct)), 4),
            "msg": msg[:400],
            "elapsed": round(time.time() - self.t0, 1),
            "error": error,
            "run_dir": str(self.run_dir) if self.run_dir else None,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)          # 原子替换

    def start(self, stage: str) -> None:
        self.stage, self.t0 = stage, time.time()
        self._write("running", 0.0, "启动中…")

    def tick(self, pct: float, msg: str) -> None:
        self._write("running", pct, msg)

    def done(self, msg: str = "完成") -> None:
        self._write("done", 1.0, msg)

    def fail(self, error: str) -> None:
        self._write("failed", 0.0, "失败", error[:400])


@dataclass
class Stage:
    name: str
    inputs: list[str]
    outputs: list[str]
    version: str = "1"
    aux: tuple[str, ...] = field(default_factory=tuple)


REGISTRY = [
    Stage("s01_perceive", ["ref.jpg"], ["calib.json", "perception.json"],
          aux=("prompts",)),
    Stage("s02_layout", ["calib.json", "perception.json"],
          ["scene_layout.json", "cameras.json"]),
    Stage("s03_gen2d", ["scene_layout.json", "perception.json", "ref.jpg"],
          ["gen2d", "gen2d_manifest.json"], aux=("prompts",)),
    Stage("s04_gen3d", ["gen2d_manifest.json", "scene_layout.json"],
          ["assets_clean", "asset_registry.json"]),
    Stage("s05_build_scene", ["scene_layout.json", "asset_registry.json",
                              "gen2d_manifest.json"], ["build_manifest.json"],
          aux=("ue",)),
]

# 所有阶段都依赖 core/ 里的公共代码（vlm_gateway/solver/imaging/...）与受控词表，
# 改动即失效缓存。
GLOBAL_AUX = ("core", "taxonomy.yaml")

# 短名别名表：显式生成、精确匹配。--stages s0x 这类笔误会直接报错而不是静默跑空。
STAGE_ALIAS = {s.name.split("_")[0]: s.name for s in REGISTRY}


def resolve_stage_names(spec: str, ap: argparse.ArgumentParser | None = None) -> set[str] | None:
    """'all' → None；否则解析成全名集合。未知名字视为致命错误。"""
    if not spec or spec == "all":
        return None
    valid = {s.name for s in REGISTRY}
    out: set[str] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        name = STAGE_ALIAS.get(token, token)
        if name not in valid:
            msg = (f"未知阶段 '{token}'。可选：" +
                   ", ".join(f"{k}({v})" for k, v in STAGE_ALIAS.items()))
            if ap is not None:
                ap.error(msg)
            raise ValueError(msg)
        out.add(name)
    return out


def _cfg_fingerprint_bytes(cfg: dict, stage: Stage) -> bytes:
    """s05 之外的阶段不吃 unreal 段：改编辑器路径不应失效感知/布局/生成的缓存。

    vlm_gateway.offline 也剔除：离线回放开关只决定 miss 时报错还是发请求，不改变
    任何命中结果的字节——它参与指纹会导致"切一下开关全量重跑"。
    """
    view = {k: v for k, v in cfg.items() if k != "unreal"}
    if "vlm_gateway" in view:
        view["vlm_gateway"] = {k: v for k, v in view["vlm_gateway"].items() if k != "offline"}
    if stage.name == "s05_build_scene":
        view["unreal"] = cfg.get("unreal", {})
    return json.dumps(view, sort_keys=True, ensure_ascii=False).encode("utf-8")


def fingerprint(stage: Stage, run_dir: Path, cfg: dict, root: Path) -> str:
    h = hashlib.sha256(stage.version.encode())
    h.update(_cfg_fingerprint_bytes(cfg, stage))
    code = root / "stages" / f"{stage.name}.py"
    if code.is_file():
        h.update(code.read_bytes())                 # 改阶段代码自动失效缓存
    for aux in (*GLOBAL_AUX, *stage.aux):
        p = root / aux
        for f in sorted(p.rglob("*")) if p.is_dir() else ([p] if p.is_file() else []):
            if f.is_file() and f.suffix != ".pyc" and "__pycache__" not in f.parts:
                h.update(f.name.encode())
                h.update(f.read_bytes())
    for inp in stage.inputs:
        p = run_dir / inp
        files = sorted(p.rglob("*")) if p.is_dir() else ([p] if p.is_file() else [])
        for f in files:
            if f.is_file():
                h.update(f.name.encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def log_event(fp, **kv) -> None:
    kv["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    fp.write(json.dumps(kv, ensure_ascii=False) + "\n")
    fp.flush()


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def run_pipeline(ref: str | Path,
                 stages: str = "all",
                 resume: bool = False,
                 force: str = "",
                 skip_ue: bool = False,
                 config: str | Path | None = None,
                 progress_json: str = "",
                 config_overrides: dict | None = None,
                 root: Path | None = None) -> int:
    """管线主循环。CLI 与 UE 内嵌宿主共用的唯一入口。

    在 UE 内嵌 Python 里由 ue/embedded_runner.py 的后台线程调用——因此这里
    以及所有 stage 代码都必须是纯 Python（不得 import unreal）。
    config_overrides 供宿主注入运行期开关（如 vlm_gateway.offline），不落盘配置文件。
    """
    root = Path(root) if root else ROOT

    ref = Path(ref)
    if not ref.is_file():
        print(f"[FAIL] 参考图不存在：{ref}")
        return 2

    cfg_path = Path(config) if config else (root / "configs" / "pipeline.yaml")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if config_overrides:
        _deep_merge(cfg, config_overrides)

    run_id = hashlib.sha256(ref.read_bytes()).hexdigest()[:8]
    run_dir = root / "output" / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ref.jpg").write_bytes(ref.read_bytes())
    cache_dir = root / "cache" / "stages"
    cache_dir.mkdir(parents=True, exist_ok=True)

    selected = resolve_stage_names(stages)
    forced = resolve_stage_names(force) or set() if force else set()
    invalidated = False

    print(f"参考图 {ref.name} → run_{run_id}")
    print(f"输出目录 {run_dir}\n")

    progress = ProgressWriter(progress_json or None, run_dir)

    ctx = {"run_dir": run_dir, "root": root, "config": cfg, "skip_ue": skip_ue,
           "progress": progress}
    summary: dict[str, dict] = {}
    rc = 0

    with open(run_dir / "pipeline_log.jsonl", "a", encoding="utf-8") as logfp:
        for stage in REGISTRY:
            if selected and stage.name not in selected:
                continue
            fp = fingerprint(stage, run_dir, cfg, root)
            marker = cache_dir / f"{run_id}.{stage.name}.{fp}.ok"
            outputs_exist = all((run_dir / o).exists() for o in stage.outputs)
            if (resume and marker.exists() and outputs_exist
                    and stage.name not in forced
                    and not invalidated):
                print(f"[skip] {stage.name}（指纹命中 {fp}）")
                progress.start(stage.name)
                progress.done("指纹命中，跳过")
                log_event(logfp, stage=stage.name, event="skip_cached", fingerprint=fp)
                summary[stage.name] = {"skipped": True}
                continue

            invalidated = True
            progress.start(stage.name)
            print(f"[run ] {stage.name} …")
            log_event(logfp, stage=stage.name, event="start", fingerprint=fp)
            t0 = time.time()
            try:
                mod = importlib.import_module(f"stages.{stage.name}")
                result = mod.run(ctx) or {}
            except Exception as e:
                progress.fail(f"{stage.name}: {e}")
                log_event(logfp, stage=stage.name, event="fail", error=str(e),
                          traceback=traceback.format_exc()[-2000:])
                print(f"[FAIL] {stage.name}: {e}")
                traceback.print_exc()
                rc = 1
                break
            dt = round(time.time() - t0, 1)
            marker.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            log_event(logfp, stage=stage.name, event="done", seconds=dt, **_flat(result))
            progress.done(_brief(result) or "完成")
            summary[stage.name] = {"seconds": dt, **result}
            print(f"[ ok ] {stage.name}  {dt}s  {_brief(result)}\n")

    _write_summary(run_dir, summary, stages_spec=stages)
    print("=" * 60)
    print(f"{'完成' if rc == 0 else '中断'} → {run_dir}")
    for k, v in summary.items():
        print(f"  {k:18s} {_brief(v)}")
    return rc


def _write_summary(run_dir: Path, summary: dict, stages_spec: str) -> None:
    """合并写：本次没跑的阶段保留上次的记录，避免部分重跑冲掉历史统计。"""
    path = run_dir / "run_summary.json"
    merged: dict = {}
    if path.is_file():
        try:
            merged = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(merged, dict):
                merged = {}
        except (json.JSONDecodeError, OSError):
            merged = {}
    merged.update(summary)
    merged["_last_invocation"] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": stages_spec,
        "ran": sorted(summary.keys()),
    }
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 场景管线编排器")
    ap.add_argument("--ref", required=True, help="参考图路径")
    ap.add_argument("--stages", default="all", help="逗号分隔的阶段名（s01 短名或全名）")
    ap.add_argument("--resume", action="store_true", help="指纹命中则跳过")
    ap.add_argument("--force", default="", help="强制重跑的阶段")
    ap.add_argument("--skip-ue", action="store_true", help="s05 只编译不起编辑器")
    ap.add_argument("--config", default=str(ROOT / "configs" / "pipeline.yaml"))
    ap.add_argument("--progress-json", dest="progress_json", default="",
                    help="把阶段进度原子写到该文件（UE 插件用）")
    args = ap.parse_args()

    # 未知阶段名在进入主循环前就报错（退出码 2 + 可选名单）
    resolve_stage_names(args.stages, ap)
    if args.force:
        resolve_stage_names(args.force, ap)

    return run_pipeline(ref=args.ref, stages=args.stages, resume=args.resume,
                        force=args.force, skip_ue=args.skip_ue, config=args.config,
                        progress_json=args.progress_json)


def _flat(d: dict) -> dict:
    """JSONL 日志只收标量，嵌套的 stats 拍平成 a.b 形式。"""
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, (int, float, str, bool)):
                    out[f"{k}.{k2}"] = v2
        elif isinstance(v, (int, float, str, bool, list)):
            out[k] = v
    return out


def _brief(d: dict) -> str:
    parts = []
    for k, v in (d or {}).items():
        if isinstance(v, (int, float, str, bool)):
            parts.append(f"{k}={v}")
    return " ".join(parts[:7])


if __name__ == "__main__":
    sys.exit(main())
