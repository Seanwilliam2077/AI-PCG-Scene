#!/usr/bin/env python3
"""把 scene_layout.json 画成俯视平面图 + 立面图，不开 UE 也能校验摆放。

    python tools/preview_layout.py output/run_xxxx/scene_layout.json

用途是「装配前的自检」：房间盒对不对、桌椅有没有堆在一起、桌上物是不是真的在桌
面高度、海报是不是贴在墙上。比起打开编辑器逐个点，这张图两秒就能看出问题。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

COLORS = {                      # BGR
    "folding_table": (180, 140, 80), "table": (180, 140, 80), "desk": (180, 140, 80),
    "metal_folding_chair": (90, 90, 200), "chair": (90, 90, 200),
    "crt_monitor": (200, 220, 120), "monitor": (200, 220, 120),
    "keyboard": (160, 160, 160), "poster": (200, 100, 220),
    "ceiling_light_panel": (255, 255, 200),
}
DEFAULT_COLOR = (140, 140, 140)


def render(layout: dict, px_per_m: int = 42, margin: int = 60) -> np.ndarray:
    room = layout["room"]
    L, W, H = room["size_cm"]
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    # 世界(cm) → 图像(px)：X 向右，Y 向下（俯视图看 UE 的 XY 平面）
    w_px = int(L / 100 * px_per_m) + margin * 2
    h_px = int(W / 100 * px_per_m) + margin * 2
    plan = np.full((h_px, w_px, 3), 28, np.uint8)

    def to_px(x_cm: float, y_cm: float) -> tuple[int, int]:
        return (int(margin + (x_cm - (cx - L / 2)) / 100 * px_per_m),
                int(margin + (y_cm - (cy - W / 2)) / 100 * px_per_m))

    cv2.rectangle(plan, to_px(cx - L / 2, cy - W / 2), to_px(cx + L / 2, cy + W / 2),
                  (70, 70, 70), 2)

    objs = sorted(layout["objects"], key=lambda o: o["position_cm"][2])
    for o in objs:
        x, y, z = o["position_cm"]
        sx, sy, _ = o["size_cm"]
        color = COLORS.get(o["category"], DEFAULT_COLOR)
        if o["confidence"] < 0.5:                       # 低置信画虚化色
            color = tuple(int(c * 0.45) for c in color)
        box = cv2.boxPoints(((0, 0), (sx / 100 * px_per_m, sy / 100 * px_per_m),
                             -o["yaw_deg"]))
        box = np.int32(box + np.array(to_px(x, y)))
        thickness = -1 if z < 1.0 else 2                # 落地物实心、架空物描边
        cv2.drawContours(plan, [box], 0, color, thickness)

    # 相机位置与朝向
    camx, camy, _ = layout["camera"]["position_cm"]
    cam = to_px(camx, camy)
    cv2.circle(plan, cam, 6, (0, 200, 255), -1)
    cv2.line(plan, cam, (cam[0] + 34, cam[1]), (0, 200, 255), 2)
    cv2.putText(plan, "CAM", (cam[0] + 8, cam[1] - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 200, 255), 1)

    # ---- 立面图：看高度分布是否合理（桌上物应在 ~75cm、海报在 ~200cm）----
    elev_h = 260
    elev = np.full((elev_h, w_px, 3), 20, np.uint8)
    for zc, lbl in ((0, "0"), (75, "75"), (150, "150"), (int(H), f"{int(H)}")):
        yy = elev_h - 20 - int(zc / max(H, 1) * (elev_h - 46))
        cv2.line(elev, (margin, yy), (w_px - margin, yy), (55, 55, 55), 1)
        cv2.putText(elev, lbl, (8, yy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (110, 110, 110), 1)
    for o in objs:
        x, _, z = o["position_cm"]
        sz = o["size_cm"][2]
        px = int(margin + (x - (cx - L / 2)) / 100 * px_per_m)
        y0 = elev_h - 20 - int((z + sz) / max(H, 1) * (elev_h - 46))
        y1 = elev_h - 20 - int(z / max(H, 1) * (elev_h - 46))
        cv2.line(elev, (px, y0), (px, y1), COLORS.get(o["category"], DEFAULT_COLOR), 3)

    canvas = np.vstack([plan, np.full((2, w_px, 3), 90, np.uint8), elev])
    cv2.putText(canvas, f"room {L:.0f}x{W:.0f}x{H:.0f}cm  objects={len(objs)}  "
                        f"hfov={layout['camera']['hfov_deg']:.1f} "
                        f"({layout['camera']['fov_source']}, conf={layout['camera']['calib_confidence']:.2f})",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)
    cv2.putText(canvas, "plan (top-down)  |  below: elevation (height)",
                (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)
    return canvas


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    layout = json.loads(src.read_text(encoding="utf-8"))
    img = render(layout)
    out = src.with_name("layout_preview.png")
    cv2.imwrite(str(out), img)
    print(f"{out}  ({img.shape[1]}x{img.shape[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
