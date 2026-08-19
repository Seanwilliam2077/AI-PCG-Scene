#!/usr/bin/env python3
"""架构红线自检：s01–s04 的一切代码路径不得 import unreal。

后台线程跑这些代码，unreal.* 是 GameThread-only——一旦混入，轻则 CLI 回归失效，
重则编辑器随机崩溃（UE5.6 起直接硬报错）。合法使用 unreal 的只有 ue/build_scene.py
与 tools/ 下的编辑器内工具。

用法：python tools/check_no_unreal.py     （退出码 0=干净，1=违规）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 受检范围：编排器 + 全部 stage + core + 内嵌宿主（宿主自己也必须纯净）
SCAN = [ROOT / "pipeline.py", ROOT / "ue" / "embedded_runner.py",
        *sorted((ROOT / "stages").glob("*.py")),
        *sorted((ROOT / "core").glob("*.py"))]

PATTERN = re.compile(r"^\s*(import\s+unreal\b|from\s+unreal\b)", re.MULTILINE)


def main() -> int:
    bad: list[str] = []
    for f in SCAN:
        if not f.is_file():
            continue
        for m in PATTERN.finditer(f.read_text(encoding="utf-8")):
            line_no = f.read_text(encoding="utf-8")[:m.start()].count("\n") + 1
            bad.append(f"{f.relative_to(ROOT)}:{line_no}")
    if bad:
        print("[FAIL] 以下位置 import 了 unreal（GameThread-only，禁止进入后台线程路径）：")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"[ ok ] {len(SCAN)} 个文件均未引用 unreal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
