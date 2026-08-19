"""compile_layout：scene_layout.json（+ registry + gen2d）→ build_manifest.json。

两份 JSON 的分工是刻意的：
  scene_layout.json  是「对参考图的描述」——面向语义，对象按类别合并、带 count；
  build_manifest.json 是「逐实例的装配指令」——面向 UE，路径绝对、旋转三元组齐全。

中间这层编译是确定性纯函数（无 AI、可单测），所以感知层的 Schema 怎么演进都
不会波及 UE 侧代码。

mesh_kind 是本实现的一个关键分流：proxy_box 直接用引擎自带 Cube 缩放，完全
不走资产导入；只有云端产出的真网格才 import。这让「没有 3D 凭证」的机器照样能
装配出一个尺度正确的完整场景。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def kelvin_to_rgb(k: float) -> list[float]:
    """色温 → sRGB 近似（Tanner Helland）。用于自发光 Tint；灯光本体走 use_temperature。"""
    t = max(1000.0, min(12000.0, float(k))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
        b = 255.0
    return [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]


def _hex_to_rgb(h: str | None) -> list[float] | None:
    if not h or not isinstance(h, str):
        return None
    s = h.strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None


def _wall_of(pos_cm: list[float], room: dict) -> tuple[str, list[float]]:
    """世界坐标 → (墙名, uv)。海报在 UE 里是 DecalActor，需要墙面参数化。"""
    L, W, H = room["size_cm"]
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    x, y, z = pos_cm
    dx0, dx1 = abs(x - (cx - L / 2)), abs(x - (cx + L / 2))
    dy0, dy1 = abs(y - (cy - W / 2)), abs(y - (cy + W / 2))
    best = min((dx1, "N"), (dx0, "S"), (dy1, "E"), (dy0, "W"), key=lambda t: t[0])[1]
    v = max(0.0, min(1.0, z / max(H, 1.0)))
    if best in ("N", "S"):
        u = max(0.0, min(1.0, (y - (cy - W / 2)) / max(W, 1.0)))
    else:
        u = max(0.0, min(1.0, (x - (cx - L / 2)) / max(L, 1.0)))
    return best, [round(u, 4), round(v, 4)]


def _compact_room(room: dict, objects: list[dict], camera: dict) -> dict:
    """房间壳收拢到落地家具包围盒 + 0.8m 边距。

    远墙的深度解算在地平线附近会爆炸（v1 实测把 8m 的房间吹到 15m），而家具
    的位置解算稳定得多——它们才是房间真实边界的可靠证据。只收不放：解算出的
    房间比家具紧时保持原样。墙面附着物（管线/挂架）随后由 _clamp_into_room
    吸附到新墙上。"""
    grounded = [o for o in objects
                if o.get("asset_strategy") == "hero" and abs(o["position_cm"][2]) < 30]
    if len(grounded) < 3:
        return room
    xs: list[float] = []
    ys: list[float] = []
    for o in grounded:
        px, py, _ = o["position_cm"]
        sx, sy, _ = o["size_cm"]
        xs += [px - sx / 2, px + sx / 2]
        ys += [py - sy / 2, py + sy / 2]
    cam_x, cam_y, _ = camera["position_cm"]
    xs.append(cam_x)
    ys.append(cam_y)
    margin = 80.0
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin
    old_L, old_W, H = room["size_cm"]
    L = min(old_L, max(300.0, x1 - x0))
    W = min(old_W, max(300.0, y1 - y0))
    if L >= old_L and W >= old_W:
        return room
    return {**room,
            "size_cm": [round(L, 1), round(W, 1), H],
            "center_cm": [round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1), 0.0]}


def _clamp_into_room(objects: list[dict], camera: dict, room: dict) -> None:
    """收拢墙体后把出界对象吸回房内（墙面附着物恰好贴到新墙上）。原地修改。"""
    L, W, _ = room["size_cm"]
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    for o in objects:
        p = o["position_cm"]
        p[0] = min(cx + L / 2 - 5, max(cx - L / 2 + 5, p[0]))
        p[1] = min(cy + W / 2 - 5, max(cy - W / 2 + 5, p[1]))
    c = camera["position_cm"]
    c[0] = min(cx + L / 2 - 30, max(cx - L / 2 + 30, c[0]))
    c[1] = min(cy + W / 2 - 30, max(cy - W / 2 + 30, c[1]))


def _reposition_camera(camera: dict, room_old: dict, room_new: dict) -> None:
    """房间收拢后按相机在旧房间里的**相对位置**重定位相机。

    求解相机与旧墙体构成了参考图的取景比例；收拢只动墙不动相机会让世界在
    镜头前平移（实测左墙被收到 1m 内吃掉半个画面，拍摄角度随之跑偏）。
    保持 (cam - center)/size 比例不变，构图即与解算一致。原地修改。"""
    if room_new is room_old:
        return
    oc = room_old.get("center_cm", [0, 0, 0])
    os_ = room_old["size_cm"]
    nc = room_new.get("center_cm", [0, 0, 0])
    ns = room_new["size_cm"]
    cam = camera["position_cm"]
    for axis in (0, 1):
        rel = (cam[axis] - oc[axis]) / max(float(os_[axis]), 1.0)
        cam[axis] = round(nc[axis] + rel * float(ns[axis]), 1)


def compile_layout(layout: dict, registry: dict, gen2d: dict,
                   content_dir: str = "/Game/AutoScene") -> dict:
    room_orig = layout["room"]
    objects = layout["objects"]
    room = _compact_room(room_orig, objects, layout["camera"])
    _reposition_camera(layout["camera"], room_orig, room)
    _clamp_into_room(objects, layout["camera"], room)
    assets_out: list[dict] = []
    instances: list[dict] = []
    decals: list[dict] = []

    warnings: list[dict] = []
    reg_assets = registry.get("assets", {})
    for aid, a in reg_assets.items():
        meta = a.get("meta", {})
        kind = ("proxy_box" if meta.get("kind") == "proxy_box"
                else "library" if meta.get("kind") == "library"
                else "imported")
        assets_out.append({
            "id": aid,
            "mesh_kind": kind,
            "mesh": a["mesh"],
            "tex_bc": a.get("textures", {}).get("BC"),
            "tex_n": a.get("textures", {}).get("N"),
            "tex_orm": a.get("textures", {}).get("ORM"),
            "is_screen": bool(a.get("is_screen")),
            "polygon_type": a.get("polygon_type"),
            "uv_source": a.get("uv_source"),
            "pbr_maps": a.get("pbr_maps", []),
            "size_cm": a.get("size_cm"),
            # 库匹配：逐实例缩放（布局尺寸/库网格 bbox）与预导入的 UE 资产路径
            "scale": meta.get("scale"),
            "ue_asset": meta.get("ue_asset"),
            "ue_mi": meta.get("ue_mi"),
        })

    # ---- 逐实例展开：count>1 的按规则补位（沿长轴等距），这是遮挡补全的落地 ----
    for obj in objects:
        aid = obj["id"]
        strategy = obj["asset_strategy"]
        if strategy == "decal":
            wall, uv = _wall_of(obj["position_cm"], room)
            # 贴图来源优先级：gen2d 逐字重绘（文字保真）> 库里的平面印刷品资产
            screen_tex = (gen2d.get("screens", {}).get(aid, {}).get("BC")
                          or gen2d.get("decals", {}).get(aid, {}).get("BC")
                          or registry.get("decal_textures", {}).get(aid))
            if not screen_tex:
                # 丢弃必须可见：静默 continue 是"35 个对象无声丢 21 个"的元凶
                warnings.append({"id": aid, "why": "decal 无贴图（gen2d 未产出）"})
                continue
            # count 展开：感知层给的是"这一类海报出现了 N 次"。参考图的墙是
            # 贴满海报的——沿锚点所在墙均匀铺开、纵向交错、贴图从平面印刷品池
            # 轮换（确定性，复跑一致）。上限 12 防止单组爆炸。
            # 密度下限：强感知模型会把整面海报墙并成 1 框（count=1），逐张计数
            # 丢失。按锚点所在墙的长度兜底——每 1.2m 一张，与参考图密度同量级。
            wall_len_cm = room["size_cm"][1] if _wall_of(obj["position_cm"], room)[0] in ("N", "S") \
                else room["size_cm"][0]
            floor_n = max(1, int(wall_len_cm / 120.0))
            count = min(12, max(floor_n, int(obj.get("count", 1))))
            pool = registry.get("decal_texture_pool") or []
            for k in range(count):
                if count == 1:
                    u_k, v_k, tex_k = uv[0], uv[1], screen_tex
                else:
                    u_k = min(0.96, max(0.04, ((k + 0.5) / count + uv[0]) % 1.0))
                    v_k = min(0.88, max(0.18, uv[1] + (0.08 if k % 2 else -0.06)))
                    tex_k = pool[(k + len(decals)) % len(pool)] if pool else screen_tex
                decals.append({
                    "id": aid if count == 1 else f"{aid}_{k:02d}",
                    "name": obj["category"], "wall": wall,
                    "uv": [round(u_k, 4), round(v_k, 4)],
                    "size_cm": [obj["size_cm"][0], obj["size_cm"][2]],
                    "texture": tex_k,
                    "emissive": bool(obj.get("is_emissive")),
                    "emissive_color": _hex_to_rgb(obj.get("emissive_color")),
                })
            continue
        if aid not in reg_assets:
            warnings.append({"id": aid, "why": "asset_registry 缺失（未生成/未匹配）"})
            continue

        reg_scale = (reg_assets[aid].get("meta") or {}).get("scale")
        count = max(1, int(obj.get("count", 1)))
        base = obj["position_cm"]
        yaw = obj["yaw_deg"]
        cx_r, cy_r, _ = room.get("center_cm", [0, 0, 0])
        size_cm = list(obj["size_cm"])
        # 结构柱强制落地并拉通到天花板：柱子底边常被桌群遮挡，解算会把它
        # 悬在半空（实测 z=68cm 浮空）。柱子的语义就是"顶天立地"。
        if any(k in obj["category"] for k in ("pillar", "column")):
            base = [base[0], base[1], 0.0]
            size_cm[2] = room["size_cm"][2]
        # 屏幕类朝房间内侧：感知不产 yaw（默认 0 → 全部背对镜头）。显示器
        # 摆在桌上必然面向使用者=面向房间轴线，取指向房间中心的方向吸附到
        # 90° 栅格——成排显示器整齐朝内，与参考图一致。
        if reg_assets[aid].get("is_screen"):
            inward = math.degrees(math.atan2(cy_r - base[1], cx_r - base[0]))
            yaw = round(inward / 90.0) * 90.0
        step = max(size_cm[0] * 1.25, 40.0)
        dedup_r = max(25.0, min(size_cm[0], size_cm[1]) * 0.6)
        for k in range(count):
            # 沿 yaw 的垂直方向等距排开（同款 CRT 排在桌沿上就是这个形态）
            off = (k - (count - 1) / 2.0) * step
            rad = math.radians(yaw)
            pos = [round(base[0] - math.sin(rad) * off, 1),
                   round(base[1] + math.cos(rad) * off, 1),
                   base[2]]
            loc = pos if count > 1 else base
            # 相机近场清空：count 展开的行列可能贯穿机位（实测显示器行从相机
            # 背后一路排到镜前 1m，糊满半个画面）。参考图的相机本来就站在
            # 无遮挡处——1.4m 半径内的高于 40cm 的实例剔除。
            cam_p = layout["camera"]["position_cm"]
            d_cam = ((loc[0] - cam_p[0]) ** 2 + (loc[1] - cam_p[1]) ** 2) ** 0.5
            # 椅子豁免：参考图前景 1m 处就有椅子，它们是近景的合法居民
            if d_cam < 140.0 and (loc[2] + size_cm[2]) > 40.0 \
                    and "chair" not in obj["category"]:
                warnings.append({"id": f"{aid}_{k:02d}",
                                 "why": f"相机近场清空（{d_cam:.0f}cm）"})
                continue
            # 同类近位去重：强感知模型会把同一排显示器拆成多个对象组，
            # count 展开后逐台同坐标叠放、z-fight 成碎裂色块（实测两排
            # 逐台坐标完全相同）。同类别 dedup_r 半径内已有实例即跳过。
            cat = obj["category"]
            clash = False
            for other in instances:
                if other.get("_cat") != cat:
                    continue
                ol = other["location_cm"]
                if (abs(ol[0] - loc[0]) < dedup_r and abs(ol[1] - loc[1]) < dedup_r
                        and abs(ol[2] - loc[2]) < 50.0):
                    clash = True
                    break
            if clash:
                warnings.append({"id": f"{aid}_{k:02d}",
                                 "why": f"同类近位去重（{dedup_r:.0f}cm 内已有 {cat}）"})
                continue
            instances.append({
                "id": aid if count == 1 else f"{aid}_{k:02d}",
                "asset_id": aid,
                "name": f"SM_{aid}" if count == 1 else f"SM_{aid}_{k:02d}",
                "location_cm": loc,
                "rotation_deg": {"pitch": 0.0, "yaw": yaw, "roll": 0.0},
                "scale": reg_scale or [1.0, 1.0, 1.0],
                "size_cm": size_cm,
                "_cat": cat,
                "_bbox": obj.get("bbox_2d"),
                "pcg_surface": ("desk" if any(k2 in obj["category"]
                                              for k2 in ("table", "desk")) else None),
            })

    # ---- 沿墙桌带吸附 ----
    # 参考图的定义性结构是两侧贴墙的桌带，桌子延伸出画面（bbox 触图边）。
    # 触边物体的框被裁切、横向深度解算不可信（实测全聚到房间中线）——按其
    # 触边侧贴到对应墙面（图左缘→+Y 墙，图右缘→-Y 墙）。不触边的桌子不动。
    L_wr, W_wr, _ = room["size_cm"]
    cwx, cwy, _ = room.get("center_cm", [0, 0, 0])
    for inst in instances:
        cat = inst.get("_cat") or ""
        if not any(k in cat for k in ("table", "desk", "bench")):
            continue
        bb = inst.get("_bbox") or []
        if len(bb) != 4:
            continue
        if bb[0] < 0.03:
            inst["location_cm"][1] = round(cwy + W_wr / 2 - inst["size_cm"][1] / 2 - 8, 1)
        elif bb[2] > 0.97:
            inst["location_cm"][1] = round(cwy - W_wr / 2 + inst["size_cm"][1] / 2 + 8, 1)

    # ---- 结构补全：显示器簇 → 沿墙桌带 ----
    # 参考图的定义性结构是两侧贴墙桌带 + 桌面 CRT 阵列，但检测把桌带并成
    # 内部框、桌面关系失联。补全的证据链：显示器本身被逐台检出了，屏幕下面
    # 必有桌（强场景先验）——每侧若 ≥3 台屏幕类沿深度分布，生成贴该侧墙的
    # 桌带（覆盖簇的深度范围），并把这一簇显示器等距重排上桌、朝向房间内侧。
    scr = [i for i in instances
           if any(k in (i.get("_cat") or "") for k in ("monitor", "crt", "screen"))]
    table_proto = next((i for i in instances
                        if any(k in (i.get("_cat") or "") for k in ("table", "desk"))),
                       None)
    band_sides: set[float] = set()
    band_range: list[float] = []
    if table_proto is not None and len(scr) >= 3:
        L_b, W_b, _ = room["size_cm"]
        cbx, cby, _ = room.get("center_cm", [0, 0, 0])
        cam_b = layout["camera"]["position_cm"]
        # 第一遍：屏幕簇实证建带。第二遍（循环后）：对称镜像补缺带侧。
        for side, sign in (("l", 1.0), ("r", -1.0)):
            group = [i for i in scr
                     if (i["location_cm"][1] - cam_b[1]) * sign > 60.0]
            if len(group) < 3:
                continue
            xs_g = [i["location_cm"][0] for i in group]
            # 钳进房间：过深幽灵会把簇的深度范围拖出远墙（实测出墙 1.3m）
            x0_b = max(min(xs_g) - 60.0, cbx - L_b / 2 + 80.0)
            x1_b = min(max(xs_g) + 60.0, cbx + L_b / 2 - 80.0)
            band_range = [x0_b, x1_b]
            # 桌带尺寸以**库网格实测 bbox** 归一到真实桌高 74cm——沿用匹配缩放
            # （实测 1.7 倍钳顶）会拼出 1.26m 高的连续白木板，从相机看就是一面
            # 假墙，显示器还会沉进桌面板里（踩过）。
            meta_b = (reg_assets.get(table_proto["asset_id"], {}).get("meta") or {})
            bb3 = meta_b.get("mesh_bbox_cm") or [180.0, 60.0, 74.0]
            s_b = 74.0 / max(float(bb3[2]), 1.0)
            seg_len = max(120.0, float(bb3[0]) * s_b)
            depth_b = float(bb3[1]) * s_b
            wall_y = cby + sign * (W_b / 2 - depth_b / 2 - 12)
            n_seg = max(1, int(-(-(x1_b - x0_b) // seg_len)))
            for si in range(n_seg):
                instances.append({
                    "id": f"band_{side}_{si:02d}",
                    "asset_id": table_proto["asset_id"],
                    "name": f"SM_band_{side}_{si:02d}",
                    "location_cm": [round(x0_b + (si + 0.5) * seg_len, 1),
                                    round(wall_y, 1), 0.0],
                    "rotation_deg": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                    "scale": [round(s_b, 4)] * 3,
                    "size_cm": [seg_len, depth_b, 74.0],
                    "_cat": "band_table", "_banded": True,
                })
            # 座位间距下限 62cm（CRT 实际网格宽 ~50cm + 余量）：纯均分在屏多
            # 带短时会互相嵌进去（实测邻座重叠 11cm）。放不下的溢出屏跳过
            # 重排（由近位去重收走），宁缺勿穿模。
            step_b = max(62.0, (x1_b - x0_b) / max(1, len(group)))
            n_fit = int((x1_b - x0_b) // step_b)
            for gi, inst in enumerate(sorted(group, key=lambda i: i["location_cm"][0])):
                if gi >= n_fit:
                    warnings.append({"id": inst["id"],
                                     "why": "桌带座位不足，溢出屏未重排"})
                    continue
                inst["location_cm"][0] = round(x0_b + (gi + 0.5) * step_b, 1)
                inst["location_cm"][1] = round(wall_y, 1)
                inst["location_cm"][2] = 76.0          # 真实桌面高度
                inst["rotation_deg"]["yaw"] = 90.0 if sign < 0 else -90.0
                inst["_banded"] = True
            band_sides.add(sign)
            warnings.append({"id": f"band_{side}",
                             "why": f"结构补全：{len(group)} 台屏幕重排上贴墙桌带"})

        # 第二遍：对称镜像补缺带侧。参考图这类机房是**三排桌骨架**（左带 +
        # 中央桌岛 + 右带，用户确认）。一侧已实证建带、另一侧只要有任何近墙
        # 桌/屏证据（≥1 件在墙 1.8m 内），即按同深度范围镜像建带，该侧现有
        # 屏幕全部上桌。
        if len(band_sides) == 1 and band_range:
            sign = -next(iter(band_sides))
            side = "l" if sign > 0 else "r"
            wall_line = cby + sign * (W_b / 2)
            evidence = [i for i in instances
                        if abs(i["location_cm"][1] - wall_line) < 180.0
                        and any(k in (i.get("_cat") or "")
                                for k in ("monitor", "crt", "screen",
                                          "table", "desk"))]
            if evidence:
                x0_b, x1_b = band_range
                meta_b = (reg_assets.get(table_proto["asset_id"], {}).get("meta") or {})
                bb3 = meta_b.get("mesh_bbox_cm") or [180.0, 60.0, 74.0]
                s_b = 74.0 / max(float(bb3[2]), 1.0)
                seg_len = max(120.0, float(bb3[0]) * s_b)
                depth_b = float(bb3[1]) * s_b
                wall_y = cby + sign * (W_b / 2 - depth_b / 2 - 12)
                n_seg = max(1, int(-(-(x1_b - x0_b) // seg_len)))
                for si in range(n_seg):
                    instances.append({
                        "id": f"band_{side}_{si:02d}",
                        "asset_id": table_proto["asset_id"],
                        "name": f"SM_band_{side}_{si:02d}",
                        "location_cm": [round(x0_b + (si + 0.5) * seg_len, 1),
                                        round(wall_y, 1), 0.0],
                        "rotation_deg": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                        "scale": [round(s_b, 4)] * 3,
                        "size_cm": [seg_len, depth_b, 74.0],
                        "_cat": "band_table", "_banded": True,
                    })
                group2 = [i for i in scr if not i.get("_banded")
                          and (i["location_cm"][1] - cam_b[1]) * sign > 0.0]
                if group2:
                    step2 = max(62.0, (x1_b - x0_b) / max(1, len(group2)))
                    n_fit2 = int((x1_b - x0_b) // step2)
                    for gi, inst in enumerate(sorted(group2,
                                                     key=lambda i: i["location_cm"][0])):
                        if gi >= n_fit2:
                            continue
                        inst["location_cm"][0] = round(x0_b + (gi + 0.5) * step2, 1)
                        inst["location_cm"][1] = round(wall_y, 1)
                        inst["location_cm"][2] = 76.0
                        inst["rotation_deg"]["yaw"] = 90.0 if sign < 0 else -90.0
                        inst["_banded"] = True
                band_sides.add(sign)
                warnings.append({"id": f"band_{side}",
                                 "why": f"对称镜像桌带（{len(group2)} 台屏幕上桌）"})

    # ---- 支撑面吸附：悬空物必须有落点 ----
    # 解算的"桌面物"父子关系在尺度变化后会脱钩（实测一排 CRT 悬在 93cm 空中、
    # 脚下没桌子，低机位一目了然）。规则：离地 >40cm 的实例吸附到最近的桌面
    # （xy 收进桌面范围、z 对齐桌高）；1.5m 内没有桌子就落地。
    tables = [i for i in instances
              if any(k in (i.get("_cat") or "") for k in ("table", "desk"))]
    for inst in instances:
        loc = inst["location_cm"]
        cat = inst.get("_cat") or ""
        if inst.get("_banded"):
            continue                                   # 桌带重排过的不再动
        if any(k in cat for k in ("table", "desk", "cable", "pipe",
                                  "light", "pillar", "column")):
            continue                                   # 悬挂物/结构物不吸附
        # 屏幕类的"过深幽灵"也要吸：桌面显示器的支撑关系失联后会走地面射线，
        # 解算落点比真实桌位深好几米（实测 6/10 台跑到 6-9m 深处的地板上）。
        # 特征 = 落地 + 比最近的桌子更深。这类拉回桌面；其余落地物不动
        # （参考图前景本来就有落地 CRT）。
        is_screen_cat = any(k in cat for k in ("monitor", "crt", "screen"))
        if loc[2] <= 40.0 and not is_screen_cat:
            continue                                   # 普通落地物不吸附
        if loc[2] <= 40.0 and is_screen_cat:
            near_t = min(tables, key=lambda t: (t["location_cm"][0] - loc[0]) ** 2
                         + (t["location_cm"][1] - loc[1]) ** 2, default=None)
            if near_t is None or loc[0] < near_t["location_cm"][0] - 30.0:
                continue                               # 不比桌深：真·落地 CRT，保留
        best, best_d = None, 1e9
        for t in tables:
            tl = t["location_cm"]
            d = ((tl[0] - loc[0]) ** 2 + (tl[1] - loc[1]) ** 2) ** 0.5
            if d < best_d:
                best, best_d = t, d
        # 悬空物 80cm 内就近吸附（更远宁可落地）；过深幽灵本来就偏出几米，
        # 放宽到 350cm 拉回它该在的桌面。
        snap_r = 350.0 if (loc[2] <= 40.0 and is_screen_cat) else 80.0
        if best is not None and best_d < snap_r:
            tl, ts = best["location_cm"], best["size_cm"]
            loc[0] = min(tl[0] + ts[0] / 2 - 15, max(tl[0] - ts[0] / 2 + 15, loc[0]))
            loc[1] = min(tl[1] + ts[1] / 2 - 15, max(tl[1] - ts[1] / 2 + 15, loc[1]))
            loc[2] = round(ts[2], 1)                   # 桌面高度
        else:
            loc[2] = 0.0                               # 附近没桌子：落地，绝不悬空

    # ---- 桌岛靠墙停靠：中央走道清空 ----
    # 参考图的布局骨架是"两侧桌带 + 中央走道"。未被桌带补全吸收的散桌按其
    # 相对相机的侧别整体平移贴到对应侧墙——桌面上已吸附的物件（显示器/主机）
    # 带着一起走，中央走道让出来，布局向参考骨架收敛。
    L_d, W_d, _ = room["size_cm"]
    cdx, cdy, _ = room.get("center_cm", [0, 0, 0])
    cam_d = layout["camera"]["position_cm"]
    for t in instances:
        cat_t = t.get("_cat") or ""
        if not any(k in cat_t for k in ("table", "desk")) or t.get("_banded"):
            continue
        # 中央桌岛豁免：参考图中间本来就有一排桌（塔/打印机所在）。证据 =
        # 检测框横向居中（cx∈0.38–0.62 的桌子是真·中央桌，实测 0.53）；
        # 只有画面偏侧的桌子才停靠侧墙。
        bb_t = t.get("_bbox") or []
        if len(bb_t) == 4 and 0.38 <= (bb_t[0] + bb_t[2]) / 2 <= 0.62:
            continue
        tl = t["location_cm"]
        # 双侧骨架：已有桌带的一侧不再收散桌——散桌停靠到空侧。两侧都已成带
        # （对称镜像后的常态）则不停靠：散桌留在原地，避免挤进桌带道。
        if len(band_sides) >= 2:
            continue
        if len(band_sides) == 1:
            sign_t = -next(iter(band_sides))
        else:
            sign_t = 1.0 if tl[1] >= cam_d[1] else -1.0
        depth_t = float(t["size_cm"][1])
        y_target = cdy + sign_t * (W_d / 2 - depth_t / 2 - 12)
        dy = y_target - tl[1]
        if abs(dy) < 20.0:
            continue
        for o in instances:                            # 桌面物随桌整体平移
            if o is t or o.get("_banded"):
                continue
            ol = o["location_cm"]
            if (ol[2] > 40.0
                    and abs(ol[0] - tl[0]) < float(t["size_cm"][0]) / 2 + 10
                    and abs(ol[1] - tl[1]) < depth_t / 2 + 10):
                ol[1] = round(ol[1] + dy, 1)
        tl[1] = round(y_target, 1)
        warnings.append({"id": t["id"],
                         "why": f"桌岛靠墙停靠（Δy={dy:+.0f}cm，{'右' if sign_t > 0 else '左'}墙）"})

    # ---- 椅子纵深调度 ----
    # 参考图的椅子分布：两侧椅在前景（贴近相机、桌带前），走道椅在深处。
    # 规则：中央带（|y-cam_y| ≤ W/4）的椅子后移 1.5m；侧带椅子前移 1.5m。
    # 前移在相机近场清空圈外 1.6m 刹车（否则会被清掉），两向都钳进房间。
    L_c, W_c, _ = room["size_cm"]
    ccx, ccy, _ = room.get("center_cm", [0, 0, 0])
    cam_c = layout["camera"]["position_cm"]
    for inst in instances:
        if "chair" not in (inst.get("_cat") or ""):
            continue
        loc = inst["location_cm"]
        # 走道半宽按人行道 0.9m 算：W/4 会把左右两侧的椅子也划进"中央"
        # 导致全体后移（踩过）
        central = abs(loc[1] - cam_c[1]) <= 90.0
        if central:
            loc[0] = min(ccx + L_c / 2 - 60, loc[0] + 150.0)
            why = "中央椅后移 1.5m"
        else:
            floor_x = cam_c[0] + 100.0                 # 前景椅允许贴近到 1m（参考图如此）
            loc[0] = max(floor_x, max(ccx - L_c / 2 + 60, loc[0] - 150.0))
            why = "侧带椅前移 1.5m"
        loc[0] = round(loc[0], 1)
        warnings.append({"id": inst["id"], "why": why})

    # ---- 穿模检测与去穿插 ----
    # 各布局规则（停靠/补带/吸附/调度）各自合法，叠加后仍可能互相穿插
    # （实测中柱穿中央桌）。终局做一遍确定性去穿插：AABB 两两求交，
    # 结构物（柱/桌带）不动，可动物沿**最小穿透轴**推开；椅↔桌豁免
    # （椅子塞进桌下是真实世界的合法姿态）。迭代 3 轮收敛。
    def _real_size(i):
        """实际渲染尺寸：库资产 = 网格实测 bbox × 缩放（比布局意图 size_cm 普遍
        更大，用 size_cm 求交会漏判）。取 yaw 旋转后的世界轴对齐包络——塔转
        90° 后脚印 x/y 互换，不算旋转会把真实相交建成假分离（实测漏掉
        全部塔簇穿模）。"""
        meta_r = (reg_assets.get(i.get("asset_id", ""), {}).get("meta") or {})
        bb_r = meta_r.get("mesh_bbox_cm")
        sc_r = i.get("scale") or [1, 1, 1]
        s_ = ([float(bb_r[k]) * float(sc_r[k]) for k in range(3)]
              if bb_r else [float(v) for v in i["size_cm"]])
        yaw = math.radians(float((i.get("rotation_deg") or {}).get("yaw") or 0.0))
        c_, n_ = abs(math.cos(yaw)), abs(math.sin(yaw))
        return [s_[0] * c_ + s_[1] * n_, s_[0] * n_ + s_[1] * c_, s_[2]]

    def _is_table(i):
        return any(k in (i.get("_cat") or "") for k in ("table", "desk", "band"))

    def _aabb(i, slab):
        l_ = i["location_cm"]
        s_ = _real_size(i)
        z_lo = l_[2]
        # slab=True 时桌类只算桌面板薄层（顶面下 12cm）：折叠桌底下是空的，
        # 实心盒会把参考图里合法的"桌下主机塔"误判成穿模。但薄板豁免只适用
        # 于桌×非桌——桌×桌必须实体对撞：矮桌变体（真实高 61 vs 96）会整体
        # 钻进高桌薄板之下漏检（实测 oz=-23 判不相交，UE 实测重叠 61cm）。
        if slab:
            z_lo = l_[2] + s_[2] - 12.0
        return (l_[0] - s_[0] / 2, l_[0] + s_[0] / 2,
                l_[1] - s_[1] / 2, l_[1] + s_[1] / 2,
                z_lo, l_[2] + s_[2])

    IMMOVABLE = ("pillar", "column", "band_table")
    L_p, W_p, _ = room["size_cm"]
    cpx, cpy, _ = room.get("center_cm", [0, 0, 0])
    # 先把全场钳回房内再去穿插：越界实例的推移候选会被限位全拒（原地不动），
    # 而事后盲钳会把它无碰撞意识地怼进结构物（实测塔被钳进桌带正中）。
    for i_ in instances:
        l_ = i_["location_cm"]
        l_[0] = round(min(cpx + L_p / 2 - 10, max(cpx - L_p / 2 + 10, l_[0])), 1)
        l_[1] = round(min(cpy + W_p / 2 - 10, max(cpy - W_p / 2 + 10, l_[1])), 1)
    def _hits_blocker(loc_m, s_mv, blk):
        """mover 试探落点是否压上结构物（柱/桌带按各自 z 模型求交）。"""
        sb_ = _real_size(blk)
        lb_ = blk["location_cm"]
        zb_lo = lb_[2] + sb_[2] - 12.0 if _is_table(blk) else lb_[2]
        ox_ = (min(loc_m[0] + s_mv[0] / 2, lb_[0] + sb_[0] / 2)
               - max(loc_m[0] - s_mv[0] / 2, lb_[0] - sb_[0] / 2))
        oy_ = (min(loc_m[1] + s_mv[1] / 2, lb_[1] + sb_[1] / 2)
               - max(loc_m[1] - s_mv[1] / 2, lb_[1] - sb_[1] / 2))
        oz_ = (min(loc_m[2] + s_mv[2], lb_[2] + sb_[2])
               - max(loc_m[2], zb_lo))
        return ox_ > 8.0 and oy_ > 8.0 and oz_ > 2.0

    blockers = [i_ for i_ in instances
                if any(k in (i_.get("_cat") or "") for k in IMMOVABLE)
                or i_.get("_banded")]
    n_fix = 0
    for _pass in range(12):
        moved = False
        for ii in range(len(instances)):
            for jj in range(ii + 1, len(instances)):
                a, b = instances[ii], instances[jj]
                ca, cb = a.get("_cat") or "", b.get("_cat") or ""
                # 椅子塞桌下豁免（真实世界的合法姿态）
                if ("chair" in ca and any(k in cb for k in ("table", "desk"))) or \
                   ("chair" in cb and any(k in ca for k in ("table", "desk"))):
                    continue
                # 地面杂物豁免：线缆/垃圾/管线从家具底下穿过是参考图的物理
                # 常态，proxy 高盒与椅腿的"相交"不是视觉穿模（UE 实测也不报）
                if any(k in ca or k in cb for k in ("cable", "litter", "pipe")):
                    continue
                A = _aabb(a, _is_table(a) and not _is_table(b))
                B = _aabb(b, _is_table(b) and not _is_table(a))
                ox = min(A[1], B[1]) - max(A[0], B[0])
                oy = min(A[3], B[3]) - max(A[2], B[2])
                oz = min(A[5], B[5]) - max(A[4], B[4])
                if ox <= 2.0 or oy <= 2.0 or oz <= 2.0:
                    continue                            # 未穿插（留 2cm 接触容差；
                    # 8cm 会放过 UE 实测可见的贴边擦碰，跳跃落点自带 5cm 间隙
                    # 不会因收紧而振荡）
                a_fixed = any(k in ca for k in IMMOVABLE) or a.get("_banded")
                b_fixed = any(k in cb for k in IMMOVABLE) or b.get("_banded")
                if a_fixed and b_fixed:
                    continue                            # 都动不了，记录即可
                mover = b if a_fixed else (a if b_fixed else
                                           (a if a["size_cm"][0] * a["size_cm"][1]
                                            <= b["size_cm"][0] * b["size_cm"][1] else b))
                other = a if mover is b else b
                ml = mover["location_cm"]
                axis = 0 if ox <= oy else 1
                # 桌带是连续车道：沿带轴（x）推只会滑进下一段（实测 6 轮
                # ping-pong 不收敛），强制沿垂直车道方向逃逸。
                if "band_table" in ca or "band_table" in cb:
                    axis = 1
                lo_lim = (cpx - L_p / 2 + 15) if axis == 0 else (cpy - W_p / 2 + 15)
                hi_lim = (cpx + L_p / 2 - 15) if axis == 0 else (cpy + W_p / 2 - 15)
                O = A if other is a else B
                s_m = _real_size(mover)
                half = s_m[axis] / 2
                # 直接跳到"贴着对方即分离"的落点，不做增量小步推——障碍与墙的
                # 隙宽不够放下 mover 时，小步推会在两点间振荡（实测 455↔479
                # 永不收敛）。落点两重校验：钳墙后仍与对方重叠作废；压上结构物
                # 也作废（贴对方分离却撞柱会形成"推开-撞柱-弹回"确定性循环，
                # 实测 2 对残留恒定）。两侧都塞不下则跳过这对。
                cands = []
                for c in (O[axis * 2 + 1] + half + 5.0,
                          O[axis * 2] - half - 5.0):
                    c = min(hi_lim, max(lo_lim, c))
                    ov = (min(c + half, O[axis * 2 + 1])
                          - max(c - half, O[axis * 2]))
                    if ov > 2.0:
                        continue
                    trial = list(ml)
                    trial[axis] = c
                    mover_chair = "chair" in (mover.get("_cat") or "")
                    if any(blk is not mover and blk is not other
                           and not (mover_chair and _is_table(blk))
                           and _hits_blocker(trial, s_m, blk)
                           for blk in blockers):
                        continue
                    # 相机近场也是禁区：近场清空规则跑在去穿插之前，落点不能
                    # 再把大件跳回镜头前（实测大桌跳到相机脸上毁构图）；椅子
                    # 沿用近场豁免（参考图前景 1m 内就有椅）
                    if not mover_chair:
                        d_cx = max(0.0, abs(cam_c[0] - trial[0]) - s_m[0] / 2)
                        d_cy = max(0.0, abs(cam_c[1] - trial[1]) - s_m[1] / 2)
                        if math.hypot(d_cx, d_cy) < 140.0:
                            continue
                    cands.append(c)
                if not cands:
                    continue
                ml[axis] = round(min(cands, key=lambda c: abs(c - ml[axis])), 1)
                n_fix += 1
                moved = True
                warnings.append({"id": mover["id"],
                                 "why": f"去穿插：与 {other['id']} 重叠 "
                                        f"{ox:.0f}×{oy:.0f}cm，已推开"})
        if not moved:
            break
    if n_fix:
        print(f"  [depen] 去穿插 {n_fix} 处")

    # ---- 重力吸附（模型级）：悬空物落到地面或正下方最近支撑面顶 ----
    # 停靠/去穿插会把支撑桌挪走，留下悬空的桌面物；解算的绝对深度也会给出
    # 离地悬浮。向下吸附到脚印重叠 ≥30% 的最高支撑面（没有就落地）。
    # 顶部安装物（灯类）与底高 >170cm 的墙/顶挂件不动。UE 侧还有一道真实
    # 包围盒重力（库网格真实高度与模型有落差，见 build_scene）。
    GRAV_EXEMPT = ("light", "lamp", "fan", "vent", "duct", "pillar", "column")
    n_grav = 0
    for _pass_g in range(2):                           # 两轮：叠放物随支撑连锁下落
        for i_ in instances:
            cat_g = i_.get("_cat") or ""
            if any(k in cat_g for k in GRAV_EXEMPT):
                continue
            z_g = i_["location_cm"][2]
            if z_g <= 2.0 or z_g > 170.0:
                continue
            s_i = _real_size(i_)
            l_i = i_["location_cm"]
            best = 0.0
            for j_ in instances:
                if j_ is i_:
                    continue
                s_j = _real_size(j_)
                top = j_["location_cm"][2] + s_j[2]
                if top > z_g + 6.0:
                    continue
                ox_g = (min(l_i[0] + s_i[0] / 2, j_["location_cm"][0] + s_j[0] / 2)
                        - max(l_i[0] - s_i[0] / 2, j_["location_cm"][0] - s_j[0] / 2))
                oy_g = (min(l_i[1] + s_i[1] / 2, j_["location_cm"][1] + s_j[1] / 2)
                        - max(l_i[1] - s_i[1] / 2, j_["location_cm"][1] - s_j[1] / 2))
                if ox_g > 0 and oy_g > 0 and ox_g * oy_g >= 0.3 * s_i[0] * s_i[1]:
                    best = max(best, top)
            if z_g - best > 3.0:
                i_["location_cm"][2] = round(best, 1)
                n_grav += 1
                warnings.append({"id": i_["id"],
                                 "why": f"重力吸附：悬空 {z_g - best:.0f}cm → 落到 {best:.0f}"})
    if n_grav:
        print(f"  [grav] 重力吸附 {n_grav} 处")

    for inst in instances:                             # 临时键不落盘
        inst.pop("_cat", None)
        inst.pop("_bbox", None)
        inst.pop("_banded", None)

    # ---- 灯光：相对强度 × 类别基准 → 绝对物理单位 ----
    # v2：s03 的氛围估计（gen2d.ambience）先给全局基调（主光色温/雾/曝光），
    # 布局解析出的逐灯记录再覆盖细节。估计失败时 amb=None，全部走旧默认值。
    amb = gen2d.get("ambience") or {}
    amb_key = amb.get("key_light") or {}
    amb_temp = int(amb_key.get("color_temp_k") or 6500)
    amb_inten = float(amb_key.get("relative_intensity") or 0.7)
    lighting = {"ceiling_panels": [], "screen_fill_lights": [], "spots": [],
                "fog_density": float(amb.get("fog_density") or 0.04)}
    H = room["size_cm"][2]
    cx, cy, _ = room.get("center_cm", [0, 0, 0])
    for lt in layout.get("lighting", []):
        kind = (lt.get("type") or "").lower()
        inten = float(lt.get("relative_intensity", 0.6) or 0.6)
        rgb = _hex_to_rgb(lt.get("color_hex"))
        if kind in ("ceiling_panel", "ceiling", "panel"):
            lighting["ceiling_panels"].append({
                "location_cm": lt.get("position_cm") or [0, 0, H - 3],
                "size_cm": [118, 58],
                "kelvin": int(lt.get("color_temp_k") or amb_temp),
                "lumen": round(1500 + 3500 * inten, 1)})
        elif kind in ("screen_glow", "screen"):
            lighting["screen_fill_lights"].append({
                "location_cm": lt.get("position_cm") or [0, 0, 100],
                "rgb": rgb or [0.5, 0.9, 0.9],
                "lumen": round(80 + 220 * inten, 1), "radius_cm": 180})
        elif kind in ("neon", "practical", "spot"):
            lighting["spots"].append({
                "location_cm": lt.get("position_cm") or [0, 0, H - 40],
                "rotation_deg": {"pitch": -60.0, "yaw": 0.0, "roll": 0.0},
                "kelvin": int(lt.get("color_temp_k") or 4500),
                "lumen": round(400 + 1600 * inten, 1)})
    if not lighting["ceiling_panels"]:                 # 没解析到灯 → 给一组默认吊顶
        lighting["ceiling_panels"].append(
            {"location_cm": [0, 0, H - 3], "size_cm": [118, 58],
             "kelvin": amb_temp, "lumen": round(1500 + 3500 * amb_inten, 1)})

    # 按房间尺寸铺开吊顶灯：VLM 只会报"有一种吊顶灯板"，不会逐个数。一间 10×15m
    # 的机房靠一盏灯必然漆黑，而参考图里是成排的灯管。按 ~3m 间距补成网格，
    # 沿用解析到的色温与强度。
    L_room, W_room = room["size_cm"][0], room["size_cm"][1]
    proto = dict(lighting["ceiling_panels"][0])
    # 2.5m 间距、上限 6×4：3m 间距在收拢后的长房间里会留大片暗区
    # （实测桌带泡在灯栅覆盖外的黑暗里，画面上等于不存在）
    nx = max(1, min(6, int(L_room // 250)))
    ny = max(1, min(4, int(W_room // 250)))
    if nx * ny > 1:
        grid = []
        for ix in range(nx):
            for iy in range(ny):
                gx = cx + (ix - (nx - 1) / 2.0) * (L_room / max(nx, 1))
                gy = cy + (iy - (ny - 1) / 2.0) * (W_room / max(ny, 1))
                p2 = dict(proto)
                p2["location_cm"] = [round(gx, 1), round(gy, 1), H - 3]
                grid.append(p2)
        lighting["ceiling_panels"] = grid

    # ---- 房间壳材质 ----
    # 材质的 id 来自感知层的自由文本（wet_concrete_floor_02 / subway_tile_wall_47），
    # 不是 wall/floor/ceiling 这种规整键；所以按关键词归槽。同一槽有多个候选时，
    # 取接缝分数最低的那套——它是四方连续质量最好的一张。
    SLOT_WORDS = {"floor": ("floor", "ground", "concrete"),
                  "ceiling": ("ceiling", "soffit"),
                  "wall": ("wall", "tile", "brick")}
    mats: dict[str, dict] = {}
    best_seam: dict[str, float] = {}
    # 精确键优先：s03 的房间壳表面用 kind 本身作 id（"wall"/"floor"/"ceiling"），
    # 它们是真正的墙地顶材质。关键词兜底只在精确键缺席时生效——否则
    # "wall_poster_31" 这类 id 会靠低接缝分抢走 wall 槽（实测翻过车）。
    for slot in SLOT_WORDS:
        entry = (gen2d.get("materials") or {}).get(slot)
        if entry and entry.get("paths"):
            mats[slot] = entry["paths"]
            best_seam[slot] = float(entry.get("seam_score", 9.9))
    for key, entry in (gen2d.get("materials") or {}).items():
        if not entry.get("paths") or key in SLOT_WORDS:
            continue
        k = key.lower()
        for slot, words in SLOT_WORDS.items():         # ceiling 先于 wall，避免 tile 抢走
            if slot in mats:
                continue                               # 精确键已占槽，不许被抢
            if any(w in k for w in words):
                score = float(entry.get("seam_score", 9.9))
                if slot not in mats or score < best_seam[slot]:
                    mats[slot], best_seam[slot] = entry["paths"], score
                break
    room_out = {"size_cm": room["size_cm"], "center_cm": room.get("center_cm", [0, 0, 0]),
                "wall_thickness_cm": room.get("wall_thickness_cm", 20.0),
                "materials": mats}

    cam = layout["camera"]
    hfov = math.radians(cam["hfov_deg"])
    focal = 36.0 / (2.0 * math.tan(hfov / 2.0)) if hfov > 0 else 24.0
    if warnings:
        print(f"  [warn] compile_layout 丢弃 {len(warnings)} 个对象："
              + "; ".join(f"{w['id']}({w['why']})" for w in warnings[:6])
              + ("…" if len(warnings) > 6 else ""))
    return {
        "content_dir": content_dir,
        "room_box": room_out,
        "assets": assets_out,
        "instances": instances,
        "decals": decals,
        "lighting": lighting,
        "warnings": warnings,
        # ev_bias 基准 1.2（AEM_MANUAL 的经验值），氛围估计给相对偏移但下限钳到 1.0：
        # 手动曝光下再压暗会让室内直接糊成黑，"暗调"应该由灯光色温与雾承担。
        # white_temp 钳到 [6500, 8000]：UE 的 WB 低于中性 6500 会把整帧推蓝
        # （VLM 报的 5200 是"光源色温"，直接当 WB 用等于反向补色，白砖墙渲成蓝墙）。
        "post": {"ev_bias": round(max(0.85, 1.2 + float(amb.get("ev_bias") or 0.0)), 2),
                 "bloom": 0.8, "vignette": 0.45,
                 "white_temp": min(8000.0, max(6500.0,
                                               float(amb.get("white_temp_k") or 6500.0)))},
        "ambience": amb or None,
        "camera": {"location_cm": cam["position_cm"], "rotation_deg": cam["rotation_deg"],
                   "hfov_deg": cam["hfov_deg"], "aspect": cam.get("aspect", 16 / 9),
                   "focal_mm": round(focal, 2), "sensor_width_mm": 36.0},
        "palette": layout.get("palette", []),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--gen2d", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--content-dir", default="/Game/AutoScene")
    a = ap.parse_args()

    layout = json.loads(Path(a.layout).read_text(encoding="utf-8"))
    registry = json.loads(Path(a.registry).read_text(encoding="utf-8"))
    gen2d = json.loads(Path(a.gen2d).read_text(encoding="utf-8"))
    manifest = compile_layout(layout, registry, gen2d, a.content_dir)
    Path(a.out).write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"build_manifest: assets={len(manifest['assets'])} "
          f"instances={len(manifest['instances'])} decals={len(manifest['decals'])}")


if __name__ == "__main__":
    main()
