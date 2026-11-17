# -*- coding: utf-8 -*-
"""清理「断点说完成但数据 0 行」的假完成断点（默认 dry-run）。

为什么需要（2026-11-17 index_weight P0 #2 实证）
------------------------------------------------
Tushare `index_weight` 服务端当日累计配额耗尽后**静默返回空 DataFrame**
（不抛异常）。`ts_call_with_retry` 将 None/空按三态纪律记成「空结果」，
`on_result_month` 随即 `cp.mark_done` ⇒ 断点完成、库内 0 行。
实测 38 个指数 × 130 期 = 4,820 个假断点键，且「失败=0」完全掩盖。
跨日配额重置后源端同一日期可正常取到 100 行，证明不是源端真空。

判据（保守）：
  断点键所属 code 在 parquet 中 **0 行** → 该 code 全部断点键判假完成并清除。
  已有数据的 code 一律不动（即使断点期数 ≠ 数据期数，可能是源端真空）。

用法
----
    python scripts/clean_fake_checkpoint.py --table index_weight          # 预览
    python scripts/clean_fake_checkpoint.py --table index_weight --apply  # 备份后写
"""
import argparse
import collections
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(os.path.dirname(_ROOT), "赤潮", "docs")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

from config import MARKET_DATA_DIR  # noqa: E402

CKPT = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
ARCHIVE = os.path.join(os.path.dirname(_ROOT), "备份", "归档文件")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="index_weight")
    ap.add_argument("--code-col", default="index_code")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    a = ap.parse_args()

    if not os.path.exists(CKPT):
        print(f"[FATAL] 断点不存在: {CKPT}")
        return 2
    with open(CKPT, encoding="utf-8") as f:
        ck = json.load(f)
    node = ck.get(a.table)
    if not isinstance(node, dict):
        print(f"[FATAL] 断点中无表 {a.table}")
        return 2
    keys = list(node.get("completed", []))
    if not keys:
        print(f"[FATAL] {a.table} 断点为空")
        return 2

    parquet = os.path.join(MARKET_DATA_DIR, a.table, f"{a.table}.parquet")
    if not os.path.exists(parquet):
        print(f"[FATAL] 数据文件不存在: {parquet}")
        return 2
    df = pd.read_parquet(parquet, columns=[a.code_col])
    dc = df[a.code_col].value_counts()

    kc = collections.Counter(k.split("|", 1)[0] for k in keys)
    fake = sorted(c for c, n in kc.items() if dc.get(c, 0) == 0)
    n_fake_keys = sum(kc[c] for c in fake)
    kept = [k for k in keys if k.split("|", 1)[0] not in set(fake)]

    print(f"表={a.table}  断点键={len(keys):,}  数据行={len(df):,}  数据 code={len(dc)}")
    print(f"假完成 code={len(fake)}  假键={n_fake_keys:,}  清理后保留键={len(kept):,}")
    print(f"假完成样例: {fake[:10]}")
    if not fake:
        print("✅ 无需清理")
        return 0
    if not a.apply:
        print("\n[dry-run] 未写任何文件。确认后加 --apply（会先备份断点）")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(ARCHIVE, f"fake-ckpt-{a.table}-{stamp}")
    os.makedirs(dest, exist_ok=True)
    bak = os.path.join(dest, os.path.basename(CKPT))
    shutil.copy2(CKPT, bak)
    print(f"\n[备份] {bak}")

    node["completed"] = kept
    tmp = CKPT + ".tmp-clean"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ck, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CKPT)

    with open(CKPT, encoding="utf-8") as f:
        after = json.load(f)
    got = len(after.get(a.table, {}).get("completed", []))
    other = {k: len(v.get("completed", [])) for k, v in after.items()
             if k != a.table and isinstance(v, dict)}
    print(f"[完成] {a.table} 断点 {len(keys):,} -> {got:,}（应={len(kept):,}）")
    print(f"[核对] 其他表键数: {len(other)} 张（未改动）")
    return 0 if got == len(kept) else 1


if __name__ == "__main__":
    sys.exit(main())
