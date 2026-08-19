"""s01 感知：参考图 → calib.json + perception.json。

两件事并行不悖地做：
  * 相机标定：纯 OpenCV 消失点法，不花一分钱、不依赖任何模型；
  * 语义解析：云端视觉模型（经云端 VLM 网关）出对象清单、灯光、色板、attached_to、text_content，
    以及 box_2d + 多边形 mask。

云端视觉模型 响应格式有一对必须写死在解析里的陷阱：**box_2d 是 (ymin,xmin,ymax,xmax)
即 (y,x) 序，而 mask 多边形顶点是 (x,y) 序**，两者相反，且都归一化到 0-1000。
本阶段一次性把它们转成下游统一消费的标准形（xyxy + (x,y)，均 0~1）。

box_3d 是 云端视觉模型 未进正式文档的实验能力，可能整批只回 2D 框。它只作 yaw/尺寸的
交叉证据，拿不到不影响主干（见 solver.resolve_yaw 的优先级设计）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2

from core import calib_vp
from core.vlm_gateway import GatewayClient

# 已知尺寸落地物：用于反解相机高度（标定）与全局尺度校准（布局）
KNOWN_HEIGHTS_CM = {
    "metal folding chair": 80, "folding chair": 80, "chair": 80,
    "folding table": 75, "table": 75, "desk": 75,
    "crt monitor": 40, "monitor": 40, "door": 205,
}


def _prompt_scene(prompts_dir: Path) -> str:
    return (prompts_dir / "s01_scene.txt").read_text(encoding="utf-8")


def _prompt_detect(prompts_dir: Path) -> str:
    return (prompts_dir / "s01_detect.txt").read_text(encoding="utf-8")


def _norm_detections(items: list[dict], w: int, h: int) -> list[dict]:
    """消化 云端视觉模型 的双序陷阱，输出标准形。顺带丢掉退化框。"""
    out: list[dict] = []
    for it in items:
        box = it.get("box_2d")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        ymin, xmin, ymax, xmax = (float(v) / 1000.0 for v in box)     # (y,x) 序!
        if xmax - xmin < 1e-3 or ymax - ymin < 1e-3:
            continue
        poly = []
        for pt in (it.get("mask") or []):                              # (x,y) 序!
            if isinstance(pt, (list, tuple)) and len(pt) == 2:
                poly.append([float(pt[0]) / 1000.0, float(pt[1]) / 1000.0])
        out.append({
            "label": str(it.get("label", "object")).strip(),
            "bbox_2d": [round(xmin, 5), round(ymin, 5), round(xmax, 5), round(ymax, 5)],
            "mask_poly": poly,
            "attached_to": it.get("attached_to") or None,
            "score": float(it.get("score", 1.0)) if it.get("score") is not None else 1.0,
        })
    return out


def _norm_surface(att):
    """attached_to 是自由文本（"concrete floor"），归一到 floor/wall/ceiling。"""
    if not att:
        return None
    a = str(att).lower()
    if "ceiling" in a or "soffit" in a:
        return "ceiling"
    if "floor" in a or "ground" in a:
        return "floor"
    if "wall" in a:
        return "wall"
    return a


def _nms(dets: list[dict], iou_thr: float = 0.5) -> list[dict]:
    """同类去重。两遍检测（整图 + 热区）会在重叠处出重复框。"""
    def iou(a: list[float], b: list[float]) -> float:
        ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
        ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
        iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
        inter = iw * ih
        ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / ua if ua > 0 else 0.0

    kept: list[dict] = []
    for d in sorted(dets, key=lambda x: -x.get("score", 1.0)):
        same = [k for k in kept if k["label"].lower() == d["label"].lower()]
        if any(iou(d["bbox_2d"], k["bbox_2d"]) > iou_thr for k in same):
            continue
        kept.append(d)
    return kept


def _hotspot_crops(dets: list[dict], w: int, h: int,
                   max_crops: int = 2) -> list[tuple[float, float, float, float]]:
    """热区：大件家具（桌/柜/架）的框外扩一圈。

    云端视觉模型 3 的图像输入有 token 硬上限（ultra_high 也只 2240），4K 图必被降采样，
    桌面上的键盘/软盘这类小件容易整个漏掉。第二遍只喂热区的高清 crop 补检。
    """
    spots = []
    for d in dets:
        lab = d["label"].lower()
        if not any(k in lab for k in ("table", "desk", "shelf", "counter", "cabinet")):
            continue
        x0, y0, x1, y1 = d["bbox_2d"]
        area = (x1 - x0) * (y1 - y0)
        spots.append((area, (max(0.0, x0 - 0.04), max(0.0, y0 - 0.10),
                             min(1.0, x1 + 0.04), min(1.0, y1 + 0.04))))
    spots.sort(key=lambda t: -t[0])
    return [s[1] for s in spots[:max_crops]]


def run(ctx: dict) -> dict:
    run_dir: Path = ctx["run_dir"]
    root: Path = ctx["root"]
    cfg: dict = ctx["config"]
    ref_path = run_dir / "ref.jpg"

    img = cv2.imread(str(ref_path))
    if img is None:
        raise RuntimeError(f"读不出参考图：{ref_path}")
    h, w = img.shape[:2]

    vlm_gateway = GatewayClient.from_config(cfg, root, run_dir=run_dir, stage="s01_perceive")
    prompts = root / "prompts"
    m_text = cfg["models"]["vision"]

    # ---- 1) 语义任务书 ----
    scene = vlm_gateway.chat_json(_prompt_scene(prompts), [ref_path], model=m_text,
                            validator=lambda o: _require(o, ["objects"]))
    objects = scene.get("objects") or []
    phrases = [str(o.get("detection_phrase") or o.get("category") or "").strip()
               for o in objects]
    phrases = [p for p in phrases if p] or ["furniture", "monitor", "chair", "table"]
    phrases += ["floor", "wall", "ceiling"]

    # ---- 2) 检测 + 分割（整图）----
    # 小图上小物件容易整个丢掉。检测坐标是归一化的，放大送入不需要任何坐标换算，
    # 纯赚细节——低于 1600px 长边的参考图先 2 倍上采样再检测。
    det_input = ref_path
    if max(w, h) < 1600:
        up = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        det_input = run_dir / "ref_x2.png"
        cv2.imwrite(str(det_input), up)

    det_prompt = _prompt_detect(prompts).format(phrases=", ".join(dict.fromkeys(phrases)))
    dets_raw = vlm_gateway.chat_json(det_prompt, [det_input], model=m_text,
                               validator=lambda o: _require_list(o))
    dets = _norm_detections(_as_list(dets_raw), w, h)

    # ---- 3) 热区二次检测（补小件）----
    crops_dir = run_dir / "crops"
    crops_dir.mkdir(exist_ok=True)
    for i, (cx0, cy0, cx1, cy1) in enumerate(_hotspot_crops(dets, w, h)):
        px = (int(cx0 * w), int(cy0 * h), int(cx1 * w), int(cy1 * h))
        crop = img[px[1]:px[3], px[0]:px[2]]
        if crop.size == 0 or min(crop.shape[:2]) < 64:
            continue
        cpath = crops_dir / f"hotspot_{i}.png"
        cv2.imwrite(str(cpath), crop)
        try:
            sub_raw = vlm_gateway.chat_json(det_prompt, [cpath], model=m_text,
                                      validator=lambda o: _require_list(o))
            for d in _norm_detections(_as_list(sub_raw), px[2] - px[0], px[3] - px[1]):
                bx = d["bbox_2d"]                       # crop 坐标 → 原图坐标
                d["bbox_2d"] = [
                    round(cx0 + bx[0] * (cx1 - cx0), 5), round(cy0 + bx[1] * (cy1 - cy0), 5),
                    round(cx0 + bx[2] * (cx1 - cx0), 5), round(cy0 + bx[3] * (cy1 - cy0), 5)]
                d["mask_poly"] = [[cx0 + p[0] * (cx1 - cx0), cy0 + p[1] * (cy1 - cy0)]
                                  for p in d["mask_poly"]]
                d["from_hotspot"] = True
                dets.append(d)
        except Exception as e:                          # 补检失败不影响主干
            print(f"  [warn] 热区 {i} 补检失败：{e}")

    # ---- 3b) 四象限分块检测（提召回）----
    # 强模型在整图上倾向把同类合并成"排"（实测 16 台显示器只框出 13）。
    # 四象限放大重检把"排"拆回"台"：每块 60%×60%（15% 重叠防切件），
    # 坐标映射回原图后与主干检测一起过 NMS。贴块边的检测直接丢弃——
    # 跨块物体会产出半截框，与整图框 IoU 不够会漏过去重变成幽灵复制品。
    TILES = [(0.0, 0.0, 0.6, 0.6), (0.4, 0.0, 1.0, 0.6),
             (0.0, 0.4, 0.6, 1.0), (0.4, 0.4, 1.0, 1.0)]
    for i, (cx0, cy0, cx1, cy1) in enumerate(TILES):
        px = (int(cx0 * w), int(cy0 * h), int(cx1 * w), int(cy1 * h))
        crop = img[px[1]:px[3], px[0]:px[2]]
        cpath = crops_dir / f"tile_{i}.png"
        cv2.imwrite(str(cpath), crop)
        try:
            sub_raw = vlm_gateway.chat_json(det_prompt, [cpath], model=m_text,
                                      validator=lambda o: _require_list(o))
            for d in _norm_detections(_as_list(sub_raw), px[2] - px[0], px[3] - px[1]):
                bx = d["bbox_2d"]
                edge = 0.02
                # 只丢触**人工切缝**的检测（跨块物体的半截框）；触**真实图边**
                # 的必须保留——沿墙桌带本来就延伸出画面，一刀切会把参考图的
                # 定义性结构（贴墙家具带）整个丢掉（实测踩过）。
                cut_l = bx[0] < edge and cx0 > 0.001
                cut_t = bx[1] < edge and cy0 > 0.001
                cut_r = bx[2] > 1 - edge and cx1 < 0.999
                cut_b = bx[3] > 1 - edge and cy1 < 0.999
                if cut_l or cut_t or cut_r or cut_b:
                    continue
                d["bbox_2d"] = [
                    round(cx0 + bx[0] * (cx1 - cx0), 5), round(cy0 + bx[1] * (cy1 - cy0), 5),
                    round(cx0 + bx[2] * (cx1 - cx0), 5), round(cy0 + bx[3] * (cy1 - cy0), 5)]
                d["mask_poly"] = [[cx0 + p[0] * (cx1 - cx0), cy0 + p[1] * (cy1 - cy0)]
                                  for p in d["mask_poly"]]
                d["from_tile"] = True
                dets.append(d)
        except Exception as e:                          # 分块失败不影响主干
            print(f"  [warn] 象限 {i} 分块检测失败：{e}")
    dets = _nms(dets)

    # ---- 3c) 逐件枚举拆分（"排"拆回"台"）----
    # 大框倾向一框一排（一排 CRT/一带桌子被并成单框，逐个对应就此丢失）。
    # 对可数类的大检测框裁块重问"逐台框出每一个"；子框 ≥2 且都明显小于父框
    # 时以子框替换父框。失败/仅一件则保留父框，不做任何发明。
    COUNTABLE = ("monitor", "crt", "tower", "table", "chair", "poster", "case")
    enum_tmpl = (prompts / "s01_enumerate.txt").read_text(encoding="utf-8")
    extra: list[dict] = []
    drop: set[int] = set()
    for di, d in enumerate(list(dets)):
        lab = d["label"].lower()
        x0b, y0b, x1b, y1b = d["bbox_2d"]
        area = (x1b - x0b) * (y1b - y0b)
        if not any(k in lab for k in COUNTABLE) or area < 0.02:
            continue
        pad = 0.06
        ex0, ey0 = max(0.0, x0b - pad), max(0.0, y0b - pad)
        ex1, ey1 = min(1.0, x1b + pad), min(1.0, y1b + pad)
        px = (int(ex0 * w), int(ey0 * h), int(ex1 * w), int(ey1 * h))
        crop = img[px[1]:px[3], px[0]:px[2]]
        if crop.size == 0 or min(crop.shape[:2]) < 96:
            continue
        cpath = crops_dir / f"enum_{di}.png"
        cv2.imwrite(str(cpath), crop)
        try:
            sub_raw = vlm_gateway.chat_json(enum_tmpl.format(label=d["label"]), [cpath],
                                      model=m_text,
                                      validator=lambda o: _require_list(o))
            kids = _norm_detections(_as_list(sub_raw), px[2] - px[0], px[3] - px[1])
        except Exception as e:                          # 枚举失败保留父框
            print(f"  [warn] 枚举 {d['label']} 失败：{str(e)[:120]}")
            continue
        good: list[dict] = []
        for kd in kids:
            bx = kd["bbox_2d"]
            kd["bbox_2d"] = [
                round(ex0 + bx[0] * (ex1 - ex0), 5), round(ey0 + bx[1] * (ey1 - ey0), 5),
                round(ex0 + bx[2] * (ex1 - ex0), 5), round(ey0 + bx[3] * (ey1 - ey0), 5)]
            kd["mask_poly"] = [[ex0 + p[0] * (ex1 - ex0), ey0 + p[1] * (ey1 - ey0)]
                               for p in (kd.get("mask_poly") or [])]
            kb = kd["bbox_2d"]
            if (kb[2] - kb[0]) * (kb[3] - kb[1]) < area * 0.8:
                kd["from_enum"] = True
                good.append(kd)
        if len(good) >= 2:
            drop.add(di)
            extra.extend(good)
            print(f"  [enum] {d['label']} 拆成 {len(good)} 件")
    if extra:
        dets = [d for i, d in enumerate(dets) if i not in drop] + extra
        dets = _nms(dets)

    # ---- 4) box_3d 实验位（失败即跳过）----
    box3d: dict[str, list[float]] = {}
    if cfg["models"].get("box3d_enabled", True):
        try:
            raw = vlm_gateway.chat_json(
                (prompts / "s01_box3d.txt").read_text(encoding="utf-8"),
                [ref_path], model=cfg["models"]["box3d"],
                validator=lambda o: _require_list(o))
            for it in _as_list(raw):
                b = it.get("box_3d")
                if isinstance(b, (list, tuple)) and len(b) == 9:
                    box3d[str(it.get("label", "")).lower()] = [float(v) for v in b]
        except Exception as e:                          # 未文档化能力，失效是预期内的
            print(f"  [note] box_3d 不可用（实验位，不影响主干）：{str(e)[:120]}")

    # ---- 5) 相机标定（纯算法）----
    known_boxes = []
    for d in dets:
        lab = d["label"].lower()
        prior = next((v for k, v in KNOWN_HEIGHTS_CM.items() if k in lab), None)
        if prior is None:
            continue
        att = _norm_surface(d.get("attached_to"))
        if att not in (None, "", "floor"):           # 只有落地物才能反解相机高
            continue
        x0, y0, x1, y1 = d["bbox_2d"]
        # 被画幅裁断的物体不能当锚点：它的"底边"是图像边界而不是着地点，
        # 顶边同理。前景常有半截入画的椅子，用它反解会得到 9cm 这种荒谬机位高。
        if y1 > 0.995 or y0 < 0.005 or x0 < 0.003 or x1 > 0.997:
            continue
        if (y1 - y0) * h < 12:                       # 太小的框，一两像素误差就毁掉比例
            continue
        known_boxes.append({"bottom_uv": ((x0 + x1) / 2 * w, y1 * h),
                            "top_uv": ((x0 + x1) / 2 * w, y0 * h),
                            "h_prior_cm": float(prior)})
    calib = calib_vp.calibrate(img, known_boxes,
                               prior_hfov_deg=cfg["calibration"]["prior_hfov_deg"])

    manual = run_dir / "calib_manual.json"
    if manual.exists():                                  # fSpy 人工兜底：存在即覆盖
        data = json.loads(manual.read_text(encoding="utf-8"))
        # 不能裸 setattr：solver 吃的是 fx/fy/up_cam 派生量，必须正向重算
        calib = calib_vp.apply_manual_override(calib, data, w, h)
        print(f"  [calib] 人工标定覆盖生效：hfov={calib.hfov_deg:.1f}° "
              f"pitch={calib.pitch_deg:.1f}° h={calib.cam_height_m:.2f}m")

    cv2.imwrite(str(run_dir / "calib_debug.png"), calib_vp.draw_debug(img, calib))
    (run_dir / "calib.json").write_text(
        json.dumps(calib.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    perception = {"image_size": [w, h], "scene": scene,
                  "detections": dets, "box3d": box3d}
    (run_dir / "perception.json").write_text(
        json.dumps(perception, ensure_ascii=False, indent=2), encoding="utf-8")

    if calib.confidence < cfg["calibration"]["min_confidence"]:
        print(f"  [warn] 标定置信度 {calib.confidence:.2f} 偏低 —— {'; '.join(calib.notes)}")
        print(f"         可用 fSpy 标定后写入 {manual} 覆盖（见 README）")

    return {"objects": len(objects), "detections": len(dets),
            "calib_conf": round(calib.confidence, 3),
            "fov_source": calib.fov_source, "box3d": len(box3d),
            "vlm_gateway": vlm_gateway.stats}


# ---------------- 小工具 ----------------
def _as_list(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    if isinstance(obj, dict):
        for key in ("detections", "objects", "items", "results", "boxes"):
            if isinstance(obj.get(key), list):
                return [o for o in obj[key] if isinstance(o, dict)]
    return []


def _require(obj: Any, keys: list[str]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("期望 JSON 对象")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"缺少字段：{missing}")


def _require_list(obj: Any) -> None:
    if not _as_list(obj):
        raise ValueError("期望非空的检测数组")
