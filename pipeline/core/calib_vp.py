"""相机标定：消失点法。全程 OpenCV + numpy，不含任何 AI 模型。

地面射线法（solver.py）的精度强依赖 FOV 与相机高度，这两个量在这里解出来：

  焦距   两组正交水平线的消失点 v1、v2 满足 d1·d2=0，展开即
         f = sqrt(-[(u1-cx)(u2-cx) + (v1-cy)(v2-cy)])
         同一式对「一个水平 VP + 天顶 VP」也成立（竖直⊥任意水平），这是
         一点透视图的救命通道。两条通道都失败才退 FOV=60° 先验。
  姿态   天顶消失点给相机系下的重力向上向量 u，pitch=-arcsin(u_z)、
         roll=atan2(u_x,-u_y)；地平线是直线 l = K^{-T}·u。
  高度   已知尺寸落地物：物高与相机高严格成线性 H_obj = h·H1，故
         h = H_prior / H1，多个先验物取中位数抗离群。

适用面：人造室内（砖缝、桌沿、灯板边缘提供大量正交直线）。会失败的情形见
`CalibResult.confidence` 与模块末尾的说明。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any

import cv2
import numpy as np


# ---------------------------------------------------------------- 线段检测
def detect_segments(img_bgr: np.ndarray, min_len_ratio: float = 0.02) -> np.ndarray:
    """返回 (N,4) 的 [x1,y1,x2,y2]。低照度参考图先 CLAHE 提对比。

    LSD 在 OpenCV 3.4.6–3.4.15 / 4.1.0–4.5.3 因原实现许可证冲突被移除（不是专利），
    4.5.4 起以 MIT 授权实现恢复；老版本自动回退 HoughLinesP。
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    min_len = max(16, int(min_len_ratio * max(img_bgr.shape[:2])))

    segs = None
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        segs = lsd.detect(gray)[0]
    except (cv2.error, AttributeError):
        segs = None
    if segs is None:
        edges = cv2.Canny(gray, 40, 120)
        segs = cv2.HoughLinesP(edges, 1, np.pi / 720, 60,
                               minLineLength=min_len, maxLineGap=6)
    if segs is None:                       # 零检出：空集，交由失败判据接管
        return np.empty((0, 4), dtype=np.float64)

    segs = np.asarray(segs, dtype=np.float64).reshape(-1, 4)
    lengths = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    return segs[lengths > min_len]


def _seg_homoline(s: np.ndarray) -> np.ndarray:
    """线段两端点叉积得齐次直线，按方向分量归一化。"""
    line = np.cross([s[0], s[1], 1.0], [s[2], s[3], 1.0])
    norm = np.linalg.norm(line[:2])
    return line / norm if norm > 1e-12 else line


def ransac_vp(segs: np.ndarray, n_iter: int = 2000, ang_tol_deg: float = 2.0,
              seed: int = 0) -> tuple[int, np.ndarray | None, np.ndarray]:
    """RANSAC 求消失点：随机两线段的交点为候选，内点判据是
    「线段方向」与「线段中点→VP 方向」的夹角 < tol。返回 (内点数, vp齐次, 内点mask)。"""
    n = len(segs)
    if n < 2:
        return 0, None, np.zeros(n, dtype=bool)

    rng = np.random.default_rng(seed)
    mids = np.c_[(segs[:, 0] + segs[:, 2]) / 2, (segs[:, 1] + segs[:, 3]) / 2]
    dirs = np.c_[segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]]
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-12)
    cos_tol = math.cos(math.radians(ang_tol_deg))

    best: tuple[int, np.ndarray | None, np.ndarray] = (0, None, np.zeros(n, dtype=bool))
    for _ in range(n_iter):
        i, j = rng.choice(n, 2, replace=False)
        vp = np.cross(_seg_homoline(segs[i]), _seg_homoline(segs[j]))
        norm = np.linalg.norm(vp)
        if norm < 1e-12:
            continue
        vp = vp / norm
        if abs(vp[2]) < 1e-9:              # 两线平行 → VP 在无穷远，用方向近似
            to_vp = np.tile(dirs[i], (n, 1))
        else:
            to_vp = vp[:2] / vp[2] - mids
            to_vp /= np.maximum(np.linalg.norm(to_vp, axis=1, keepdims=True), 1e-12)
        inliers = np.abs(np.sum(dirs * to_vp, axis=1)) > cos_tol
        cnt = int(inliers.sum())
        if cnt > best[0]:
            best = (cnt, vp, inliers)
    return best


# ---------------------------------------------------------------- 坐标框架
def make_cam_to_flu(up_cam: np.ndarray) -> np.ndarray:
    """相机系(OpenCV: +X右 +Y下 +Z前) → 重力对齐 FLU 系(X前 Y左 Z上) 的旋转。"""
    up = np.asarray(up_cam, dtype=np.float64)
    up = up / np.linalg.norm(up)
    fwd = np.array([0.0, 0.0, 1.0])        # 相机光轴
    fwd = fwd - (fwd @ up) * up            # 投影到水平面
    n = np.linalg.norm(fwd)
    if n < 1e-9:                           # 光轴与重力共线（正对地/天）：另取基准
        fwd = np.array([1.0, 0.0, 0.0])
        fwd = fwd - (fwd @ up) * up
        n = np.linalg.norm(fwd)
    fwd /= n
    left = np.cross(up, fwd)               # 右手系 Z×X=Y
    return np.stack([fwd, left, up])       # 行向量即新基


# ---------------------------------------------------------------- 结果
@dataclass
class CalibResult:
    fx: float
    fy: float
    cx: float
    cy: float
    hfov_deg: float
    vfov_deg: float
    pitch_deg: float
    roll_deg: float
    cam_height_m: float
    up_cam: list[float]
    fov_source: str          # vp2_horizontal | vp1_plus_zenith | prior_hfov60 | manual
    confidence: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibrate(img_bgr: np.ndarray, known_boxes: list[dict],
              prior_hfov_deg: float = 60.0, seed: int = 0) -> CalibResult:
    """known_boxes: [{'bottom_uv':(u,v), 'top_uv':(u,v), 'h_prior_cm':80.0}, ...]
    像素坐标取自感知层给出的已知类别检测框（椅/桌/CRT 等）。"""
    H, W = img_bgr.shape[:2]
    cx, cy = W / 2.0, H / 2.0
    notes: list[str] = []

    segs = detect_segments(img_bgr)
    if len(segs) == 0:
        notes.append("未检出任何线段")
    ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1],
                                segs[:, 2] - segs[:, 0])) % 180 if len(segs) else np.empty(0)
    vert = segs[np.abs(ang - 90) < 20] if len(segs) else segs
    horz = segs[np.abs(ang - 90) >= 20] if len(segs) else segs

    n_z, vp_z, _ = ransac_vp(vert, seed=seed)
    n_1, vp_1, in1 = ransac_vp(horz, seed=seed)
    horz2 = horz[~in1] if len(horz) and in1.any() else horz
    n_2, vp_2, _ = ransac_vp(horz2, seed=seed + 1)

    def f_from(vpa: np.ndarray | None, vpb: np.ndarray | None) -> float | None:
        if vpa is None or vpb is None or abs(vpa[2]) < 1e-9 or abs(vpb[2]) < 1e-9:
            return None
        pa, pb = vpa[:2] / vpa[2], vpb[:2] / vpb[2]
        val = -((pa[0] - cx) * (pb[0] - cx) + (pa[1] - cy) * (pb[1] - cy))
        return float(np.sqrt(val)) if val > 0 else None

    # 任意一对正交消失点都能解 f，但 RANSAC 挑出的"第二水平族"常是伪的
    # （近一点透视图尤其如此，会解出 hfov 160°+ 的离谱值）。所以三对都算，
    # 先用可信 FOV 区间筛掉不合理解，再按"最弱一族的内点数"择优。
    lo_fov, hi_fov = 30.0, 120.0
    candidates: list[tuple[int, float, str]] = []
    for va, na, vb, nb, tag in ((vp_1, n_1, vp_2, n_2, "vp2_horizontal"),
                                (vp_1, n_1, vp_z, n_z, "vp1_plus_zenith"),
                                (vp_2, n_2, vp_z, n_z, "vp2_plus_zenith")):
        cand = f_from(va, vb)
        if cand is None or cand <= 1e-6:
            continue
        cand_hfov = math.degrees(2 * math.atan(W / (2 * cand)))
        if not lo_fov <= cand_hfov <= hi_fov:
            notes.append(f"{tag} 解出 hfov={cand_hfov:.0f}°，越界弃用")
            continue
        candidates.append((min(na, nb), cand, tag))

    if candidates:
        candidates.sort(key=lambda t: -t[0])
        _, f, fov_source = candidates[0]
        # 互检：两个及以上可信解相差 >15% 说明场景大概率非曼哈顿
        f_cross = f
        if len(candidates) >= 2 and abs(candidates[1][1] - f) / f > 0.15:
            notes.append(f"多通道焦距不一致（{f:.0f} vs {candidates[1][1]:.0f}），"
                         f"可能非曼哈顿场景")
            f_cross = None
    else:
        f = W / (2.0 * math.tan(math.radians(prior_hfov_deg) / 2.0))
        fov_source, f_cross = "prior_hfov60", None
        notes.append(f"无可信消失点对，退回 hfov={prior_hfov_deg}° 先验")

    K_inv = np.array([[1 / f, 0, -cx / f], [0, 1 / f, -cy / f], [0, 0, 1]])
    if vp_z is None:
        up = np.array([0.0, -1.0, 0.0])             # 无竖直线族：按零俯仰处理
        notes.append("未求得天顶消失点，pitch/roll 置零")
    else:
        up = K_inv @ vp_z
        up = up / np.linalg.norm(up)
        if up[1] > 0:                               # 约定 u_y<0 为朝上
            up = -up
    pitch = -math.degrees(math.asin(float(np.clip(up[2], -1.0, 1.0))))
    roll = math.degrees(math.atan2(float(up[0]), float(-up[1])))

    # ---- 相机高度：已知落地物中位数反解 ----
    R_g = make_cam_to_flu(up)

    def ray(u: float, v: float) -> np.ndarray:
        d = R_g @ (K_inv @ np.array([u, v, 1.0]))
        return d / np.linalg.norm(d)

    # 地平线：l = K^{-T}·u，用来判锚点底边离地平线够不够远
    hl = K_inv.T @ up

    def horizon_v(u: float) -> float:
        a, b, c = hl
        return float(-(a * u + c) / b) if abs(b) > 1e-9 else -1e9

    heights: list[float] = []
    for kb in known_boxes:
        ub, vb = kb["bottom_uv"]
        # 底边离地平线太近时 d_bz→0，距离公式在这里发散（∂D/∂v ≈ D²/(f·h)），
        # 拿这种锚点反解相机高会得到荒谬的值。留 8px 余量再用。
        if vb - horizon_v(ub) < 8.0:
            continue
        d_b, d_t = ray(ub, vb), ray(*kb["top_uv"])
        if d_b[2] >= -1e-6:                         # 底边不在地平线下方 → 不可用
            continue
        denom = abs(d_b[2]) * max(float(np.hypot(d_t[0], d_t[1])), 1e-9)
        H1 = 1.0 + d_t[2] * float(np.hypot(d_b[0], d_b[1])) / denom
        if H1 > 0.05:
            heights.append(kb["h_prior_cm"] / 100.0 / H1)

    if heights:
        h = float(np.median(heights))
        spread = (float(np.median(np.abs(np.array(heights) - h)) / h)
                  if len(heights) >= 2 else 0.5)
        # 常识兜底：一张室内照片的机位高度不可能是 9cm 或 6m。越界说明锚点框或
        # 焦距不可信，钳回区间并判低置信，别让一个坏值污染整条管线。
        h_clamped = float(np.clip(h, 0.4, 3.0))
        if abs(h_clamped - h) > 1e-6:
            notes.append(f"相机高度解出 {h:.2f}m，越界钳到 {h_clamped:.2f}m（锚点或焦距不可信）")
            spread = max(spread, 0.5)
            h = h_clamped
    else:
        h, spread = 1.45, 1.0                       # 无先验物：视线高先验，判低置信
        notes.append("无可用已知尺寸落地物，相机高度取 1.45m 先验")

    hfov = math.degrees(2 * math.atan(W / (2 * f)))
    vfov = math.degrees(2 * math.atan(H / (2 * f)))

    conf = (min(1.0, (n_1 + n_2) / 60.0)
            * (1.0 if fov_source == "vp2_horizontal" else 0.5)
            * (1.0 if f_cross is not None else 0.6)
            * (1.0 if spread < 0.15 else 0.4)
            * (1.0 if 35 < hfov < 110 else 0.2))
    if n_1 < 8 or n_2 < 8:
        conf *= 0.5
        notes.append(f"水平线族内点偏少（{n_1}/{n_2}），标定不稳")
    if spread > 0.2:
        notes.append(f"相机高度估计离散（MAD/median={spread:.2f}）")
    if not 35 < hfov < 110:
        notes.append(f"hfov={hfov:.1f}° 越界，疑似裁切图或畸变")

    return CalibResult(
        fx=f, fy=f, cx=cx, cy=cy, hfov_deg=hfov, vfov_deg=vfov,
        pitch_deg=pitch, roll_deg=roll, cam_height_m=h, up_cam=up.tolist(),
        fov_source=fov_source, confidence=float(np.clip(conf, 0.0, 1.0)), notes=notes)


def apply_manual_override(calib: CalibResult, data: dict[str, Any],
                          img_w: int, img_h: int) -> CalibResult:
    """fSpy 人工标定覆盖。**必须重算派生量**：

    solver 的射线数学吃的是 fx/fy（K_inv）与 up_cam，不是 hfov/pitch/roll——
    只 setattr 顶层字段的话四个覆盖里只有 cam_height_m 真正生效（历史 bug）。
    这里从覆盖值正向重算：hfov→fx/fy/vfov；pitch/roll→up_cam（calibrate 里
    pitch=-asin(up_z)、roll=atan2(up_x,-up_y) 的逆式）。

    置信度不强设 1.0：0.9 已高于 min_confidence（跳过 FOV 网格搜索，人工标定
    的本意），又不会把 s02 的先验一致性反解与尺度校准短路掉。
    越界值直接拒绝——手滑输入 hfov=620 不该被静默接受。
    """
    hfov = float(data.get("hfov_deg", calib.hfov_deg))
    pitch = float(data.get("pitch_deg", calib.pitch_deg))
    roll = float(data.get("roll_deg", calib.roll_deg))
    height = float(data.get("cam_height_m", calib.cam_height_m))
    if not 20.0 <= hfov <= 120.0:
        raise ValueError(f"calib_manual.json：hfov_deg={hfov} 越界（20~120）")
    if not -60.0 <= pitch <= 60.0:
        raise ValueError(f"calib_manual.json：pitch_deg={pitch} 越界（±60）")
    if not 0.3 <= height <= 5.0:
        raise ValueError(f"calib_manual.json：cam_height_m={height} 越界（0.3~5.0）")

    f = img_w / (2.0 * math.tan(math.radians(hfov) / 2.0))
    p, r = math.radians(pitch), math.radians(roll)
    up = np.array([math.cos(p) * math.sin(r),
                   -math.cos(p) * math.cos(r),
                   -math.sin(p)])
    return CalibResult(
        fx=f, fy=f, cx=calib.cx, cy=calib.cy,
        hfov_deg=hfov,
        vfov_deg=math.degrees(2 * math.atan(img_h / (2 * f))),
        pitch_deg=pitch, roll_deg=roll, cam_height_m=height,
        up_cam=up.tolist(), fov_source="manual",
        confidence=float(data.get("confidence", 0.9)),
        notes=[*calib.notes, "已由 calib_manual.json 覆盖（fSpy 人工标定，派生量已重算）"])


def draw_debug(img_bgr: np.ndarray, calib: CalibResult) -> np.ndarray:
    """标定调试图：线段按族着色 + 地平线。低置信时随 run 落盘供人工判读。"""
    vis = img_bgr.copy()
    segs = detect_segments(img_bgr)
    if len(segs):
        ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1],
                                    segs[:, 2] - segs[:, 0])) % 180
        for s, a in zip(segs, ang):
            color = (0, 200, 255) if abs(a - 90) < 20 else (0, 255, 120)
            cv2.line(vis, (int(s[0]), int(s[1])), (int(s[2]), int(s[3])), color, 1)
    # 地平线 l = K^{-T}·u
    K_inv = np.array([[1 / calib.fx, 0, -calib.cx / calib.fx],
                      [0, 1 / calib.fy, -calib.cy / calib.fy], [0, 0, 1]])
    a, b, c = K_inv.T @ np.array(calib.up_cam)
    if abs(b) > 1e-9:
        W = vis.shape[1]
        p0 = (0, int(-c / b))
        p1 = (W, int(-(a * W + c) / b))
        cv2.line(vis, p0, p1, (0, 0, 255), 2)
    cv2.putText(vis, f"hfov={calib.hfov_deg:.1f} h={calib.cam_height_m:.2f}m "
                     f"conf={calib.confidence:.2f} src={calib.fov_source}",
                (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return vis
