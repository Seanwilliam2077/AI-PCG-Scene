"""地面射线法布局求解器：把 2D 检测框变成 UE 世界坐标。纯 numpy，无 AI。

没有稠密深度图，几何靠一个强假设撑起来：**物体站在已知的平面上**。于是
「检测框底边中点」这条射线与该平面的交点，就是物体的落点。

    地面    z=0     t = h / (-d_z)          需 d_z<0（底边在地平线下方）
    支撑面  z=z_s   t = (z_s - h) / d_z     先解出支撑物才有 z_s
    天花板  z=H     t = (H - h) / d_z       需 d_z>0
    墙      n·P=c   t = (c - n·C) / (n·d)   取 t>0 的最近墙

因此求解必须**分层**：先地面物 → 定房间盒 → 再桌面物 → 再墙/顶物。依赖关系由
感知层给出的 attached_to 字段声明，本模块据此做拓扑排序。

误差特性（必须知道）：距离误差对底边像素误差的敏感度 ∂D/∂v ≈ D²/(f·h)。4K、
hfov 60°(f≈3326px)、h=1.5m 时，5m 处约 0.5cm/px、10m 处约 2cm/px——越接近地平线
放大越剧烈。所以置信度里「底边到地平线的像素余量」占最大权重，余量不足的实例
不硬解，交给规则兜底。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from core.calib_vp import make_cam_to_flu

# 常识尺寸先验 (宽, 深, 高) cm —— 兼作尺度校准的尺子与越界钳制的区间中心
PRIORS: dict[str, tuple[float, float, float]] = {
    "folding_table": (183, 76, 75),
    "table": (140, 70, 75),
    "desk": (140, 70, 75),
    "metal_folding_chair": (47, 50, 80),
    "chair": (47, 50, 85),
    "crt_monitor": (39, 41, 40),
    "monitor": (50, 20, 40),
    "keyboard": (45, 15, 4),
    "ceiling_light_panel": (118, 58, 6),
    "wall_poster": (50, 2, 70),
    "poster": (50, 2, 70),
    "cabinet": (80, 45, 180),
    "shelf": (90, 40, 180),
    "door": (90, 10, 205),
}
# 尺度校准只信这几类（形状规整、尺寸方差小）
SCALE_ANCHORS = ("metal_folding_chair", "chair", "folding_table", "table",
                 "desk", "door", "crt_monitor")
# 高度钳制区间 cm
HEIGHT_CLAMP: dict[str, tuple[float, float]] = {
    "folding_table": (65, 85), "table": (65, 85), "desk": (65, 85),
    "metal_folding_chair": (70, 95), "chair": (70, 100),
    "crt_monitor": (30, 50), "monitor": (30, 60),
    "ceiling_light_panel": (3, 20), "door": (190, 220),
}


def _canon(category: str) -> str:
    """类别词归一：Gemini 给的是自由文本 label，先落到先验表的键上。

    子串匹配必须限定最小长度：空串或极短串对任何键都「包含成立」，会把所有未知
    类别静默归到先验表的第一项上——先验、钳制、尺度锚点会一起错，且不报错。
    """
    c = (category or "").lower().strip().replace(" ", "_").replace("-", "_")
    if not c:
        return ""
    if c in PRIORS:
        return c
    for key in PRIORS:                       # 双向子串，但两侧都要够长才算数
        if len(key) >= 4 and key in c:
            return key
        if len(c) >= 4 and c in key:
            return key
    return c


class LayoutSolver:
    """分层射线求解。所有内部计算在重力对齐 FLU 系（米），输出时转 UE（cm）。"""

    def __init__(self, calib: dict, img_w: int, img_h: int) -> None:
        self.W, self.H = img_w, img_h
        self.h = float(calib["cam_height_m"])
        fx, fy = float(calib["fx"]), float(calib["fy"])
        cx, cy = float(calib["cx"]), float(calib["cy"])
        self.K_inv = np.array([[1 / fx, 0, -cx / fx], [0, 1 / fy, -cy / fy], [0, 0, 1]])
        self.R_g = make_cam_to_flu(np.array(calib["up_cam"], dtype=np.float64))
        self.C = np.array([0.0, 0.0, self.h])
        self.hfov = float(calib["hfov_deg"])
        self.vfov = float(calib["vfov_deg"])
        self._hl = self.K_inv.T @ np.array(calib["up_cam"], dtype=np.float64)
        # 室内场景尺度有界。射线法在地平线附近误差按 D²/(f·h) 发散，个别坏解会
        # 算出几十米外的物体，进而把房间盒撑成几十米——这类解一律判废，交给规则
        # 兜底，而不是让它定义整个场景的尺度。
        self.max_dist = float(calib.get("max_solve_distance_m", 15.0))

    # ---------------- 基础 ----------------
    def bbox_px(self, bbox: list[float]) -> tuple[float, float, float, float]:
        """标准 xyxy(0~1) → 像素。感知层已把 Gemini 的 (y,x) 序转成标准形。"""
        x0, y0, x1, y1 = bbox
        return x0 * self.W, y0 * self.H, x1 * self.W, y1 * self.H

    def horizon_v(self, u: float) -> float:
        """地平线在列 u 处的行坐标，由 l·(u,v,1)=0 解出。"""
        a, b, c = self._hl
        return float(-(a * u + c) / b) if abs(b) > 1e-9 else -1e9

    def ray(self, u: float, v: float) -> np.ndarray:
        d = self.R_g @ (self.K_inv @ np.array([u, v, 1.0]))
        return d / np.linalg.norm(d)

    def hit_z(self, d: np.ndarray, z0: float) -> np.ndarray | None:
        if abs(d[2]) < 1e-6:
            return None
        t = (z0 - self.h) / d[2]
        return self.C + t * d if t > 0 else None

    def hit_wall(self, d: np.ndarray, n_xy: list[float], c: float) -> np.ndarray | None:
        n = np.array([n_xy[0], n_xy[1], 0.0])
        nd = float(n @ d)
        if abs(nd) < 1e-6:
            return None
        t = (c - float(n @ self.C)) / nd
        return self.C + t * d if t > 0 else None

    # ---------------- 第 0 层：落地物 ----------------
    def solve_grounded(self, inst: dict) -> dict | None:
        x0, y0, x1, y1 = self.bbox_px(inst["bbox_2d"])
        um = (x0 + x1) / 2
        d_b = self.ray(um, y1)
        if d_b[2] >= -1e-6:
            return None                                  # 底边过地平线 → 规则兜底
        foot = self.hit_z(d_b, 0.0)
        if foot is None or float(np.hypot(*foot[:2])) > self.max_dist:
            return None                                  # 超出室内合理距离 → 坏解
        d_t = self.ray(um, y0)
        s = float(np.hypot(*foot[:2])) / max(float(np.hypot(d_t[0], d_t[1])), 1e-9)
        height = self.h + s * d_t[2]
        bl, br = self.hit_z(self.ray(x0, y1), 0.0), self.hit_z(self.ray(x1, y1), 0.0)
        proj_w = float(np.hypot(*(br - bl)[:2])) if bl is not None and br is not None else None
        return {"pos": foot, "height_m": max(height, 0.02), "proj_w_m": proj_w,
                "margin_px": y1 - self.horizon_v(um), "method": "ground_ray"}

    # ---------------- 第 1 层：支撑面上的物体 ----------------
    def solve_supported(self, inst: dict, support: dict) -> dict | None:
        z_s = float(support["pos"][2]) + float(support["size_m"][2])
        x0, y0, x1, y1 = self.bbox_px(inst["bbox_2d"])
        um = (x0 + x1) / 2
        p = self.hit_z(self.ray(um, y1), z_s)
        if p is None or float(np.hypot(*p[:2])) > self.max_dist:
            return None
        penalty = 0.0
        if not _in_footprint(p, support):
            p = _clamp_to_footprint(p, support)
            penalty = 0.3
        d_t = self.ray(um, y0)
        s = float(np.hypot(*p[:2])) / max(float(np.hypot(d_t[0], d_t[1])), 1e-9)
        return {"pos": p, "height_m": max(self.h + s * d_t[2] - z_s, 0.02),
                "margin_px": y1 - self.horizon_v(um), "method": "support_ray",
                "penalty": penalty}

    # ---------------- 第 2 层：墙面 / 天花板 ----------------
    def solve_on_wall(self, inst: dict, walls: list[dict]) -> dict | None:
        x0, y0, x1, y1 = self.bbox_px(inst["bbox_2d"])
        d = self.ray((x0 + x1) / 2, (y0 + y1) / 2)
        hits = []
        for w in walls:
            p = self.hit_wall(d, w["n_xy"], w["c"])
            if p is not None:
                hits.append((float(np.linalg.norm(p - self.C)), p, w))
        if not hits:
            return None
        dist, p, w = min(hits, key=lambda t: t[0])
        if dist > self.max_dist:
            return None
        ang_w = (x1 - x0) / self.W * math.radians(self.hfov)
        ang_h = (y1 - y0) / self.H * math.radians(self.vfov)
        return {"pos": p, "method": "wall_ray",
                "size_wh_m": [2 * dist * math.tan(ang_w / 2),
                              2 * dist * math.tan(ang_h / 2)],
                "yaw": math.degrees(math.atan2(w["n_xy"][1], w["n_xy"][0])) + 180.0}

    def solve_on_ceiling(self, inst: dict, room_h: float) -> dict | None:
        x0, y0, x1, y1 = self.bbox_px(inst["bbox_2d"])
        d = self.ray((x0 + x1) / 2, (y0 + y1) / 2)
        p = self.hit_z(d, room_h)
        if p is None:
            return None
        dist = float(np.linalg.norm(p - self.C))
        if dist > self.max_dist:
            return None
        ang_w = (x1 - x0) / self.W * math.radians(self.hfov)
        ang_h = (y1 - y0) / self.H * math.radians(self.vfov)
        return {"pos": p, "method": "ceiling_ray",
                "size_wh_m": [2 * dist * math.tan(ang_w / 2),
                              2 * dist * math.tan(ang_h / 2)]}

    # ---------------- 房间盒体 ----------------
    def solve_room(self, floor_polys: list[list[list[float]]],
                   grounded: list[dict]) -> dict:
        """三条证据：地面多边形落地点、落地物外包、天花板先验。无深度图时的替代方案。"""
        pts: list[np.ndarray] = []
        for poly in floor_polys:
            for u_n, v_n in poly:
                d = self.ray(u_n * self.W, v_n * self.H)
                if d[2] < -1e-6:
                    p = self.hit_z(d, 0.0)
                    if p is not None and np.linalg.norm(p[:2]) < self.max_dist:
                        pts.append(p[:2])
        for g in grounded:                                  # 落地物外包做兜底/外扩
            pts.append(np.asarray(g["pos"][:2]))

        if pts:
            arr = np.array(pts)
            lo = np.percentile(arr, 3, axis=0)
            hi = np.percentile(arr, 97, axis=0)
            margin = 0.4                                    # 墙脚常被家具遮挡，外扩一点
            lo, hi = lo - margin, hi + margin
        else:
            lo, hi = np.array([-3.0, -3.0]), np.array([3.0, 3.0])

        # 相机必须在房间里 —— 拍这张照片的人就站在屋内。
        # 房间盒是由物体位置推出来的，而物体全在相机前方，所以 x 下界会停在最近的
        # 物体处，把原点（相机的地面投影）关在墙外；对齐机位截图就会拍到一面墙。
        cam_pad = 0.8
        lo = np.minimum(lo, np.array([-cam_pad, -cam_pad]))
        hi = np.maximum(hi, np.array([cam_pad, cam_pad]))

        size_x = max(float(hi[0] - lo[0]), 2.0)
        size_y = max(float(hi[1] - lo[1]), 2.0)
        center = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]
        return {"size_m": [size_x, size_y, 2.85], "center_xy_m": center,
                "bounds_m": [lo.tolist(), hi.tolist()]}

    def walls_from_room(self, room: dict) -> list[dict]:
        """房间 AABB → 四面墙的平面方程 n·P = c（n 为指向房内的水平单位法线）。"""
        (lo_x, lo_y), (hi_x, hi_y) = room["bounds_m"]
        return [
            {"name": "N", "n_xy": [-1.0, 0.0], "c": -hi_x},
            {"name": "S", "n_xy": [1.0, 0.0], "c": lo_x},
            {"name": "E", "n_xy": [0.0, -1.0], "c": -hi_y},
            {"name": "W", "n_xy": [0.0, 1.0], "c": lo_y},
        ]


# ---------------- 辅助 ----------------
def _in_footprint(p: np.ndarray, support: dict, margin_m: float = 0.15) -> bool:
    sx, sy = support["size_m"][0] / 2 + margin_m, support["size_m"][1] / 2 + margin_m
    dx, dy = p[0] - support["pos"][0], p[1] - support["pos"][1]
    return abs(dx) <= sx and abs(dy) <= sy


def _clamp_to_footprint(p: np.ndarray, support: dict) -> np.ndarray:
    sx, sy = support["size_m"][0] / 2, support["size_m"][1] / 2
    q = p.copy()
    q[0] = float(np.clip(p[0], support["pos"][0] - sx, support["pos"][0] + sx))
    q[1] = float(np.clip(p[1], support["pos"][1] - sy, support["pos"][1] + sy))
    return q


def flu_to_ue(p_flu, cam_height_m: float = 0.0) -> list[float]:
    """FLU(米, 原点=相机正下方地面) → UE(cm, 左手系 Z-up)。手性翻转即 Y 取负。"""
    x, y, z = float(p_flu[0]), float(p_flu[1]), float(p_flu[2])
    return [round(100.0 * x, 1), round(-100.0 * y, 1), round(100.0 * z, 1)]


def yaw_flu_to_ue(yaw_deg: float) -> float:
    """FLU 右手系 yaw → UE 左手系 yaw：同样因手性翻转而取负。"""
    return round(-float(yaw_deg), 1)


def confidence(margin_px: float, touches_edge: bool, geom_ok: bool,
               clamped: bool, box3d_agrees: bool) -> float:
    """逐实例置信度。地平线余量权重最大——它直接决定距离误差的量级。"""
    return round(
        0.30 * min(1.0, max(margin_px, 0.0) / 30.0)
        + 0.25 * (0.0 if touches_edge else 1.0)
        + 0.20 * (1.0 if geom_ok else 0.0)
        + 0.15 * (0.0 if clamped else 1.0)
        + 0.10 * (1.0 if box3d_agrees else 0.0), 3)


def calibrate_scale(instances: list[dict]) -> float:
    """全局尺度因子：已知先验物的 (先验高 / 实测高) 中位数，抗单点离群。

    返回 s；调用方把相机高乘以 s 后整体重解（两遍式）。
    """
    ratios = []
    for inst in instances:
        # 求解阶段的实例只有 label（category 是输出阶段才落的字段），两个都试
        cat = _canon(inst.get("category") or inst.get("label") or "")
        if cat not in SCALE_ANCHORS or cat not in PRIORS:
            continue
        measured = inst.get("_height_m")
        if not measured or measured < 0.05:
            continue
        bb = inst.get("bbox_2d")
        # 触到画幅边缘的实例被裁断过，实测高度没有意义，不能拿来当尺子
        if bb and (bb[1] < 0.005 or bb[3] > 0.995 or bb[0] < 0.003 or bb[2] > 0.997):
            continue
        # 底边贴近地平线时 ∂D/∂v ≈ D²/(f·h) 发散，这种实例的高度不可信
        if float((inst.get("_r") or {}).get("margin_px", 99)) < 8.0:
            continue
        ratios.append((PRIORS[cat][2] / 100.0) / measured)
    if not ratios:
        return 1.0
    s = float(np.median(ratios))
    return float(np.clip(s, 0.5, 2.0))          # 越界视为解算失败，不做疯狂缩放


def clamp_size(category: str, size_cm: list[float]) -> tuple[list[float], bool]:
    """尺寸钳制：已知类别以先验比例为准，测量值只保留一个整体缩放自由度。

    这是「尺寸的最终裁决权在规则，不在测量」的落地。FOV 只要有偏差，射线法解出的
    绝对尺寸就会系统性地偏大或偏小；而"键盘约 45×15×4cm"这件事的可信度远高于
    任何单张图的测量。所以对先验表里的类别：整体缩放因子取自实测高度与先验高度之
    比（限制在 ±33% 内），三轴按先验比例重建；未知类别才原样用测量值。
    """
    cat = _canon(category)
    out = [max(float(v), 0.5) for v in size_cm]
    clamped = False

    if cat in PRIORS:
        pw, pd, ph = PRIORS[cat]
        k = float(np.clip(out[2] / max(ph, 1e-6), 0.75, 1.33))   # 允许的个体差异
        rebuilt = [pw * k, pd * k, ph * k]
        # 与先验比例差异超过 25% 就判为"被钳"，供下游与置信度追溯
        if any(abs(out[i] - rebuilt[i]) / max(rebuilt[i], 1e-6) > 0.25 for i in range(3)):
            clamped = True
        out = rebuilt
    elif cat in HEIGHT_CLAMP:
        lo, hi = HEIGHT_CLAMP[cat]
        if not lo <= out[2] <= hi:
            scale = float(np.clip(out[2], lo, hi)) / max(out[2], 1e-6)
            out = [v * scale for v in out]        # 等比缩放，保住外形比例
            clamped = True

    return [round(v, 1) for v in out], clamped


def resolve_yaw(inst: dict, walls: list[dict], support: dict | None,
                room: dict) -> tuple[float, str]:
    """yaw 优先级：box_3d(带校验) → 贴墙法线 → 随支撑物 → 长轴贴墙 → 面向相机。

    box_3d 是 Gemini 未文档化的实验能力，随时可能失效；因此它只做增益不做依赖，
    完全拿不到时下面四条规则照样给出可用朝向。
    """
    b3d = inst.get("box3d_raw")
    if isinstance(b3d, (list, tuple)) and len(b3d) == 9:
        yaw = float(b3d[8])
        if -360.0 <= yaw <= 360.0:
            return _snap_to_walls(yaw, walls), "box3d"
    if inst.get("attached_to") == "wall":
        w = _nearest_wall(inst, walls, room)
        if w:
            return round(math.degrees(math.atan2(w["n_xy"][1], w["n_xy"][0])) + 180.0, 1), "wall_normal"
    if support is not None:
        return float(support.get("yaw_deg", 0.0)), "follow_support"
    w = _nearest_wall(inst, walls, room)
    if w:
        return _snap_to_walls(math.degrees(math.atan2(w["n_xy"][1], w["n_xy"][0])),
                              walls), "wall_axis_snap"
    return 0.0, "face_camera"


def _snap_to_walls(yaw_deg: float, walls: list[dict], tol_deg: float = 15.0) -> float:
    """与墙轴夹角小于 tol 就吸附过去——人造室内物体极少斜放。"""
    for w in walls:
        wall_yaw = math.degrees(math.atan2(w["n_xy"][1], w["n_xy"][0]))
        for cand in (wall_yaw, wall_yaw + 90, wall_yaw + 180, wall_yaw + 270):
            diff = (yaw_deg - cand + 180) % 360 - 180
            if abs(diff) < tol_deg:
                return round(cand % 360, 1)
    return round(yaw_deg % 360, 1)


def _nearest_wall(inst: dict, walls: list[dict], room: dict) -> dict | None:
    pos = inst.get("_pos_flu")
    if pos is None or not walls:
        return None
    best, best_d = None, 1e18
    for w in walls:
        n = np.array([w["n_xy"][0], w["n_xy"][1], 0.0])
        d = abs(float(n @ np.asarray(pos)) - w["c"])
        if d < best_d:
            best, best_d = w, d
    return best


def topo_order(instances: list[dict]) -> list[dict]:
    """按 attached_to 依赖排序：地面物 → 支撑物上的物 → 墙/顶物。

    环依赖（A 依赖 B、B 依赖 A，模型偶尔会这么标）直接降级为落地物处理，
    不让整批卡死。
    """
    by_id = {i["id"]: i for i in instances}
    layer: dict[str, int] = {}

    def depth(inst: dict, seen: set[str]) -> int:
        iid = inst["id"]
        if iid in layer:
            return layer[iid]
        if iid in seen:                       # 环 → 当作落地物
            layer[iid] = 0
            return 0
        att = inst.get("attached_to")
        if att in ("wall", "ceiling"):
            d = 100                            # 墙/顶物最后解（要先有房间盒）
        elif att in (None, "", "floor") or att not in by_id:
            d = 0
        else:
            d = depth(by_id[att], seen | {iid}) + 1
        layer[iid] = d
        return d

    for inst in instances:
        depth(inst, set())
    return sorted(instances, key=lambda i: (layer[i["id"]], i["id"]))
