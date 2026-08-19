#!/usr/bin/env python3
"""把 canonical 管线（本目录）同步进 UE 插件的 Python/ 目录。

单一真相源纪律：管线代码只在 C:\\AI Pipeline Test\\pipeline 下修改；插件内的
Python/ 是**同步产物**，改它等于改一个会被覆盖的副本。同步范围是白名单制：
代码与配置进插件，运行产物（output/）与阶段指纹（cache/stages/）不进；
cache/vlm_gateway/ 不同步——插件运行时以自己的根目录为 cache 家目录，且离线回放
需要的缓存由打包脚本按需拷贝。

用法：
    python tools/sync_plugin.py            # 同步
    python tools/sync_plugin.py --check    # 只比对，发现漂移退出码 1（CI 用）
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PY = Path(r"C:\AI Pipeline Test\AISceneBuilderDemo\Plugins\AISceneBuilder\Python")

# 白名单：目录整树 + 单文件
SYNC_DIRS = ["stages", "core", "ue", "prompts", "configs", "tools"]
SYNC_FILES = ["pipeline.py", "requirements.txt", "taxonomy.yaml"]
EXCLUDE_NAMES = {"__pycache__", ".pytest_cache"}


def _iter_src() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for d in SYNC_DIRS:
        src = ROOT / d
        if not src.is_dir():
            continue
        for f in sorted(src.rglob("*")):
            if f.is_file() and not (set(f.parts) & EXCLUDE_NAMES) and f.suffix != ".pyc":
                pairs.append((f, PLUGIN_PY / f.relative_to(ROOT)))
    for name in SYNC_FILES:
        src = ROOT / name
        if src.is_file():
            pairs.append((src, PLUGIN_PY / name))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写入")
    args = ap.parse_args()

    pairs = _iter_src()
    drift: list[str] = []
    for src, dst in pairs:
        same = dst.is_file() and filecmp.cmp(src, dst, shallow=False)
        if same:
            continue
        drift.append(str(src.relative_to(ROOT)))
        if not args.check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    if args.check:
        if drift:
            print(f"[FAIL] 插件副本落后 {len(drift)} 个文件：")
            for d in drift[:20]:
                print(f"  {d}")
            return 1
        print(f"[ ok ] 插件副本与 canonical 一致（{len(pairs)} 个文件）")
        return 0

    digest = hashlib.sha256()
    for src, _ in pairs:
        digest.update(src.read_bytes())
    (PLUGIN_PY / "SYNCED_FROM.md").write_text(
        "# 本目录是同步产物，勿直接编辑\n\n"
        f"来源：`C:\\AI Pipeline Test\\pipeline`（canonical）\n"
        f"同步时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"内容摘要：`{digest.hexdigest()[:16]}`（{len(pairs)} 个文件）\n\n"
        "改管线请改 canonical 后运行 `python tools/sync_plugin.py`。\n",
        encoding="utf-8")
    print(f"[ ok ] 同步 {len(drift)} 个变更文件（共 {len(pairs)} 个受管文件）→ {PLUGIN_PY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
