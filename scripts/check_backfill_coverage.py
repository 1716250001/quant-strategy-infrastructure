# -*- coding: utf-8 -*-
"""backfill 覆盖自检 — 断点键数守恒 + 数据行数不回退（引擎外兜底）。

为什么需要（2026-11-16 index_weight 校准批 P0 实证）
----------------------------------------------------
一次 `--limit 520` 跑出：断点 +1,040（8 指数×130）、数据只落 4 个指数，
另一批 **17.9 万行被静默覆盖丢失**，全程零 ERROR，`--dry-run` 还报
「待跑=0」＝假绿。根因是 backfill 两处写入（`cp.save()` 全量覆盖 +
`merge_append` 全文件读改写）都缺跨进程保护 —— 已在
`fetch/base.py:FileLock` + `fetch/backfill.py:run_backfill` 补上。

本脚本是**引擎外**的第二道网：不 import 任何取数逻辑，只读断点与 parquet，
所以任何原因（并发、残留进程、未知 bug）造成的丢失都能被抓到。

用法
----
    # 跑批前
    python scripts/check_backfill_coverage.py --table index_weight --snapshot
    # 跑批后（--expect = 本批实际完成数，取自 backfill 输出的「完成=N」）
    python scripts/check_backfill_coverage.py --table index_weight --verify --expect 1300

判据
----
① 硬 有效断点键数增量 == --expect
     有效 = completed ∩ 权威任务集（build_tasks 产出）；虚记键与非任务键不计。
② 硬 数据行数 >= 快照行数（防 merge_append 覆盖丢数据）
③ 硬 本批新增断点 code 中 ≥2 个数据 0 行 → 失败（配额耗尽批量假完成特征）；
     单个 → 警告（源端真空，直查确认）
     ⚠ 必须基于「断点新增 code」而非「数据新增 code」——后者恒有数据＝死判据
④ 软 全量列出「断点期数 ≠ 数据期数」的 code（源端发布时间边界集中在此，不作硬判据）

退出码：0 通过 / 1 硬判据失败 / 2 环境或参数错误
"""
import argparse
import collections
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_DOCS = os.path.join(os.path.dirname(_ROOT), "赤潮", "docs")
if os.path.isdir(_DOCS) and _DOCS not in sys.path:
    sys.path.insert(0, _DOCS)

import pandas as pd  # noqa: E402

from config import MARKET_DATA_DIR  # noqa: E402
from config.backfill_targets import BACKFILL_TARGETS  # noqa: E402
from fetch.backfill_spec import build_tasks  # noqa: E402

CKPT = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
SNAP_DIR = os.path.join(_ROOT, "_scratch")
EMPTY_WHITELIST = os.path.join(_ROOT, "config", "index_weight_empty_codes.txt")


def load_empty_whitelist():
    """源端真空白名单：直查确认过的真空指数，判据③据此排除。

    ⛔ 登记纪律：逐个直查 ≥3 个不同年份交易日全 0 行、且同期对照指数有数据。
    未登记的新整段空仍触发硬失败——这是抓配额耗尽的关键防线。
    """
    if not os.path.exists(EMPTY_WHITELIST):
        return set()
    out = set()
    with open(EMPTY_WHITELIST, encoding="utf-8") as f:
        for line in f:
            code = line.split("#", 1)[0].strip()
            if code:
                out.add(code)
    return out


def load_task_keys(table):
    """权威任务键集：与 backfill 实际生成的 tasks 完全同源。"""
    conf = dict(BACKFILL_TARGETS)[table]
    tasks, kind = build_tasks(table, conf, dry_run=True)
    if not tasks:
        print(f"[FATAL] {table} 任务集为空（kind={kind}），无法自检", file=sys.stderr)
        raise SystemExit(2)
    return {f"{t[0]}|{t[1]}" for t in tasks}, kind


def load_completed(table):
    if not os.path.exists(CKPT):
        print(f"[FATAL] 断点文件不存在: {CKPT}", file=sys.stderr)
        raise SystemExit(2)
    with open(CKPT, encoding="utf-8") as f:
        return set(json.load(f).get(table, {}).get("completed", []))


def data_stats(table, code_col, date_col):
    p = os.path.join(MARKET_DATA_DIR, table, f"{table}.parquet")
    if not os.path.exists(p):
        return 0, {}
    df = pd.read_parquet(p, columns=[code_col, date_col])
    return len(df), df.groupby(code_col)[date_col].nunique().to_dict()


def main():
    ap = argparse.ArgumentParser(description="backfill 覆盖自检")
    ap.add_argument("--table", default="index_weight")
    ap.add_argument("--code-col", default="index_code")
    ap.add_argument("--date-col", default="trade_date")
    ap.add_argument("--snapshot", action="store_true", help="记录基线")
    ap.add_argument("--verify", action="store_true", help="比对基线")
    ap.add_argument("--expect", type=int, default=None,
                    help="本批应新增的有效断点键数（取自 backfill 的「完成=N」）")
    a = ap.parse_args()

    if not (a.snapshot or a.verify):
        ap.error("必须指定 --snapshot 或 --verify")

    snap_path = os.path.join(SNAP_DIR, f".coverage_snapshot_{a.table}.json")
    valid, _kind = load_task_keys(a.table)
    completed = load_completed(a.table)
    eff = completed & valid
    rows, per_code = data_stats(a.table, a.code_col, a.date_col)
    codes = set(per_code)

    if a.snapshot:
        os.makedirs(SNAP_DIR, exist_ok=True)
        snap = {
            "table": a.table,
            "valid_keys": len(eff),
            "rows": rows,
            "codes": sorted(codes),
            # 断点 code 集合：verify 时据此算「本批新增 code」，避免用累计量误判
            "ck_codes": sorted({k.split("|")[0] for k in eff}),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        print(f"[SNAP] {a.table}  有效断点={len(eff)}  数据行={rows:,}  指数={len(codes)}")
        print(f"       任务集={len(valid)}  断点总键={len(completed)}  "
              f"非任务集键={len(completed - valid)}")
        print(f"       -> {snap_path}")
        return 0

    if not os.path.exists(snap_path):
        print(f"[FATAL] 无基线快照，请先跑 --snapshot: {snap_path}", file=sys.stderr)
        return 2
    with open(snap_path, encoding="utf-8") as f:
        snap = json.load(f)

    fails, warns = [], []
    d_key = len(eff) - snap["valid_keys"]
    if a.expect is not None:
        # ⛔ 判据方向（2026-11-16 P0 实证）：P0 的特征是「多记」——一次 520 任务
        #    记了 1,040 个键（并发写导致键数翻倍），必须硬失败停止。
        #    「少记」多为偶发网络失败（ok=False 不记断点），重跑即可补齐，只警告。
        if d_key > a.expect:
            fails.append(f"① 断点增量 {d_key} > 期望 {a.expect}（多记＝并发/假完成特征）")
        elif d_key < a.expect:
            warns.append(f"① 断点增量 {d_key} < 期望 {a.expect}（疑似部分失败，重跑可补）")
    if rows < snap["rows"]:
        fails.append(f"② 数据行数回退 {snap['rows']:,} -> {rows:,}（疑似被覆盖丢失）")

    # ⭐ 判据③（2026-11-17 P0 #2b 修正版）：只看**本批新增断点 code** 中
    #    「数据 0 行」的整段真空。旧版遍历「数据里新增的 code」，而那些 code
    #    必然有数据 ⇒ per_code==0 恒假＝死判据，永不触发。
    #    区分：直查实证 000124.CSI 五期全 0 行＝真源端真空（单个，放过）；
    #    配额耗尽则是**一批** code 同时整段真空（≥2，硬停）。
    ck_cnt = collections.Counter(k.split("|")[0] for k in eff)
    new_ck_codes = set(ck_cnt) - set(snap.get("ck_codes", []))
    wl = load_empty_whitelist()
    empty_new = sorted(c for c in new_ck_codes
                       if per_code.get(c, 0) == 0 and c not in wl)
    skipped_wl = sorted(c for c in new_ck_codes
                        if per_code.get(c, 0) == 0 and c in wl)
    if skipped_wl:
        print(f"       [白名单] 已确认源端真空 {len(skipped_wl)} 个: {skipped_wl[:8]}")
    if len(empty_new) >= 2:
        fails.append(f"③ 本批新增 {len(new_ck_codes)} 个断点 code 中 "
                     f"{len(empty_new)} 个数据 0 行（整段真空批量＝配额耗尽特征）: "
                     f"{empty_new[:8]}")
    elif empty_new:
        warns.append(f"③ 本批新增 code 中数据 0 行: {empty_new[0]}"
                     f"（单个，疑似源端真空，直查确认后可忽略）")

    # 判据④：全量「断点期数 ≠ 数据期数」清单——源端发布时间边界会集中在此，
    #    只作信息性警告，不作为硬判据（否则 82 个部分真空指数会误停）。
    mism = [(c, per_code.get(c, 0), ck_cnt[c]) for c in sorted(set(ck_cnt) | codes)
            if per_code.get(c, 0) != ck_cnt[c]]
    if mism:
        warns.append(f"④ 断点期数 ≠ 数据期数: {len(mism)} 个指数（源端发布时间边界）")
        for c, d, k in mism[:8]:
            flag = "  ⚠数据0行" if d == 0 and k > 0 else ""
            print(f"       {c:<12} 数据={d:<4} 断点={k:<4}{flag}")

    print(f"[VERIFY] {a.table}  有效断点 {snap['valid_keys']} -> {len(eff)} (+{d_key})"
          f"  数据行 {snap['rows']:,} -> {rows:,}  指数 {len(codes)}")
    for w in warns:
        print(f"  [WARN] {w}")
    if fails:
        for f_ in fails:
            print(f"  [FAIL] {f_}")
        print("\n  ⛔ 自检未通过 —— 停止补数，回滚本批断点后重跑")
        return 1
    print("  ✅ 硬判据通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
