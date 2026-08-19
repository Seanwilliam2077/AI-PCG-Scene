"""s05 装配：编译 build_manifest → 调 UnrealEditor-Cmd 执行编辑器内 Python。

UE 官方给了两条命令行入口，这里选 `-ExecutePythonScript`：
  -run=pythonscript   更快但环境不完整（不加载关卡/资产），装配用不了；
  -ExecutePythonScript 完整编辑器初始化、跑完自动退出，正是我们要的。

参数串的引号只能包住 -ExecutePythonScript 的**值**。若用 list 形式交给
subprocess，Windows 的 list2cmdline 会把整个 token 连同键名一起加引号，UE 的
FParse 取值时会在第一个空格截断，参数就丢了。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ue.compile_layout import compile_layout


def run(ctx: dict) -> dict:
    run_dir: Path = ctx["run_dir"]
    root: Path = ctx["root"]
    cfg: dict = ctx["config"]
    ue_cfg = cfg["unreal"]

    # ---- 1) 编译装配指令（确定性纯函数，UE 外执行）----
    layout = json.loads((run_dir / "scene_layout.json").read_text(encoding="utf-8"))
    registry = json.loads((run_dir / "asset_registry.json").read_text(encoding="utf-8"))
    gen2d = json.loads((run_dir / "gen2d_manifest.json").read_text(encoding="utf-8"))
    manifest = compile_layout(layout, registry, gen2d, ue_cfg["content_dir"])
    manifest_path = run_dir / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"  [compile] assets={len(manifest['assets'])} "
          f"instances={len(manifest['instances'])} decals={len(manifest['decals'])}")

    if ctx.get("skip_ue"):
        return {"compiled": True, "ue": "skipped",
                "instances": len(manifest["instances"])}

    editor = Path(ue_cfg["editor_cmd"])
    uproject = Path(ue_cfg["uproject"])
    if not editor.is_file():
        raise RuntimeError(f"找不到 UnrealEditor-Cmd：{editor}")
    if not uproject.is_file():
        raise RuntimeError(f"找不到 uproject：{uproject}")

    # ---- 2) 调 UE ----
    scene_manifest = run_dir / "scene_manifest.json"
    if scene_manifest.exists():
        scene_manifest.unlink()

    # UE 的 -ExecutePythonScript 把值按空格切成「脚本 + 参数」，所以**脚本路径不能有
    # 空格**（本仓库就在 "C:\AI Pipeline Test\..." 下，直接传会被切坏、报
    # Could not load Python file）。对策：把脚本暂存到无空格的临时目录，参数改用
    # 同目录的 args.json 旁路传递——命令行上一个空格都不出现。
    # mkdtemp 每次唯一目录：固定共享目录会让并发 run 互相覆盖 args.json、
    # 写入失败时静默复用上一轮的陈旧参数（P0 修复）。
    stage_dir = Path(tempfile.mkdtemp(prefix="ue_scene_"))
    if " " in str(stage_dir):
        raise RuntimeError(
            f"临时目录含空格（{stage_dir}），UE 命令行会截断脚本路径。"
            "请把 TMP 环境变量指向无空格路径后重试。")
    staged = stage_dir / "build_scene.py"
    shutil.copy2(root / "ue" / "build_scene.py", staged)
    (stage_dir / "args.json").write_text(json.dumps({
        "manifest": str(manifest_path),
        "manifest_out": str(scene_manifest),
        "level": ue_cfg["level"],
        "clean": True,
    }, ensure_ascii=False), encoding="utf-8")

    cmd = (f'"{editor}" "{uproject}" -ExecutePythonScript="{staged}" '
           f'-stdout -FullStdOutLogOutput -unattended -nosplash -nosound')

    print(f"  [ue] 启动编辑器装配（超时 {ue_cfg['timeout_seconds']}s）…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=ue_cfg["timeout_seconds"])
    except subprocess.TimeoutExpired as te:
        # run() 超时会杀掉子进程再抛；把已捕获的部分日志落盘，别让现场蒸发
        partial = te.stdout if isinstance(te.stdout, str) else \
            (te.stdout or b"").decode("utf-8", "replace")
        (run_dir / "ue_build.log").write_text(partial or "", encoding="utf-8")
        raise RuntimeError(
            f"UE 装配超时（{ue_cfg['timeout_seconds']}s），子进程已终止；"
            f"部分日志见 {run_dir / 'ue_build.log'}") from te
    (run_dir / "ue_build.log").write_text(proc.stdout or "", encoding="utf-8")
    if proc.stderr:
        (run_dir / "ue_build.err.log").write_text(proc.stderr, encoding="utf-8")

    # 双重校验：进程退出码 + 回执内容。UE 崩了看退出码，脚本逻辑失败看回执。
    if not scene_manifest.exists():
        tail = "\n".join((proc.stdout or "").splitlines()[-40:])
        raise RuntimeError(f"UE 未写出回执，exit={proc.returncode}\n"
                           f"日志见 {run_dir / 'ue_build.log'}\n--- 尾部 ---\n{tail}")
    result = json.loads(scene_manifest.read_text(encoding="utf-8"))
    if result.get("status") == "error":
        raise RuntimeError(f"装配失败：{result.get('errors')}")

    return {"compiled": True, "ue": result.get("status"),
            "actors": len(result.get("actors", [])),
            "errors": len(result.get("errors", [])),
            "exit_code": proc.returncode}
