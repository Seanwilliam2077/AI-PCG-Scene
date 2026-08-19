"""图像后处理：全部确定性算法，用来兜住生成模型的三个硬约束。

Gemini 图像模型没有 alpha 通道、没有 mask 输入、输出还会相对输入漂移，本文件
就是这三条的工程回答：

  绿幕抠像    模型画不出透明背景（会把棋盘格画进 RGB），于是约定纯色底后本地抠。
  ECC 对齐    编辑类输出会整体位移/缩放，出图后一律对齐回输入坐标系。
  接缝质检    无 circular padding 就没有无缝的数学保证，改用可量化的检验：把接缝
              滚到画面中心，比较十字带与其余区域的梯度能量比。
  PBR 推导    绝不让生成模型「画」法线图——那是视觉伪造而非归一化向量场，烘进
              UE 光照必错。height/normal/roughness 一律由 albedo 算出来。
"""

from __future__ import annotations

import cv2
import numpy as np

# 绿幕：纯绿 hue=120° → OpenCV H 域(0~179) 即 60。
# 上限 78 避开青色(H≈90)，下限 42 避开黄绿。注意绿色荧光屏内容(H≈55~70)恰在
# 区间内，这类资产必须切品红底。
CHROMA_PRESETS = {
    "green":   {"hex": "#00FF00", "lo": (42, 90, 60), "hi": (78, 255, 255)},
    "magenta": {"hex": "#FF00FF", "lo": (140, 90, 60), "hi": (170, 255, 255)},
}


# ---------------------------------------------------------------- 抠像
def chroma_key(bgr: np.ndarray, preset: str = "green", feather: int = 2) -> np.ndarray:
    """返回 BGRA（cv2 通道序）。含溢色抑制：前景边缘的底色分量钳到另两通道的最大值。"""
    cfg = CHROMA_PRESETS[preset]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    bg = cv2.inRange(hsv, np.array(cfg["lo"], np.uint8), np.array(cfg["hi"], np.uint8))
    bg = cv2.morphologyEx(bg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    alpha = cv2.GaussianBlur(255 - bg, (0, 0), max(feather, 0.1))

    b, g, r = (c.astype(np.int16) for c in cv2.split(bgr))
    edge = (alpha > 0) & (alpha < 255)
    if preset == "green":
        spill = (g > np.maximum(r, b)) & edge
        g[spill] = np.maximum(r, b)[spill]
    else:                                   # 品红：R 与 B 同时偏高
        spill = (np.minimum(r, b) > g) & edge
        r[spill] = g[spill] + (r[spill] - g[spill]) // 3
        b[spill] = g[spill] + (b[spill] - g[spill]) // 3
    rgb = cv2.merge([np.clip(b, 0, 255).astype(np.uint8),
                     np.clip(g, 0, 255).astype(np.uint8),
                     np.clip(r, 0, 255).astype(np.uint8)])
    alpha = alpha.astype(np.uint8)

    # 全透明区的 RGB 仍是底色。贴图做 mip / 双线性过滤时它会渗到边缘上（绿边），
    # 所以用前景均值把背景填掉——alpha 不变，只换看不见的那部分颜色。
    opaque = alpha > 128
    if opaque.any():
        fill = rgb[opaque].reshape(-1, 3).mean(axis=0).astype(np.uint8)
        rgb[alpha < 8] = fill
    return np.dstack([rgb, alpha])


def tight_crop(bgra: np.ndarray, pad_ratio: float = 0.06) -> np.ndarray:
    """按非透明像素紧裁并留边居中。喂给图生 3D 前统一构图，减少尺度歧义。"""
    alpha = bgra[..., 3]
    ys, xs = np.where(alpha > 16)
    if len(xs) == 0:
        return bgra
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    crop = bgra[y0:y1 + 1, x0:x1 + 1]
    h, w = crop.shape[:2]
    pad = int(max(h, w) * pad_ratio)
    side = max(h, w) + 2 * pad
    canvas = np.zeros((side, side, 4), np.uint8)
    oy, ox = (side - h) // 2, (side - w) // 2
    canvas[oy:oy + h, ox:ox + w] = crop
    return canvas


def bgra_to_rgba_png(bgra: np.ndarray) -> np.ndarray:
    """cv2 的 BGRA → 保存 PNG 需要的 RGBA。落盘前调用，避免下游通道翻转。"""
    b, g, r, a = cv2.split(bgra)
    return cv2.merge([r, g, b, a])


# ---------------------------------------------------------------- 对齐
def align_back(out_bgr: np.ndarray, ref_bgr: np.ndarray) -> np.ndarray:
    """把编辑输出对齐回输入坐标系。ECC 不收敛则退 ORB+RANSAC 仿射。"""
    if out_bgr.shape[:2] != ref_bgr.shape[:2]:
        out_bgr = cv2.resize(out_bgr, (ref_bgr.shape[1], ref_bgr.shape[0]),
                             interpolation=cv2.INTER_AREA)
    g1 = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    g2 = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cv2.findTransformECC(g1, g2, warp, cv2.MOTION_AFFINE,
                             (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6),
                             None, 5)
    except cv2.error:
        warp = _orb_affine(g1, g2)
        if warp is None:
            return out_bgr                     # 两法皆败：原样返回，不硬扭
    h, w = ref_bgr.shape[:2]
    return cv2.warpAffine(out_bgr, warp, (w, h),
                          flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)


def _orb_affine(g1: np.ndarray, g2: np.ndarray) -> np.ndarray | None:
    a = (g1 * 255).astype(np.uint8)
    b = (g2 * 255).astype(np.uint8)
    orb = cv2.ORB_create(2000)
    k1, d1 = orb.detectAndCompute(a, None)
    k2, d2 = orb.detectAndCompute(b, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
    if len(matches) < 8:
        return None
    matches = sorted(matches, key=lambda m: m.distance)[:200]
    src = np.float32([k1[m.queryIdx].pt for m in matches])
    dst = np.float32([k2[m.trainIdx].pt for m in matches])
    warp, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC)
    return warp.astype(np.float32) if warp is not None else None


# ---------------------------------------------------------------- 无缝平铺
def offset_half(img: np.ndarray) -> np.ndarray:
    """半幅滚动：把四边接缝搬到画面正中的十字上，便于定点修补与检验。"""
    h, w = img.shape[:2]
    return np.roll(np.roll(img, h // 2, axis=0), w // 2, axis=1)


def seam_score(tile_bgr: np.ndarray, band: int = 8) -> float:
    """接缝能量比：十字带梯度均值 / 其余区域梯度均值。<1.25 判过（阈值需实测标定）。"""
    h, w = tile_bgr.shape[:2]
    rolled = offset_half(tile_bgr)
    g = cv2.cvtColor(rolled, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mag = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    cross = np.zeros((h, w), bool)
    cross[h // 2 - band:h // 2 + band, :] = True
    cross[:, w // 2 - band:w // 2 + band] = True
    rest = mag[~cross].mean()
    return float(mag[cross].mean() / (rest + 1e-6))


def blend_cross_band(base: np.ndarray, patched: np.ndarray, band: int = 48) -> np.ndarray:
    """只把修补图的中心十字带（羽化）合成回原图。

    模型没有 mask 输入、只能整图重绘，这一步把不可控的全局漂移物理限制在接缝带内。
    """
    h, w = base.shape[:2]
    mask = np.zeros((h, w), np.float32)
    mask[h // 2 - band:h // 2 + band, :] = 1.0
    mask[:, w // 2 - band:w // 2 + band] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), band / 3.0)[..., None]
    return (base.astype(np.float32) * (1 - mask)
            + patched.astype(np.float32) * mask).astype(np.uint8)


def rectify_plane(img_bgr: np.ndarray, quad: np.ndarray, out_size: int = 1024) -> np.ndarray:
    """按四点单应做确定性去透视。透视矫正不交给生成模型，减少它的自由度。"""
    dst = np.float32([[0, 0], [out_size, 0], [out_size, out_size], [0, out_size]])
    M = cv2.getPerspectiveTransform(np.float32(quad), dst)
    return cv2.warpPerspective(img_bgr, M, (out_size, out_size))


def largest_inner_rect(mask: np.ndarray, downscale: int = 4
                       ) -> tuple[int, int, int, int] | None:
    """mask 内的最大内接轴对齐矩形（逐行直方图 + 单调栈）。

    用来从墙面/地面的分割 mask 里裁一块干净的、不含家具的表面。4K mask 上逐像素
    跑是 O(H·W) 的 Python 循环，太慢——先降采样求解再把坐标放大回去，精度对
    「裁一块材质」这个用途绰绰有余。
    """
    if mask is None or mask.size == 0 or not (mask > 0).any():
        return None
    small = mask[::downscale, ::downscale]
    m = (small > 0).astype(np.int32)
    h, w = m.shape
    heights = np.zeros(w, np.int32)
    best = (0, 0, 0, 0, 0)                      # area, x0, y0, x1, y1

    for y in range(h):
        heights = np.where(m[y] > 0, heights + 1, 0)
        stack: list[int] = []                   # 存列下标，高度单调递增
        for x in range(w + 1):
            cur = int(heights[x]) if x < w else 0
            while stack and int(heights[stack[-1]]) >= cur:
                hh = int(heights[stack.pop()])
                left = stack[-1] + 1 if stack else 0
                ww = x - left
                area = hh * ww
                if area > best[0]:
                    best = (area, left, y - hh + 1, left + ww, y + 1)
            stack.append(x)

    if best[0] <= 0:
        return None
    _, x0, y0, x1, y1 = best
    return (x0 * downscale, y0 * downscale, x1 * downscale, y1 * downscale)


# ---------------------------------------------------------------- PBR 推导
def derive_height(albedo_bgr: np.ndarray, levels: int = 5) -> np.ndarray:
    """多尺度带通：高斯金字塔逐层差分加权和。低频当大起伏、高频当细节。返回 0~1 float。"""
    weights = (0.05, 0.10, 0.20, 0.30, 0.35)
    g = cv2.cvtColor(albedo_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    out = np.zeros_like(g)
    cur = g
    for i in range(min(levels, len(weights))):
        blur = cv2.GaussianBlur(cur, (0, 0), 2.0)
        band = cv2.resize(cur - blur, (g.shape[1], g.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
        out += weights[i] * band
        cur = cv2.pyrDown(blur) if min(cur.shape[:2]) > 32 else blur
    # NumPy 2.0 起 ndarray.ptp 已移除，必须用 np.ptp
    return (out - out.min()) / (np.ptp(out) + 1e-6)


def height_to_normal(h: np.ndarray, strength: float = 2.0) -> np.ndarray:
    """Sobel 梯度 → 单位法线场，按 UE 期望的 DirectX(-Y) 约定编码。

    图像 y 轴向下，Sobel 的 gy 是 ∂h/∂row；DirectX 约定 G 通道取 -gy，取 +gy
    得到的是 OpenGL 约定、在 UE 里凹凸会上下颠倒。返回 BGR（cv2 保存序）。
    """
    gx = cv2.Sobel(h, cv2.CV_32F, 1, 0, ksize=3) * strength
    gy = cv2.Sobel(h, cv2.CV_32F, 0, 1, ksize=3) * strength
    n = np.dstack([-gx, -gy, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    rgb = ((n * 0.5 + 0.5) * 255).astype(np.uint8)
    return rgb[..., ::-1]                     # RGB → BGR


ROUGH_BASE = {"glazed_tile": 0.15, "ceramic": 0.15, "concrete": 0.85,
              "painted_metal": 0.40, "brushed_metal": 0.35, "metal": 0.35,
              "plastic": 0.55, "paper": 0.75, "wood": 0.60, "fabric": 0.80,
              "default": 0.65}
METALLIC_CLASSES = {"metal", "brushed_metal", "painted_metal", "steel", "aluminium"}


def derive_roughness(albedo_bgr: np.ndarray, height: np.ndarray,
                     mat_class: str, jitter: float = 0.12) -> np.ndarray:
    base = ROUGH_BASE.get(mat_class, ROUGH_BASE["default"])
    v = cv2.cvtColor(albedo_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    rough = base + jitter * (0.5 - (v - float(v.mean())))     # 亮处(高光)略降粗糙度
    if mat_class in ("glazed_tile", "ceramic"):               # 勾缝: height 低分位区提粗
        rough[height < np.quantile(height, 0.12)] = 0.70
    return (np.clip(rough, 0.0, 1.0) * 255).astype(np.uint8)


def derive_metallic(shape: tuple[int, int], mat_class: str) -> np.ndarray:
    val = 255 if mat_class in METALLIC_CLASSES else 0
    return np.full(shape, val, np.uint8)


def pack_orm(ao: np.ndarray, rough: np.ndarray, metal: np.ndarray) -> np.ndarray:
    """UE 约定 ORM = R:AO / G:Roughness / B:Metallic。cv2 存 BGR，故顺序反过来。"""
    return cv2.merge([metal, rough, ao])


def ao_from_height(height: np.ndarray, radius: int = 16) -> np.ndarray:
    """廉价 AO：高度低于邻域均值的地方变暗。够用于平铺材质的接缝阴影。"""
    blur = cv2.GaussianBlur(height, (0, 0), radius)
    ao = np.clip(1.0 - (blur - height) * 2.0, 0.3, 1.0)
    return (ao * 255).astype(np.uint8)


def to_pow2(img: np.ndarray, cap: int = 2048) -> np.ndarray:
    """UE 不给非 2 的幂次贴图生成 mip。就近向下取幂，避免上采样造假细节。"""
    def p2(n: int) -> int:
        p = 1
        while p * 2 <= n:
            p *= 2
        return p
    h, w = img.shape[:2]
    nw, nh = min(p2(w), cap), min(p2(h), cap)
    if (nw, nh) == (w, h):
        return img
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
