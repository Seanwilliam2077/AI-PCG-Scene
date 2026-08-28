"""Export the measured data that drives the methodology document's interactions.

Every control on docs/方法论-单图到三维美术场景.html has to be backed by something
that was actually measured in this repository, otherwise it is decoration. This
pulls those numbers out of the run artefacts and the asset registry so the page
can inline them.

    python tools/export_method_data.py [--out process/figures/method/method_data.json]

Sources (all read-only):
  process/run_fd6e434f/perception.json   70 detections with their provenance flags
  process/run_fd6e434f/match_report.json 30 match records, methods and flags
  <AssetLib_processed>/library_registry.json  39 modules, est vs measured size
"""
import argparse
import io
import json
import os
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_CANDIDATES = [
    r"C:\AI Pipeline Test\AssetLib_processed\library_registry.json",
    os.path.join(ROOT, "process", "AssetLib_processed", "library_registry.json"),
]

# the four fixed tiles used by the quadrant recall pass, as fractions of the frame
TILES = [(0.0, 0.0, 0.6, 0.6), (0.4, 0.0, 1.0, 0.6),
         (0.0, 0.4, 0.6, 1.0), (0.4, 0.4, 1.0, 1.0)]


def provenance(d):
    """Which recall pass produced this detection. Order matters: a box can only
    come from one pass, and the flags are written by exactly one of them."""
    if d.get("from_enum"):
        return "enum"
    if d.get("from_tile"):
        return "tile"
    if d.get("from_hotspot"):
        return "hotspot"
    return "whole"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "process", "figures",
                                                  "method", "method_data.json"))
    a = ap.parse_args()
    run = os.path.join(ROOT, "process", "run_fd6e434f")

    per = json.load(open(os.path.join(run, "perception.json"), encoding="utf-8"))
    W, H = per["image_size"]
    dets = []
    for i, d in enumerate(per["detections"]):
        b = d.get("bbox_2d")
        if not b or len(b) != 4:
            continue
        dets.append({
            "i": i,
            "label": d.get("label", ""),
            "src": provenance(d),
            "att": d.get("attached_to"),
            "box": [round(float(v), 4) for v in b],
            "mask_pts": len(d.get("mask_poly") or []),
        })
    by_src = Counter(x["src"] for x in dets)

    mr = json.load(open(os.path.join(run, "match_report.json"), encoding="utf-8"))
    matches = mr.get("matches", [])

    reg_path = next((p for p in REGISTRY_CANDIDATES if os.path.exists(p)), None)
    modules = []
    if reg_path:
        reg = json.load(open(reg_path, encoding="utf-8"))
        for mid, m in reg.get("modules", {}).items():
            est, bb = m.get("est_size_cm"), m.get("mesh_bbox_cm")
            if not est or not bb:
                continue
            # compare longest side to longest side: both triples are unordered
            # with respect to each other, so per-axis ratios are meaningless
            ratio = max(bb) / max(est) if max(est) else None
            modules.append({
                "id": mid, "cat": m.get("category"),
                "est_max": round(max(est), 1), "mesh_max": round(max(bb), 1),
                "ratio": round(ratio, 2) if ratio else None,
                "tris": m.get("tri_count"),
                "screen": bool(m.get("is_screen")),
                "flat": bool(m.get("is_flat_print")),
                "needs_review": bool(m.get("needs_review")),
            })
        modules.sort(key=lambda t: -(t["ratio"] or 0))

    out = {
        "image": {"w": W, "h": H, "src": "../process/figures/tri/ref_interior.jpg"},
        "detections": dets,
        "by_source": dict(by_src),
        "tiles": TILES,
        "match": {
            "n": len(matches),
            "method": dict(Counter(m.get("method") for m in matches)),
            "flags": dict(Counter(f for m in matches for f in (m.get("flags") or []))),
            "summary": mr.get("summary"),
        },
        "modules": modules,
        "module_stats": {
            "n": len(modules),
            "ratio_min": min((m["ratio"] for m in modules if m["ratio"]), default=None),
            "ratio_max": max((m["ratio"] for m in modules if m["ratio"]), default=None),
            "mesh_max_lo": min((m["mesh_max"] for m in modules), default=None),
            "mesh_max_hi": max((m["mesh_max"] for m in modules), default=None),
            "needs_review": sum(1 for m in modules if m["needs_review"]),
        },
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))

    print(f"检出 {len(dets)} 个，来源分布 {dict(by_src)}")
    print(f"匹配 {out['match']['n']} 条  方式 {out['match']['method']}  标记 {out['match']['flags']}")
    if modules:
        s = out["module_stats"]
        print(f"资产模块 {s['n']} 个  最长边实测落在 {s['mesh_max_lo']}–{s['mesh_max_hi']} cm  "
              f"est/mesh 比 {s['ratio_min']}–{s['ratio_max']}  needs_review {s['needs_review']}")
    else:
        print("!! 没找到 library_registry.json，资产那节的散点将没有数据")
    print(f"写入 {a.out}  {os.path.getsize(a.out)//1024} KB")


main()
