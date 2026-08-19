#!/usr/bin/env python3
"""参考图 vs UE 还原截图 并排对比图——还原度的最直观证据，进视频与报告。

用法：
    python tools/make_comparison.py output/run_xxxx        # 需要 run 目录里已有 ue_shot.png
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python tools/make_comparison.py <run目录>")
        return 2
    run_dir = Path(sys.argv[1])
    ref = cv2.imread(str(run_dir / "ref.jpg"))
    shot = cv2.imread(str(run_dir / "ue_shot.png"))
    if ref is None or shot is None:
        print(f"[FAIL] 缺 ref.jpg 或 ue_shot.png（先用 tools/shot.py 出对齐机位截图）")
        return 1

    h = 720
    def _fit(img):
        s = h / img.shape[0]
        return cv2.resize(img, (int(img.shape[1] * s), h), interpolation=cv2.INTER_AREA)

    ref, shot = _fit(ref), _fit(shot)
    gap = np.full((h, 8, 3), 32, np.uint8)
    canvas = np.hstack([ref, gap, shot])
    band = np.full((44, canvas.shape[1], 3), 24, np.uint8)
    cv2.putText(band, "REFERENCE", (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (200, 200, 200), 2)
    cv2.putText(band, "UE5 REBUILD", (ref.shape[1] + 24, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
    out = run_dir / "comparison.png"
    cv2.imwrite(str(out), np.vstack([band, canvas]))
    print(f"[ ok ] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
