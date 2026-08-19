# -*- coding: utf-8 -*-
"""UE 内嵌 Python 的管线宿主：让 s01–s04 在编辑器进程内的后台线程运行。

设计约束（缺一不可）：
  1. 本模块顶层只 import 标准库——它在编辑器启动语境里被 import，任何三方依赖
     都要等 ensure_paths() 把 vendor site-packages 插进 sys.path 之后再拿。
  2. 后台线程里跑的一切（pipeline.run_pipeline 与全部 stage/core 代码）必须是
     纯 Python，绝不 import unreal：unreal.* 是 GameThread-only（UE5.6 起跨线程
     直接硬报错，5.5 是未定义行为）。tools/check_no_unreal.py 在 CI 侧把关。
  3. 需要引擎 API 的两个半步（compile_manifest / assemble）由 C++ 在 GameThread
     上经 ExecPythonCommandEx 同步调用，不进后台线程。

进度通路沿用既有协议：ProgressWriter 原子写 _ui_progress.json，C++ Ticker 轮询。
后台线程的 stdout/stderr 重定向到 output/embedded_run.log，出问题看这个文件。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_worker: threading.Thread | None = None


def ensure_paths(vendor: str | None = None) -> None:
    """把管线根目录与（可选的）vendor site-packages 插进 sys.path。幂等。"""
    for p in ([vendor] if vendor else []) + [str(_ROOT)]:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def is_running() -> bool:
    return _worker is not None and _worker.is_alive()


def _purge_pipeline_modules() -> None:
    """卸掉上一轮加载的管线模块：编辑器进程长驻，改完代码不重启编辑器也要生效。

    只清 _ROOT 下的模块，vendor 的三方包（numpy/cv2）不动——它们重载昂贵且无必要。
    """
    root = str(_ROOT).lower()
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if name == __name__ or not f:
            continue
        try:
            if str(Path(f).resolve()).lower().startswith(root):
                del sys.modules[name]
        except (OSError, ValueError):
            continue


def _write_fail(progress: str, error: str) -> None:
    """兜底：在 ProgressWriter 尚未建立时也能把失败态送到 UI。"""
    if not progress:
        return
    try:
        p = Path(progress)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "stage": "", "state": "failed", "pct": 0.0, "msg": "失败",
            "elapsed": 0.0, "error": error[:400], "run_dir": None,
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def start(ref: str, stages: str = "all", config: str | None = None,
          progress: str = "", resume: bool = True, force: str = "",
          vendor: str | None = None, offline: bool = False) -> str:
    """启动后台线程跑管线，立刻返回（"started" / "busy"）。

    永远 skip_ue=True：s05 的编辑器内装配走 assemble()（GameThread），
    绝不允许后台线程再去拉起第二个编辑器进程。
    注意：线程无法被强杀——UI 的"取消"只是不再关注，线程会跑完当前阶段自然结束。
    """
    global _worker
    if is_running():
        return "busy"
    ensure_paths(vendor)

    def _run() -> None:
        log_path = _ROOT / "output" / "embedded_run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        old_out, old_err = sys.stdout, sys.stderr
        rc = -1
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                sys.stdout = sys.stderr = lf
                print(f"\n===== embedded run {time.strftime('%Y-%m-%d %H:%M:%S')} "
                      f"stages={stages} resume={resume} force={force!r} =====", flush=True)
                try:
                    _purge_pipeline_modules()
                    import pipeline as pl
                    overrides = {"vlm_gateway": {"offline": True}} if offline else None
                    rc = pl.run_pipeline(ref=ref, stages=stages, resume=resume,
                                         force=force, skip_ue=True, config=config,
                                         progress_json=progress,
                                         config_overrides=overrides, root=_ROOT)
                except Exception:
                    traceback.print_exc()
                    _write_fail(progress, traceback.format_exc()[-1000:])
                print(f"===== embedded run rc={rc} =====", flush=True)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    _worker = threading.Thread(target=_run, name="ASB-Pipeline", daemon=True)
    _worker.start()
    return "started"


def run_blocking(ref: str, stages: str = "all", config: str | None = None,
                 resume: bool = True, force: str = "", vendor: str | None = None) -> int:
    """同步跑管线（当前线程）。给 commandlet 冒烟测试与 CLI 调试用，编辑器 UI 别用。"""
    ensure_paths(vendor)
    _purge_pipeline_modules()
    import pipeline as pl
    return pl.run_pipeline(ref=ref, stages=stages, resume=resume, force=force,
                           skip_ue=True, config=config, root=_ROOT)


def _load_cfg(config: str | None) -> dict:
    import yaml
    cfg_path = Path(config) if config else _ROOT / "configs" / "pipeline.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def compile_manifest(run_dir: str, config: str | None = None) -> dict:
    """s05 的纯编译半步：三份 JSON → build_manifest.json。

    毫秒级确定性纯函数，由 C++ 在 GameThread 上同步调用，不需要线程。
    """
    ensure_paths()
    from ue.compile_layout import compile_layout
    rd = Path(run_dir)
    cfg = _load_cfg(config)
    layout = json.loads((rd / "scene_layout.json").read_text(encoding="utf-8"))
    registry = json.loads((rd / "asset_registry.json").read_text(encoding="utf-8"))
    gen2d = json.loads((rd / "gen2d_manifest.json").read_text(encoding="utf-8"))
    manifest = compile_layout(layout, registry, gen2d, cfg["unreal"]["content_dir"])
    out = rd / "build_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"assets": len(manifest["assets"]), "instances": len(manifest["instances"]),
            "decals": len(manifest["decals"]),
            "warnings": len(manifest.get("warnings", []))}


def assemble(run_dir: str, config: str | None = None, clean: bool = True) -> str:
    """编辑器内装配（GameThread，阻塞至完成）。替代旧的 %TEMP% 拷贝 + args.json 旁路。

    参数经 ASB_ARGS_JSON 环境变量指向 run 目录里的 args 文件——不经命令行，
    路径含空格也无所谓；共享临时目录的并发竞态与陈旧参数问题一并消失。
    """
    ensure_paths()
    import runpy
    rd = Path(run_dir)
    cfg = _load_cfg(config)
    args = {
        "manifest": str(rd / "build_manifest.json"),
        "manifest_out": str(rd / "scene_manifest.json"),
        "level": cfg["unreal"]["level"],
        "clean": bool(clean),
    }
    args_path = rd / "assemble_args.json"
    args_path.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
    os.environ["ASB_ARGS_JSON"] = str(args_path)
    try:
        runpy.run_path(str(_ROOT / "ue" / "build_scene.py"), run_name="__main__")
    finally:
        os.environ.pop("ASB_ARGS_JSON", None)
    return str(rd / "scene_manifest.json")
