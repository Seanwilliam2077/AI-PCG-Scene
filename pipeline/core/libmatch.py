"""资产库匹配引擎：感知对象 → 库模块。纯函数，s04 调用，可单测。

四级瀑布：
  L0 人工覆盖（match_overrides.json，存在即无条件采纳——与 calib_manual 同语义）
  L1 精确类别命中 + L2 同类变体确定性轮换（实例按 id 排序 round-robin，
     候选先按三轴形状相似度排序——兼顾"形状对"与"同类多样性"，复跑字节一致）
  L3 视觉 LLM 兜底（参考图 crop vs 候选预览拼图，confidence 达标才采纳）
  L4 降级 proxy（尺寸永远正确，场景比例不塌）

尺寸对齐：权威尺寸只认库网格实测 bbox；缩放取三轴比中位数（抗单轴离群），
带四档畸形保护（WARN / DANGER / ABSURD / SHAPE_MISMATCH）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_taxonomy(root: Path) -> dict:
    return yaml.safe_load((root / "taxonomy.yaml").read_text(encoding="utf-8"))


def normalize_category(label: str, taxonomy: dict) -> str:
    """自由标签 → 受控类别。先精确后包含，同义词按键长降序（长词优先）。"""
    s = (label or "").strip().lower()
    cats = set(taxonomy["categories"])
    if s in cats:
        return s
    syn: dict[str, str] = taxonomy.get("synonyms") or {}
    if s in syn:
        return syn[s]
    for key in sorted(syn, key=len, reverse=True):
        if key in s:
            return syn[key]
    for cat in cats:
        if cat in s:
            return cat
    return "misc"


def _shape_dist(size_cm: list[float], bbox_cm: list[float]) -> float:
    """三轴比例的形状距离（对整体缩放不敏感）。排序键，越小越像。"""
    import math
    eps = 1e-6
    a = sorted(max(eps, float(v)) for v in size_cm)
    b = sorted(max(eps, float(v)) for v in bbox_cm)
    return sum(abs(math.log(a[i] / b[i])) for i in range(3))


def compute_scale(size_cm: list[float], bbox_cm: list[float],
                  keep_xy_uniform: bool = False) -> tuple[list[float], list[str]]:
    """布局尺寸 / 库网格 bbox → 逐轴缩放 + 畸形标志。

    keep_xy_uniform：屏幕类保持 x/y 等比（4:3 的 CRT 不许被拉成宽屏）。
    """
    eps = 1e-6
    s = [float(size_cm[i]) / max(eps, float(bbox_cm[i])) for i in range(3)]
    k = sorted(s)[1]                                  # 中位轴比：保形且抗离群
    flags: list[str] = []
    if k > 10.0 or k < 0.1:
        return [k, k, k], ["SCALE_ABSURD"]            # 10 倍级：判定误匹配
    if k > 1.7:
        # 放大上限软钳：库资产按真实尺寸建模，布局把桌子解算成 4.6m 多半是
        # 深度高估而不是真有巨桌——放大超过 1.7 倍宁可信资产不信解算
        # （缩小方向不钳：小物件确实存在）。实测近景巨桌就是 2.58x 惹的祸。
        k = 1.7
        flags.append("SCALE_CLAMPED")
    if not (0.2 <= k <= 5.0):
        flags.append("SCALE_DANGER")
    elif not (0.5 <= k <= 2.0):
        flags.append("SCALE_WARN")
    if max(s) / max(min(s), eps) > 3.0:
        flags.append("SHAPE_MISMATCH")                # 拿主机塔配长桌的形态
    if keep_xy_uniform:
        kxy = (s[0] + s[1]) / 2.0
        scale = [kxy, kxy, s[2]]
    else:
        scale = [k, k, k]
    return [round(v, 4) for v in scale], flags


def eligible_modules(registry: dict, category: str) -> list[str]:
    """某类别的可用模块（enabled 且非平面印刷品）。"""
    out = []
    for mid in registry.get("category_index", {}).get(category, []):
        m = registry["modules"].get(mid) or {}
        if m.get("enabled", True) and not m.get("is_flat_print"):
            out.append(mid)
    return out


def match_objects(objects: list[dict], registry: dict, taxonomy: dict,
                  overrides: dict | None = None,
                  llm_match=None) -> list[dict]:
    """核心匹配。objects 是 layout 的 hero 对象；返回逐对象的 match 记录。

    llm_match(obj) -> (module_id | None, confidence, reason)：L3 兜底回调，
    由 s04 注入（带 vlm_gateway 与参考图上下文）；None 表示不启用。
    """
    overrides = overrides or {}
    by_instance: dict[str, str] = overrides.get("by_instance") or {}
    screen_cats = set(taxonomy.get("screen_categories") or [])
    cursors: dict[str, int] = {}
    report: list[dict] = []

    for obj in sorted(objects, key=lambda o: o["id"]):
        oid = obj["id"]
        cat = normalize_category(obj.get("category") or obj.get("label", ""), taxonomy)
        rec: dict[str, Any] = {"object_id": oid, "category": cat, "module": None,
                               "method": "proxy_fallback", "confidence": 0.0,
                               "scale": None, "flags": []}

        def _accept(mid: str, method: str, conf: float) -> bool:
            mod = registry["modules"][mid]
            scale, flags = compute_scale(obj["size_cm"], mod["mesh_bbox_cm"],
                                         keep_xy_uniform=cat in screen_cats)
            if "SCALE_ABSURD" in flags and method != "manual_override":
                return False                          # 误匹配，不落地畸形网格
            rec.update(module=mid, method=method, confidence=conf,
                       scale=scale, flags=flags)
            return True

        # L0 人工覆盖：无条件采纳（含 ABSURD——人说了算）
        if oid in by_instance and by_instance[oid] in registry["modules"]:
            _accept(by_instance[oid], "manual_override", 1.0)
            report.append(rec)
            continue

        # L1/L2 类别命中 + 变体轮换。
        # 两类对象刻意不进库匹配，直接 proxy（贴参考图 crop 的盒体）：
        #   misc——"未知配未知"会把结构柱配成大理石杂物盒（实测翻过车）；
        #   cable（含 pipe/conduit 折叠）——细长物配上线缆盘资产，20 个实例
        #   沿墙一摆就是一面蓝色假墙（实测也翻过车）。细长 proxy 盒反而像管线。
        NO_LIB = {"misc", "cable"}
        cands = [] if cat in NO_LIB else eligible_modules(registry, cat)
        if cands:
            cands.sort(key=lambda m: _shape_dist(obj["size_cm"],
                                                 registry["modules"][m]["mesh_bbox_cm"]))
            idx = cursors.get(cat, 0)
            picked = False
            for step in range(len(cands)):            # 轮换起点开始逐个试（跳过 ABSURD）
                mid = cands[(idx + step) % len(cands)]
                if _accept(mid, "exact_category", 1.0):
                    cursors[cat] = idx + step + 1
                    picked = True
                    break
            if picked:
                report.append(rec)
                continue

        # L3 视觉 LLM 兜底（NO_LIB 类别不进：它们的归宿就是 proxy）
        if llm_match is not None and cat not in NO_LIB:
            try:
                mid, conf, reason = llm_match(obj)
                if mid and mid in registry["modules"] and conf >= 0.6 \
                        and _accept(mid, "llm_visual", conf):
                    rec["reason"] = reason
                    report.append(rec)
                    continue
            except Exception as e:                    # 兜底失败不阻塞——落 proxy
                rec["flags"].append(f"llm_error:{str(e)[:80]}")

        # L4 proxy
        rec["flags"].append("NO_MATCH")
        report.append(rec)
    return report
