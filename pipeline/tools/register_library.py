#!/usr/bin/env python3
"""s00 资产库注册：AssetLib_processed → library_registry.json。

每个模块 = 视觉 LLM 对 preview.png 打标（走 vlm_gateway 哈希缓存，复跑免费、可交付）
+ 预处理 stats.json 的实测几何。LLM 预估尺寸 vs 网格实测尺寸交叉校验，比例差
大的打 needs_review 进人工队列。human_verified=true 的条目重跑打标不覆盖——
人的判断永远压过 LLM。

用法：
    python tools/register_library.py               # 增量（已核对条目跳过）
    python tools/register_library.py --retag       # 未核对条目全部重打标
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.libmatch import load_taxonomy  # noqa: E402
from core.vlm_gateway import GatewayClient  # noqa: E402

LIB_ROOT = Path(r"C:\AI Pipeline Test\AssetLib_processed")
REGISTRY = LIB_ROOT / "library_registry.json"


def _validate_tag(obj) -> None:
    if not isinstance(obj, dict):
        raise ValueError("不是 JSON object")
    for key in ("category", "is_screen", "is_flat_print", "est_size_cm", "confidence"):
        if key not in obj:
            raise ValueError(f"缺字段 {key}")
    if not (isinstance(obj["est_size_cm"], list) and len(obj["est_size_cm"]) == 3):
        raise ValueError("est_size_cm 应为三元数组")


def _size_ratio(a: list[float], b: list[float]) -> float:
    """两组尺寸的最大轴向偏差倍数（>1）。用于 LLM 估计 vs 实测的交叉校验。"""
    eps = 1e-6
    sa, sb = sorted(max(eps, float(v)) for v in a), sorted(max(eps, float(v)) for v in b)
    return max(max(sa[i], sb[i]) / min(sa[i], sb[i]) for i in range(3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retag", action="store_true", help="未人工核对的条目重新打标")
    ap.add_argument("--config", default=str(ROOT / "configs" / "pipeline.yaml"))
    args = ap.parse_args()

    import yaml
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    taxonomy = load_taxonomy(ROOT)
    cats = taxonomy["categories"]

    old: dict = {}
    if REGISTRY.is_file():
        old = json.loads(REGISTRY.read_text(encoding="utf-8"))

    vlm_gateway = GatewayClient.from_config(cfg, ROOT, run_dir=LIB_ROOT, stage="s00_register")
    prompt_tmpl = (ROOT / "prompts" / "s00_tag_asset.txt").read_text(encoding="utf-8")
    prompt = prompt_tmpl.format(categories=", ".join(cats))

    modules: dict = {}
    tagged = kept = review = 0
    for stats_path in sorted(LIB_ROOT.glob("*/stats.json")):
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        sid = stats["id"]
        mod_dir = stats_path.parent
        prev = (old.get("modules") or {}).get(sid) or {}

        # 人工核对过的语义字段原样保留；其余重新打标（或沿用旧标签）
        if prev.get("human_verified") and not args.retag:
            entry = dict(prev)
            kept += 1
        elif prev.get("category") and not args.retag:
            entry = dict(prev)
            kept += 1
        else:
            tag = vlm_gateway.chat_json(prompt, [mod_dir / "preview.png"],
                                  model=cfg["models"]["vision"],
                                  validator=_validate_tag)
            cat = tag["category"] if tag["category"] in cats else "misc"
            entry = {
                "category": cat,
                "variant_desc": str(tag.get("variant_desc") or "")[:120],
                "aliases": [str(a).lower() for a in (tag.get("aliases") or [])][:3],
                "is_screen": bool(tag.get("is_screen")),
                "is_flat_print": bool(tag.get("is_flat_print")),
                "poster_text": tag.get("poster_text"),
                "est_size_cm": [round(float(v), 1) for v in tag["est_size_cm"]],
                "tag_confidence": round(float(tag.get("confidence", 0.0)), 2),
                "human_verified": False,
            }
            tagged += 1

        # 几何字段永远以实测为准（人不改、LLM 不碰）
        entry.update({
            "id": sid, "dir": str(mod_dir),
            "mesh": stats["files"]["mesh"],
            "textures": {"BC": stats["files"]["BC"], "N": stats["files"]["N"],
                         "ORM": stats["files"]["ORM"]},
            "preview": stats["files"].get("preview", "preview.png"),
            "mesh_bbox_cm": stats["bbox_cm"],
            "tri_count": stats["tri_count"],
            "pivot": "bottom_center",
            "enabled": entry.get("enabled", True),
        })

        # 交叉校验：LLM 常识尺寸 vs 网格实测（仅提示，不参与缩放）
        ratio = _size_ratio(entry["est_size_cm"], stats["bbox_cm"])
        entry["needs_review"] = bool(ratio > 2.0 or entry["category"] == "misc"
                                     or entry.get("tag_confidence", 1.0) < 0.5)
        entry["review_reason"] = (f"est/mesh 尺寸差 {ratio:.1f}x" if ratio > 2.0 else
                                  ("类别 misc" if entry["category"] == "misc" else
                                   ("置信度低" if entry.get("tag_confidence", 1.0) < 0.5
                                    else None)))
        if entry["needs_review"]:
            review += 1
        modules[sid] = entry

    index: dict[str, list[str]] = {}
    for sid, m in modules.items():
        index.setdefault(m["category"], []).append(sid)

    registry = {
        "version": int(old.get("version", 0)) + 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "taxonomy_file": "taxonomy.yaml",
        "tagger": {"backend": f"vlm_gateway/{cfg['models']['vision']}"},
        "modules": modules,
        "category_index": {k: sorted(v) for k, v in sorted(index.items())},
    }
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"[ ok ] 注册 {len(modules)} 个模块（新打标 {tagged} / 沿用 {kept} / "
          f"待人工复核 {review}）→ {REGISTRY}")
    print(f"       类别分布：{ {k: len(v) for k, v in registry['category_index'].items()} }")
    print(f"       vlm_gateway 统计：{vlm_gateway.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
