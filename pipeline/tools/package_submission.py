#!/usr/bin/env python3
"""交付打包：白名单收集 → Key 泄漏扫描 → 体积构成表 → zip。

500MB 预算靠三件事守住：白名单制（Saved/Intermediate/DDC/源资产永不入包）、
资产库只带 uasset 不带 OBJ/PNG 源、打包前把体积构成表打给人看。
Key 泄漏检查兜底"手一滑把密钥写进 DefaultXXX.ini 随包发出去"的事故。

用法：
    python tools/package_submission.py                 # 全量打包
    python tools/package_submission.py --dry-run       # 只算体积不写 zip
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

PIPE = Path(r"C:\AI Pipeline Test\pipeline")
PROJ = Path(r"C:\AI Pipeline Test\AISceneBuilderDemo")
OUT_ZIP = Path(r"C:\AI Pipeline Test\AISceneBuilderDemo_submission.zip")

# ---------------- 白名单（相对工程根） ----------------
INCLUDE = [
    "AISceneBuilderDemo.uproject",
    "QUICKSTART.md",
    "SKILL.md",                  # 评审辅助：丢给任意 coding agent 即可引导跑通全流程
    "技术方案报告.pdf",
    "补充说明-复现差距分析.pdf",
    "Config",
    "Content",
    "Plugins/AISceneBuilder",
]
# 交付需要预编译二进制（评审机可能没有 MSVC）
BINARIES = ["Binaries/Win64", "Plugins/AISceneBuilder/Binaries/Win64"]

EXCLUDE_DIR_NAMES = {"Intermediate", "Saved", "DerivedDataCache", "__pycache__",
                     ".vs", ".git", "output"}
EXCLUDE_SUFFIXES = {".pdb", ".pyc", ".obj", ".ilk", ".exp", ".sarif"}
# 插件 Python 里的运行产物与开发期工具不进包；cache/vlm_gateway 是离线回放的生命线，必须进。
# Taos_Map 是工程遗留的无关关卡（53MB 的 world-partition 外置 actor），整树剔除；
# 交付关卡 GenScene 不用外置 actor，不受影响。
EXCLUDE_REL_PATTERNS = [
    re.compile(r"Plugins/AISceneBuilder/Python/output/", re.I),
    # 开发期工具不入包：打包/同步脚本对评审无用，且泄漏扫描黑名单不外发
    re.compile(r"Plugins/AISceneBuilder/Python/tools/(package_submission|sync_plugin)\.py", re.I),
    # 云端 3D provider 模块不随包分发；库/proxy 路径在 mesh_proxy.py
    re.compile(r"Plugins/AISceneBuilder/Python/core/providers3d\.py", re.I),
    # refs/ 必须入包：QUICKSTART 指引评审用它跑管线（几 MB，不排除）
    re.compile(r"Content/__ExternalActors__/Taos_Map/", re.I),
    re.compile(r"Content/__ExternalObjects__/Taos_Map/", re.I),
    re.compile(r"Content/Taos_Map", re.I),
    re.compile(r"Content/Tao/", re.I),
]

KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret[_-]?key|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    re.I)
# 服务端点/域名一律不得入包：命中即中止打包。
# 黑名单本身也是敏感信息（枚举它等于公布它），因此不写死在代码里——
# 从环境变量 PACKAGE_DOMAIN_BLACKLIST 读取 `|` 分隔的正则片段；未配置时
# 只保留通用 IP:port 规则，仍能拦住最常见的手滑。
_EXTRA = os.environ.get("PACKAGE_DOMAIN_BLACKLIST", "").strip()
DOMAIN_PATTERN = re.compile(
    r"(" + (_EXTRA + "|" if _EXTRA else "") +
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5})",
    re.I)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for rel in INCLUDE + BINARIES:
        p = PROJ / rel
        if p.is_file():
            files.append(p)
            continue
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            relstr = f.relative_to(PROJ).as_posix()
            if set(f.relative_to(PROJ).parts) & EXCLUDE_DIR_NAMES:
                continue
            if f.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            if any(pat.search(relstr) for pat in EXCLUDE_REL_PATTERNS):
                continue
            files.append(f)
    return sorted(set(files))


def scan_keys(files: list[Path]) -> list[str]:
    """扫文本类文件里的疑似密钥与服务端点。命中即中止打包。"""
    hits: list[str] = []
    for f in files:
        if f.suffix.lower() not in {".ini", ".yaml", ".yml", ".json", ".jsonl",
                                    ".py", ".txt", ".md", ".cfg", ".xml",
                                    ".html", ".h", ".cpp", ".cs"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in KEY_PATTERN.finditer(text):
            frag = m.group(0)
            # 排除明显的占位/空值/文档描述
            if re.search(r"(''|\"\"|<|\{|示例|placeholder|your[_-]?key)", frag, re.I):
                continue
            hits.append(f"{f.relative_to(PROJ)}: KEY {frag[:60]}…")
        for m in DOMAIN_PATTERN.finditer(text):
            s = max(0, m.start() - 30)
            hits.append(f"{f.relative_to(PROJ)}: DOMAIN …{text[s:m.end()][:70]}…")
    return hits


def size_table(files: list[Path]) -> tuple[dict[str, int], int]:
    buckets: dict[str, int] = defaultdict(int)
    total = 0
    for f in files:
        rel = f.relative_to(PROJ).as_posix()
        size = f.stat().st_size
        total += size
        if rel.startswith("Content/AutoScene/AssetLib"):
            key = "Content/AutoScene/AssetLib（资产库 uasset）"
        elif rel.startswith("Content"):
            key = "Content（地图与其余资产）"
        elif "Python/cache/vlm_gateway" in rel:
            key = "插件 vlm_gateway 缓存（离线回放）"
        elif "Content/Python" in rel:
            key = "插件 vendor 依赖（numpy/cv2/yaml）"
        elif rel.startswith("Plugins"):
            key = "插件（代码+其他）"
        elif "Binaries" in rel:
            key = "预编译二进制"
        else:
            key = "工程根（Config/uproject）"
        buckets[key] += size
    return dict(buckets), total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = iter_files()
    hits = scan_keys(files)
    if hits:
        print("[FAIL] 检出疑似密钥，打包中止：")
        for h in hits[:10]:
            print(f"  {h}")
        return 2

    buckets, total = size_table(files)
    print(f"文件数 {len(files)}，总计 {total / 1e6:,.1f} MB（预算 500 MB）")
    for key, size in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {size / 1e6:9,.1f} MB  {key}")
    if total > 500e6:
        print("[warn] 超出 500MB 预算——考虑：资产库子集打包 / 贴图再降档 / 剔除大地图")

    if args.dry_run:
        return 0

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in files:
            zf.write(f, f"AISceneBuilderDemo/{f.relative_to(PROJ).as_posix()}")
    print(f"[ ok ] {OUT_ZIP}（{OUT_ZIP.stat().st_size / 1e6:,.1f} MB 压缩后）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
