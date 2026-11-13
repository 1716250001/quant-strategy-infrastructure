#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/db/ckpt_clean_once.py — 清理 page 域里的 `once` 死键（P3-5 尾账）

依据：18 号 §458 W-4。`wz_index` / `stk_seasoned` 的 `completed` 混入历史遗留
`'once'`，而 registry 的 key_space 是 `page`（合法键只有数字页码）⇒ `db audit`
A2 各报 extra 1。

⛔ **默认干跑**；`--apply` 才写，且写前自动备份 `.bak-once-<时间戳>`。
⛔ **只删严格等于 `'once'` 的键**，其他键一个都不动。
⛔ 真跑归赤潮（§454 施工面切分）；本脚本只提供能力。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import MARKET_DATA_DIR
from config.checkpoint_registry import DOMAINS

CKPT = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
BAD_KEY = "once"
# 只处理 registry 明确声明为 page 域的表：合法键是页码，`once` 必为死键。
PAGE_TABLES = sorted({
    t for d in DOMAINS
    if d.library == "market_data" and d.key_space == "page"
    for t in d.tables
})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理 page 域的 'once' 死键（默认干跑）")
    ap.add_argument("--apply", action="store_true",
                    help="真删（写前自动备份 .bak-once-<时间戳>）")
    ap.add_argument("--ckpt", default=CKPT, help="断点文件路径（默认主库）")
    args = ap.parse_args(argv)

    path = args.ckpt
    if not os.path.isfile(path):
        print(f"[FAIL] 断点不存在: {path}")
        return 2
    with open(path, encoding="utf-8") as f:
        cp = json.load(f)

    hits: list[tuple[str, list]] = []
    for name in PAGE_TABLES:
        rec = cp.get(name)
        if not isinstance(rec, dict):
            continue
        keys = rec.get("completed")
        if not isinstance(keys, list):
            continue
        if BAD_KEY in keys:
            hits.append((name, keys))

    if not hits:
        print(f"[OK] 未发现 '{BAD_KEY}' 死键（page 域 {len(PAGE_TABLES)} 张表）")
        return 0

    print(f"断点: {path}")
    print(f"page 域表 {len(PAGE_TABLES)} 张；命中 '{BAD_KEY}' 死键 {len(hits)} 张：")
    for name, keys in hits:
        remain = [k for k in keys if k != BAD_KEY]
        print(f"  - {name}: 现有 {len(keys)} 键 {keys} → 将删 ['{BAD_KEY}']，剩余 {len(remain)} 键")

    if not args.apply:
        print("[DRY-RUN] 干跑，未写任何文件。真跑: --apply（归赤潮执行）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path + f".bak-once-{stamp}"
    shutil.copy2(path, bak)
    for name, _keys in hits:
        cp[name]["completed"] = [k for k in cp[name]["completed"] if k != BAD_KEY]

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    print(f"[OK] 已删 {len(hits)} 个 '{BAD_KEY}' 键；备份 {os.path.basename(bak)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
