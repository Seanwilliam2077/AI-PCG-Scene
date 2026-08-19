"""s02 布局：perception.json + calib.json → scene_layout.json。纯 numpy，零 AI。

分层求解（顺序由 attached_to 的拓扑排序决定）：
    落地物 → 房间盒 → 支撑面上的物体 → 墙面/天花板物

尺度校准是两遍式的：第一遍按标定的相机高解出所有先验物，取 (先验高/实测高) 的
中位数作为全局尺度因子 s；|s-1|>5% 就把相机高乘以 s 后**整体重解**。这与 v1 用
深度图时的做法同源——尺子没换，只是把"深度图"这个中间媒介换成了地面平面假设。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core import solver as S


SURFACE_WORDS = {
    "floor": ("floor", "ground", "concrete"),
    "wall": ("wall", "tile", "brick"),
    "ceiling": ("ceiling", "drop_ceiling", "soffit"),
}


def _surface_kind(label: str | None) -> str | None:
    """判断一个 label 指的是不是「大面」（地/墙/顶），是则返回归一后的类别。

    关键在于**只看最后一个词**：感知层给的是自由文本，"brick wall" / "concrete floor" /
    "drop ceiling" 的中心词在词尾，是真的面；而 "wall poster" / "ceiling light panel"
    里的 wall/ceiling 只是定语，它们是物体。按整串包含匹配会把海报和灯板一起吞掉，
    按词尾匹配才分得开。
    """
    if not label:
        return None
    text = str(label).lower().replace("_", " ").replace("-", " ")
    # "poster on wall" 的词尾也是 wall，但它显然是物体。带明确物体词的一律排除。
    if any(w in text for w in ("poster", "photo", "picture", "sign", "panel", "light",
                               "monitor", "screen", "shelf", "art", "decal", "sticker",
                               "clock", "vent", "socket", "outlet", "pipe")):
        return None
    words = text.split()
    if not words:
        return None
    head = words[-1]
    if head in ("ceiling", "soffit"):
        return "ceiling"
    if head in ("floor", "ground", "flooring"):
        return "floor"
    if head in ("wall", "walls", "wallpaper", "brickwork"):
        return "wall"
    return None


def _normalize_attachment(att: str | None) -> str | None:
    """attached_to 回来的是自由文本（"wet concrete floor"/"subway tile wall"），
    先把三类大面归一到 floor/wall/ceiling，其余原样留给实例解析。"""
    if not att:
        return None
    a = str(att).lower().strip()
    for canon, words in SURFACE_WORDS.items():
        if any(w in a for w in words):
            return canon
    return a


def _resolve_support_ids(items: list[dict]) -> None:
    """把 attached_to 的**类别名**解析成具体支撑**实例的 id**，写进 d["support_id"]。

    模型说"键盘放在 folding table 上"，但图里有 8 张桌子——到底哪张？按几何选：
    取该类别中「2D 框水平范围覆盖本物体底边中点、且底边在本物体底边之下」的最近一张。
    选不出来就置 None，退化为落地物（宁可位置略偏，也不要挂到错误的桌子上）。
    """
    by_label: dict[str, list[dict]] = {}
    for d in items:
        by_label.setdefault(_slug(d["label"]), []).append(d)

    # 已知的落地/桌面类别不接受"挂墙"：模型在杂乱小图上常把靠墙摆放的桌子、
    # 显示器、机箱标成 attached_to=wall，一旦当墙面物解算，它们就会被拍到墙上去。
    FLOOR_STANDING = ("table", "desk", "chair", "monitor", "tower", "cabinet",
                      "shelf_unit", "printer", "trash", "box", "crate", "stool")
    for d in items:
        att = _normalize_attachment(d.get("attached_to"))
        if att == "wall" and any(k in d["label"].lower() for k in FLOOR_STANDING):
            att = None                                   # 交给落地/支撑面求解
        d["_attach"] = att
        d["support_id"] = None
        if att in (None, "floor", "wall", "ceiling"):
            continue
        key = _slug(att)
        cands = by_label.get(key)
        if not cands:                                  # 模糊匹配：类别名互相包含即可
            for lab, group in by_label.items():
                if lab and (lab in key or key in lab):
                    cands = group
                    break
        if not cands:
            continue
        x0, y0, x1, y1 = d["bbox_2d"]
        cx, by = (x0 + x1) / 2, y1
        best, best_score = None, -1e9
        for c in cands:
            if c is d:
                continue
            sx0, sy0, sx1, sy1 = c["bbox_2d"]
            if not (sx0 - 0.02 <= cx <= sx1 + 0.02):   # 水平范围要盖住
                continue
            if sy1 < by - 0.02:                        # 支撑物底边应低于（或接近）本物体底边
                continue
            score = -abs(sy0 - by)                     # 桌面边缘离物体底边越近越可信
            if score > best_score:
                best, best_score = c, score
        if best is not None:
            d["support_id"] = best["id"]


def _emissive_guess(obj: dict) -> tuple[bool, str | None]:
    if obj.get("is_emissive"):
        return True, obj.get("emissive_color")
    return False, None


def _solve_pass(dets: list[dict], calib: dict, w: int, h: int,
                scene_objects: dict) -> tuple[list[dict], dict, list[dict]]:
    """一遍完整求解。返回 (实例列表, 房间, 墙列表)。"""
    sv = S.LayoutSolver(calib, w, h)

    # 地面/墙/天花板只供几何，不进物体清单。标签是自由文本（"concrete floor"、
    # "brick wall"），必须按词尾判面——否则地面多边形收不到，房间盒只能靠落地物
    # 外包硬撑，会撑出几十米的假房间。
    floor_polys = [d["mask_poly"] for d in dets
                   if _surface_kind(d["label"]) == "floor" and d.get("mask_poly")]
    items = [d for d in dets if _surface_kind(d["label"]) is None]

    for i, d in enumerate(items):
        d.setdefault("id", f"{_slug(d['label'])}_{i:02d}")

    # attached_to 是类别名，先解析到具体支撑实例，拓扑排序才有意义
    _resolve_support_ids(items)
    ordered = S.topo_order([{**d, "attached_to": d.get("support_id") or d["_attach"]}
                            for d in items])
    order_index = {d["id"]: n for n, d in enumerate(ordered)}
    items.sort(key=lambda d: order_index.get(d["id"], 0))
    ordered = items

    solved: dict[str, dict] = {}
    grounded_list: list[dict] = []

    # --- 第 0 层：落地物（没有支撑实例、也不挂墙/顶的）---
    for d in ordered:
        if d.get("support_id") or d.get("_attach") in ("wall", "ceiling"):
            continue
        r = sv.solve_grounded(d)
        if r is None:
            continue
        size = _size_from(r, d, sv)
        # 立刻钳制：支撑面高度必须用钳后值，否则桌上的物体会按未钳高度落位、
        # 而输出时桌子被钳到 85cm —— 两边不一致，UE 里就是悬空或穿模。
        # _height_m 保留**实测**高度，两遍式尺度校准要拿它当尺子。
        size_c, _ = S.clamp_size(d["label"], [v * 100 for v in size])
        size = [v / 100.0 for v in size_c]
        solved[d["id"]] = {**d, "_r": r, "size_m": size, "_height_m": r["height_m"],
                           "_pos_flu": r["pos"]}
        grounded_list.append({"pos": r["pos"], "size_m": size})

    room = sv.solve_room(floor_polys, grounded_list)
    walls = sv.walls_from_room(room)

    # --- 第 1 层：支撑面上的物体 ---
    for d in ordered:
        if d["id"] in solved:
            continue
        if d.get("_attach") in ("wall", "ceiling"):
            continue
        sup_id = d.get("support_id")
        sup_rec = solved.get(sup_id) if sup_id else None
        # solve_supported 要的是 {"pos","size_m"}；solved 里存的是内部形（_pos_flu）
        sup = ({"pos": sup_rec["_pos_flu"], "size_m": sup_rec["size_m"],
                "yaw_deg": sup_rec.get("_yaw", 0.0)} if sup_rec else None)
        if sup is None:
            r = sv.solve_grounded(d)                       # 支撑物没解出来 → 当落地物
            if r:
                size = _size_from(r, d, sv)
                size_c, _ = S.clamp_size(d["label"], [v * 100 for v in size])
                size = [v / 100.0 for v in size_c]
                solved[d["id"]] = {**d, "_r": r, "size_m": size,
                                   "_height_m": r["height_m"], "_pos_flu": r["pos"]}
            continue
        r = sv.solve_supported(d, sup)
        if r is None:
            continue
        size = _size_from(r, d, sv)
        size_c, _ = S.clamp_size(d["label"], [v * 100 for v in size])
        size = [v / 100.0 for v in size_c]
        solved[d["id"]] = {**d, "_r": r, "size_m": size, "_height_m": r["height_m"],
                           "_pos_flu": r["pos"], "_support": sup_id}

    # --- 第 2 层：墙面 / 天花板 ---
    for d in ordered:
        if d["id"] in solved:
            continue
        att = d.get("_attach")
        if att == "wall":
            r = sv.solve_on_wall(d, walls)
            if r:
                wh = r["size_wh_m"]
                solved[d["id"]] = {**d, "_r": r, "size_m": [wh[0], 0.02, wh[1]],
                                   "_height_m": wh[1], "_pos_flu": r["pos"],
                                   "_yaw_hint": r.get("yaw")}
        elif att == "ceiling":
            r = sv.solve_on_ceiling(d, room["size_m"][2])
            if r:
                wh = r["size_wh_m"]
                solved[d["id"]] = {**d, "_r": r, "size_m": [wh[0], wh[1], 0.06],
                                   "_height_m": 0.06, "_pos_flu": r["pos"]}

    # --- 收口：把物体钳进房间 ---
    # 越界的几乎都是坏解（底边贴近地平线时距离误差会爆炸式放大）。与其把房间撑大
    # 去迁就离群点，不如把它们拉回墙内并降低置信度——房间尺寸保持可信，闭环轮次
    # 也能优先盯上这些被移动过的实例。
    (lo_x, lo_y), (hi_x, hi_y) = room["bounds_m"]
    for rec in solved.values():
        if rec.get("_attach") == "wall":                # 墙面物本就贴在墙上，不动
            continue
        p = np.asarray(rec["_pos_flu"], dtype=float)
        sm = rec["size_m"]
        hx, hy = min(sm[0] / 2, (hi_x - lo_x) / 2), min(sm[1] / 2, (hi_y - lo_y) / 2)
        nx = float(np.clip(p[0], lo_x + hx, hi_x - hx))
        ny = float(np.clip(p[1], lo_y + hy, hi_y - hy))
        shift = float(np.hypot(nx - p[0], ny - p[1]))
        if shift > 0.01:
            rec["_pos_flu"] = np.array([nx, ny, p[2]])
            rec["_clamped_into_room"] = round(shift, 3)

    return list(solved.values()), room, walls


def _size_from(r: dict, d: dict, sv: S.LayoutSolver) -> list[float]:
    """由投影宽与物高推物理尺寸；深度方向无观测，按类别先验的宽深比给。"""
    height = float(r["height_m"])
    width = float(r.get("proj_w_m") or 0.0)
    if width <= 0.01:
        x0, y0, x1, y1 = sv.bbox_px(d["bbox_2d"])
        dist = float(np.linalg.norm(np.asarray(r["pos"]) - sv.C))
        width = 2 * dist * np.tan((x1 - x0) / sv.W * np.radians(sv.hfov) / 2)
    cat = S._canon(d["label"])
    if cat in S.PRIORS:
        pw, pd, _ = S.PRIORS[cat]
        depth = width * (pd / pw) if pw > 0 else width * 0.6
    else:
        depth = width * 0.6
    return [max(width, 0.02), max(depth, 0.02), max(height, 0.02)]


def _anchor_ratios(instances: list[dict]) -> list[float]:
    """已知尺寸类别的 (先验高 / 实测高)。全都等于 1 时说明这套相机参数自洽。"""
    out = []
    for inst in instances:
        cat = S._canon(inst.get("label", ""))
        m = inst.get("_height_m")
        if not (cat in S.SCALE_ANCHORS and cat in S.PRIORS and m and m > 0.05):
            continue
        bb = inst.get("bbox_2d")
        if bb and (bb[1] < 0.005 or bb[3] > 0.995):        # 被画幅裁断
            continue
        if float(inst.get("_r", {}).get("margin_px", 99)) < 8.0:   # 贴近地平线，误差发散
            continue
        out.append((S.PRIORS[cat][2] / 100.0) / m)
    return out


def _camera_by_prior_consistency(dets: list[dict], calib: dict, w: int, h: int,
                                 scene_objects: dict) -> tuple[dict, dict] | None:
    """标定失败时的兜底：把 (FOV, 机位高, 俯仰) 当未知数，用已知尺寸的物体反解。

    单靠消失点在一点透视、相机近水平、或裁切帧上会失效（焦距数学上不可观测）。
    但场景里有一批尺寸已知的物体（桌 75、椅 80、CRT 40），它们同时给出多个约束：
    只有相机参数正确时，这些物体才会**同时**量出各自的先验高度。于是在三个参数上
    做网格搜索，取「可解锚点多、比例集中、且中位比例接近 1」的那一组。

    评分里三项缺一不可：离散度保证互相一致，log(中位数) 保证绝对尺度对，可解数
    保证不是靠丢掉大部分物体换来的假一致。
    """
    cands = [d for d in dets
             if S._canon(d.get("label", "")) in S.SCALE_ANCHORS
             and not (d["bbox_2d"][3] > 0.995 or d["bbox_2d"][1] < 0.005)]
    if len(cands) < 5:
        return None

    best = None
    for hfov in (55, 65, 75, 85, 95, 105):
        fx = w / (2.0 * np.tan(np.radians(hfov) / 2.0))
        vfov = float(np.degrees(2.0 * np.arctan(h / (2.0 * fx))))
        for cam_h in (0.7, 0.85, 1.0, 1.15, 1.3, 1.5, 1.7):
            for pitch in (0.0, 3.0, 6.0, 9.0, 12.0):
                pr = np.radians(pitch)
                trial = {**calib, "fx": fx, "fy": fx, "hfov_deg": float(hfov),
                         "vfov_deg": vfov, "cam_height_m": cam_h,
                         "up_cam": [0.0, float(-np.cos(pr)), float(-np.sin(pr))]}
                sv = S.LayoutSolver(trial, w, h)
                ratios, n = [], 0
                for d in cands:
                    r = sv.solve_grounded({"bbox_2d": d["bbox_2d"]})
                    if not r or r["margin_px"] < 8:
                        continue
                    n += 1
                    cat = S._canon(d["label"])
                    if r["height_m"] > 0.05:
                        ratios.append(S.PRIORS[cat][2] / 100.0 / r["height_m"])
                if n < 5 or len(ratios) < 4:
                    continue
                med = float(np.median(ratios))
                spread = float(np.median(np.abs(np.array(ratios) - med)) / max(med, 1e-6))
                score = (spread                                   # 互相一致
                         + abs(np.log(max(med, 1e-3))) * 0.5      # 绝对尺度也要对
                         + max(0, len(cands) - n) * 0.01)         # 别靠丢物体换一致
                if best is None or score < best[0]:
                    best = (score, trial, n, med, spread)

    if best is None:
        return None
    score, trial, n, med, spread = best
    trial = {**trial, "fov_source": "prior_consistency",
             "confidence": round(float(np.clip(1.0 - spread * 1.5, 0.15, 0.85)), 3),
             "notes": list(calib.get("notes", []))
                      + [f"消失点标定不可信，改用先验一致性联合反解："
                         f"hfov={trial['hfov_deg']:.0f}° 机位={trial['cam_height_m']:.2f}m "
                         f"俯仰={np.degrees(np.arcsin(-trial['up_cam'][2])):.0f}°"
                         f"（{n} 个锚点，中位比 {med:.2f}，离散度 {spread:.3f}）"]}
    return trial, {"spread": round(spread, 4), "anchors": n, "median_ratio": round(med, 3),
                   "hfov_deg": trial["hfov_deg"], "cam_height_m": trial["cam_height_m"]}


def _slug(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")[:28] or "obj"


def run(ctx: dict) -> dict:
    run_dir: Path = ctx["run_dir"]
    perception = json.loads((run_dir / "perception.json").read_text(encoding="utf-8"))
    calib = json.loads((run_dir / "calib.json").read_text(encoding="utf-8"))
    calib["max_solve_distance_m"] = float(
        ctx["config"]["calibration"].get("max_solve_distance_m", 15.0))
    w, h = perception["image_size"]
    scene = perception.get("scene", {})
    dets = perception["detections"]
    box3d = perception.get("box3d", {})

    scene_objects = {str(o.get("category", "")).lower(): o
                     for o in (scene.get("objects") or [])}

    # 尺度校准：解一遍 → 用已知先验物反解全局尺度 → 缩放相机高重解，迭代到收敛。
    # 物高与相机高严格成线性，理论上一步到位；但不同类别的实测互有出入，
    # 多迭代一两轮能让中位数真正落在 1.0 附近。
    # 标定不可信时（一点透视/相机水平/裁切帧都会让 FOV 不可观测），先用先验
    # 一致性把 FOV 与机位一起反解出来，再进入常规的尺度校准。
    fov_search = None
    if float(calib.get("confidence", 1.0)) < float(
            ctx["config"]["calibration"]["min_confidence"]):
        found = _camera_by_prior_consistency(dets, calib, w, h, scene_objects)
        if found:
            calib, fov_search = found
            print(f"  [calib] 先验一致性联合反解: hfov={calib['hfov_deg']:.0f}° "
                  f"h={calib['cam_height_m']:.2f}m 中位比={fov_search['median_ratio']} "
                  f"离散度={fov_search['spread']} (锚点 {fov_search['anchors']})")

    instances, room, walls = _solve_pass(dets, calib, w, h, scene_objects)
    scale_applied = 1.0
    # 人工标定（fSpy）是真值，不能让噪声锚点把它改掉。自动标定才需要尺度校准。
    manual = str(calib.get("fov_source", "")) == "manual"
    for _ in range(0 if manual else 3):
        s = S.calibrate_scale(instances)
        if abs(s - 1.0) <= 0.05:
            break
        # 同样的常识约束：迭代不能把机位推到 0.4m 以下或 3m 以上，
        # 否则一批坏锚点能把整个场景的尺度带走。
        new_h = float(np.clip(calib["cam_height_m"] * s, 0.4, 3.0))
        if abs(new_h - calib["cam_height_m"]) < 1e-4:
            break
        scale_applied *= new_h / calib["cam_height_m"]
        calib = {**calib, "cam_height_m": new_h}
        instances, room, walls = _solve_pass(dets, calib, w, h, scene_objects)

    # ---- 组装输出 ----
    # 每个 out_object 都是**一次真实检测的解**（位置来自它自己的 bbox 射线），
    # 语义组的 count 不能整个盖上来——那会让每台已定位的显示器再被程序化复制
    # 8 份，用假想排布淹没真实位置（对象与参考图的逐个对应就是这么丢的）。
    # count 只用来补遮挡缺口：组计数 ÷ 该类已解出的检测数，检测够数时为 1。
    from collections import Counter as _Counter
    _det_n = _Counter(S._canon(i["label"]) for i in instances)
    out_objects = []
    id_to_solved = {i["id"]: i for i in instances}
    for inst in instances:
        label = inst["label"]
        sm = inst["size_m"]
        size_cm, clamped = S.clamp_size(label, [v * 100 for v in sm])
        sup = id_to_solved.get(inst.get("_support"))
        yaw, yaw_src = S.resolve_yaw(
            {**inst, "box3d_raw": box3d.get(label.lower())}, walls,
            {"yaw_deg": sup.get("_yaw", 0.0)} if sup else None, room)
        inst["_yaw"] = yaw

        r = inst["_r"]
        x0, y0, x1, y1 = inst["bbox_2d"]
        touches_edge = x0 < 0.005 or y0 < 0.005 or x1 > 0.995 or y1 > 0.995
        conf = S.confidence(margin_px=float(r.get("margin_px", 0.0)),
                            touches_edge=touches_edge,
                            geom_ok=(r.get("penalty", 0.0) == 0.0
                                     and not inst.get("_clamped_into_room")),
                            clamped=clamped,
                            box3d_agrees=label.lower() in box3d)

        meta = scene_objects.get(S._canon(label), {})
        is_em, em_color = _emissive_guess(meta)
        pos_ue = S.flu_to_ue(inst["_pos_flu"])
        out_objects.append({
            "id": inst["id"],
            "category": S._canon(label),
            "label": label,
            "description": meta.get("description", ""),
            "asset_strategy": meta.get("asset_strategy") or _route_hint(label, meta),
            "count": max(1, -(-int(meta.get("count", 1) or 1)
                              // max(1, _det_n[S._canon(label)]))),
            "position_cm": pos_ue,
            "size_cm": size_cm,
            "yaw_deg": S.yaw_flu_to_ue(yaw),
            "yaw_source": yaw_src,
            "material_tags": meta.get("material_tags", []),
            "is_emissive": bool(is_em),
            "emissive_color": em_color,
            "text_content": meta.get("text_content"),
            "thin": bool(meta.get("thin", False)),
            "attached_to": inst.get("attached_to"),
            "bbox_2d": inst["bbox_2d"],
            "mask_poly": inst["mask_poly"],
            "confidence": conf,
            "size_clamped": clamped,
            "occluded": touches_edge,
            "solve": {"method": r["method"], "support_id": inst.get("_support"),
                      "margin_px": round(float(r.get("margin_px", 0.0)), 1),
                      "clamped_into_room_m": inst.get("_clamped_into_room")},
            "box3d_raw": box3d.get(label.lower()),
        })

    room_cm = {
        "size_cm": [round(room["size_m"][0] * 100, 1), round(room["size_m"][1] * 100, 1),
                    round(room["size_m"][2] * 100, 1)],
        "center_cm": S.flu_to_ue([room["center_xy_m"][0], room["center_xy_m"][1], 0.0]),
        "wall_thickness_cm": 20.0,
        "wall_yaws_deg": [0.0, 90.0],
    }
    layout = {
        "meta": {"source_image": "ref.jpg", "coordinate_system": "UE_LHS_Zup",
                 "units": "cm", "pipeline_version": "2.0",
                 "perception_backend": ctx["config"]["models"]["vision"],
                 "scale_calibration": round(scale_applied, 4),
                 "fov_search": fov_search},
        "camera": {"position_cm": [0.0, 0.0, round(calib["cam_height_m"] * 100, 1)],
                   "rotation_deg": {"pitch": round(calib["pitch_deg"], 2), "yaw": 0.0,
                                    "roll": round(calib["roll_deg"], 2)},
                   "hfov_deg": round(calib["hfov_deg"], 2),
                   "vfov_deg": round(calib["vfov_deg"], 2),
                   "aspect": round(w / h, 4),
                   "fov_source": calib["fov_source"],
                   "calib_confidence": calib["confidence"]},
        "room": room_cm,
        "objects": out_objects,
        "lighting": scene.get("lighting", []),
        "palette": scene.get("palette", []),
        "style_tags": scene.get("style_tags", []),
    }
    (run_dir / "scene_layout.json").write_text(
        json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "cameras.json").write_text(
        json.dumps(layout["camera"], ensure_ascii=False, indent=2), encoding="utf-8")

    low = [o["id"] for o in out_objects if o["confidence"] < 0.5]
    return {"objects": len(out_objects), "room_cm": room_cm["size_cm"],
            "scale_calibration": round(scale_applied, 3),
            "low_confidence": len(low)}


def _route_hint(label: str, meta: dict) -> str:
    lab = label.lower()
    # 物体词优先于面词：更强的感知模型会给出 "wall poster" / "floor litter"
    # 这类带方位前缀的标签，用包含匹配判 tileable 会把海报当墙面材质候选
    # （实测：海报纹理抢走 wall 槽，整面墙变成海报棕）。面判定改成
    # 「中心词在词尾」——与 _surface_kind 的语义一致。
    if any(k in lab for k in ("poster", "photo", "sticker", "sign", "screen")):
        return "decal"
    words = lab.replace("_", " ").split()
    if words and words[-1] in ("wall", "floor", "ceiling", "tile", "tiles"):
        return "tileable"
    return "hero"
