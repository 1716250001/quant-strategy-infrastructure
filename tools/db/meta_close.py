# -*- coding: utf-8 -*-
"""tools/db/meta_close.py — 元数据刷新后的 R12 收尾流水线（CC-FIX-7，§362）

九步（顺序固定，默认干跑；``--apply`` 才写盘）：

  1. 读 fund_basic → market=E 集合 N，对照 known_gaps.market_e_count
  2. 四表覆盖检查：E 码在 fund_daily/nav/adj/share 中是否有数据
  3. 未覆盖码逐只探针一次（fund_daily，limit=1）：0 行 ⇒ 四表 source_empty 登记；
     有行 ⇒ **只报告「应补数」**，不补（补数走 gap-update）
  4. 调 tools.research.fund_stale.apply_fix(detect()) 做注册表对账
  5. market_e_count = N
  6. fund_basic_status_nan = 当前 E 码中 status 为空的码（集合相等）
  7. 重建 derived/fund_basic_patch.parquet（口径见 rebuild_patch）
  8. 自检：整跑 regress（apply 后）
  9. 打印变更摘要

禁令：不改四表数据、不自动补数、不改任何判据。
"""
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

import pandas as pd

from config import MARKET_DATA_DIR, META_DIR, PROJECT_DIR

TABLES = ("fund_daily", "fund_nav", "fund_adj", "fund_share")
_HERE = os.path.dirname(os.path.abspath(__file__))          # tools/db
KG_PATH = os.path.join(os.path.dirname(_HERE), "known_gaps.json")
CODE_ROOT = os.path.dirname(os.path.dirname(_HERE))         # 代码/
PATCH_PATH = os.path.join(MARKET_DATA_DIR, "derived", "fund_basic_patch.parquet")
PATCH_COLS = ["ts_code", "name", "fund_type", "list_date", "first_trade_date",
              "early_days", "category", "status"]
_NAN_TOKENS = ("", "nan", "<NA>", "None")


# ============================================================
# 纯函数（供单测）
# ============================================================
def e_codes(basic):
    """fund_basic 中 market=E 的码（排序后）。"""
    return sorted(str(c) for c in basic.loc[basic["market"] == "E", "ts_code"])


def status_nan_set(basic):
    """当前 market=E 中 status 为空的码集合（与 R20/R22 同口径）。"""
    e = basic[basic["market"] == "E"]
    mask = (e["status"].isna()
            | e["status"].astype(str).isin(_NAN_TOKENS))
    return sorted(str(c) for c in e.loc[mask, "ts_code"])


def coverage_missing(codes, stores, registered=None):
    """{table: 未覆盖码列表}。

    stores     = {table: 库内实际有数据的码集合}
    registered = {table: 已登记 source_empty 的码集合}
    ⇒ 只报「未覆盖且未登记」的可执行缺口（已登记源端空不算新缺口）。
    """
    es = set(codes)
    registered = registered or {}
    return {t: sorted(es - set(stores.get(t, ())) - set(registered.get(t, ())))
            for t in TABLES}


def rebuild_patch(basic, first_map):
    """按现有口径重建补表（必须与旧表逐行可复现）。

    口径（2026-11-03 由旧表反推、§292 已用独立重算验证）：
      first_trade_date = fund_daily 该码 min(trade_date)
      early_days       = list_date − first_trade_date（天，float64）
      category:
        no_daily = first_trade_date 空
        list_na  = list_date 空（且 first 非空）
        early    = early_days > 0
        late     = early_days < −30（首笔行情晚于上市日 >30 天）
        ok       = 其余（含 −30…0 的小幅「上市后未立即成交」）
    """
    base = basic.loc[basic["market"] == "E",
                     ["ts_code", "name", "fund_type", "list_date", "status"]].copy()
    base["first_trade_date"] = base["ts_code"].map(first_map).astype("str")
    ld = pd.to_datetime(base["list_date"], format="%Y%m%d", errors="coerce")
    ft = pd.to_datetime(base["first_trade_date"], format="%Y%m%d", errors="coerce")
    base["early_days"] = (ld - ft).dt.days.astype("float64")
    cat = pd.Series("ok", index=base.index, dtype="str")
    cat[base["early_days"] < -30] = "late"
    cat[base["early_days"] > 0] = "early"
    cat[base["list_date"].isna()] = "list_na"
    cat[base["first_trade_date"].isna()] = "no_daily"
    base["category"] = cat
    return base[PATCH_COLS].reset_index(drop=True)


def first_trade_dates(table="fund_daily", root=MARKET_DATA_DIR):
    """全表 first_trade_date：{ts_code: 'YYYYMMDD'}（只读 ts_code/trade_date 两列）。"""
    frames = [pd.read_parquet(f, columns=["ts_code", "trade_date"])
              for f in sorted(glob.glob(os.path.join(root, table, "*.parquet")))]
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    return df.groupby("ts_code")["trade_date"].min().to_dict()


# ============================================================
# known_gaps 读写
# ============================================================
def _load_kg():
    with open(KG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_kg(kg):
    tmp = KG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kg, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, KG_PATH)


def _norm_status(v):
    """status 归一：NaN/空/`<NA>`/None → 'nan'（与 R20/R22 同口径）。"""
    if v is None:
        return "nan"
    try:
        if pd.isna(v):
            return "nan"
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return "nan" if s in _NAN_TOKENS else s


def sync_evidence_status(kg, status_map):
    """把 evidence 里的 status 快照同步为当前 fund_basic（R20 要求逐码相等）。

    只更新**已有 status 字段**的条目；缺字段的不补（保持原证据形态）。
    返回 (变更数, {table: [(code, old, new)]})。
    """
    n, changed = 0, {}
    for t in TABLES:
        ev = kg.get(t, {}).get("source_empty_evidence")
        items = (ev.items() if isinstance(ev, dict)
                 else [(x.get("ts_code"), x) for x in (ev or [])])
        for c, v in items:
            if not isinstance(v, dict) or "status" not in v:
                continue
            exp = status_map.get(str(c), (None, None))[1]
            if exp is None:
                continue
            cur = _norm_status(v.get("status"))
            if cur != exp:
                v["status"] = exp
                n += 1
                changed.setdefault(t, []).append((str(c), cur, exp))
    return n, changed


def register_source_empty(kg, code, name, status, today):
    """单码在四表登记「源端空」并写 evidence（幂等）。"""
    st = _norm_status(status)
    for t in TABLES:
        entry = kg.setdefault(t, {})
        se = entry.setdefault("source_empty", [])
        if code not in se:
            se.append(code)
        entry["source_empty"] = sorted(se)
        if "source_empty_count" in entry:
            entry["source_empty_count"] = len(entry["source_empty"])
        ev = entry.setdefault("source_empty_evidence", {})
        rec = {"probe_rows": 0, "status": st, "probed_at": today}
        if isinstance(ev, list):
            ev = [x for x in ev if str(x.get("ts_code")) != code]
            ev.append({"ts_code": code, "name": name, "status": st,
                       "probe_rows": 0, "probed_at": today})
            ev.sort(key=lambda x: str(x.get("ts_code")))
            entry["source_empty_evidence"] = ev
        else:
            ev[code] = rec
            entry["source_empty_evidence"] = {k: ev[k] for k in sorted(ev)}


def _check_invariants(kg):
    """注册表 ↔ evidence ↔ count 三者一致（写盘前 fail-loud）。"""
    for t in TABLES:
        entry = kg.get(t, {})
        reg = {str(c) for c in entry.get("source_empty", [])}
        ev = entry.get("source_empty_evidence", {})
        if isinstance(ev, dict):
            ev_codes = {str(k) for k in ev}
        else:
            ev_codes = {str(x.get("ts_code")) for x in ev}
        assert reg == ev_codes, f"{t} 注册表/证据错位: {sorted(reg ^ ev_codes)[:5]}"
        if "source_empty_count" in entry:
            assert entry["source_empty_count"] == len(reg), \
                f"{t} source_empty_count={entry['source_empty_count']} ≠ {len(reg)}"


# ============================================================
# 主流程
# ============================================================
def _diff_patch(old, new):
    """按 ts_code 对齐比较旧/新补表，返回 {列: 差异行数} 与码集差。"""
    common = sorted(set(old["ts_code"]) & set(new["ts_code"]))
    a = old[old["ts_code"].isin(common)].set_index("ts_code").sort_index()
    b = new[new["ts_code"].isin(common)].set_index("ts_code").sort_index()
    out = {}
    for c in PATCH_COLS:
        if c == "ts_code":
            continue
        eq = (a[c] == b[c]) | (a[c].isna() & b[c].isna())
        out[c] = int((~eq).sum())
    return out, sorted(set(new["ts_code"]) - set(old["ts_code"])), \
        sorted(set(old["ts_code"]) - set(new["ts_code"]))


def _regress_tail(n=40):
    """整跑 regress，返回 (rc, 末尾若干行)。"""
    try:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", "main.py", "regress"],
            cwd=CODE_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600)
        lines = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return r.returncode, lines[-n:]
    except Exception as e:  # noqa: BLE001
        return 1, [f"[ERROR] regress 无法执行: {type(e).__name__}: {e}"]


def run(apply=False, verbose=True):
    """执行九步；返回退出码（0=成功/干跑一致，1=自检失败或前置不符）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    basic = pd.read_parquet(os.path.join(META_DIR, "fund_basic.parquet"))
    kg = _load_kg()
    codes = e_codes(basic)
    n_e = len(codes)
    old_count = kg.get("market_e_count")

    print("=" * 70)
    print(f"  db meta-close  {'APPLY' if apply else 'DRY-RUN'}  ({today})")
    print("=" * 70)
    print(f"\n[1] fund_basic market=E: {n_e}（known_gaps.market_e_count={old_count}）")

    # ---- 2. 四表覆盖 ----
    from common import reader
    stores = {t: set(reader.codes_from_store(t)) for t in TABLES}
    registered = {t: set(map(str, kg.get(t, {}).get("source_empty", [])))
                  for t in TABLES}
    missing = coverage_missing(codes, stores, registered)
    print("\n[2] 四表覆盖（未覆盖且未登记）：")
    for t in TABLES:
        print(f"    {t:12s} 库内 {len(stores[t]):5d} 码 | 已登记源端空 "
              f"{len(registered[t]):3d} | 可执行缺口 {len(missing[t])} 只"
              + (f" → {missing[t]}" if missing[t] else ""))

    # ---- 3. 探针（只探未覆盖码；fund_daily limit=1）----
    probe_codes = sorted({c for t in TABLES for c in missing[t]})
    status_map = {}
    for _, r in basic.loc[basic["market"] == "E", ["ts_code", "name", "status"]].iterrows():
        status_map[str(r["ts_code"])] = (r["name"], _norm_status(r["status"]))
    to_register, should_fill, probe_fail = [], [], []
    if probe_codes:
        from fetch.base import RateLimiter, get_pro, ts_call_with_retry
        pro, limiter = get_pro(), RateLimiter()
        print(f"\n[3] 探针 {len(probe_codes)} 只（fund_daily，limit=1）：")
        for code in probe_codes:
            df = ts_call_with_retry(pro, "fund_daily", limiter,
                                    {"ts_code": code, "limit": 1}, verbose=verbose)
            if df is None:
                probe_fail.append(code)
                print(f"    {code}  探针失败（不登记，留待重跑）")
            elif len(df) == 0:
                to_register.append(code)
                print(f"    {code}  源端 0 行 ⇒ 四表登记 source_empty")
            else:
                should_fill.append((code, len(df)))
                print(f"    {code}  源端有 {len(df)} 行 ⇒ 应补数（走 gap-update，不自动补）")
    else:
        print("\n[3] 无未覆盖码，跳过探针")

    # ---- 4. 注册表对账 ----
    from tools.research.fund_stale import apply_fix, detect
    det = detect()
    n_stale = sum(len(x["stale"]) for x in det)
    print(f"\n[4] 注册表对账：stale 合计 {n_stale}")
    for x in det:
        print(f"    {x['table']:12s} stale={len(x['stale']):3d} stale_late={len(x['stale_late']):3d}"
              + (f" → {x['stale'][:5]}" if x["stale"] else ""))
    n_status, status_changes = sync_evidence_status(kg, status_map)
    print(f"    evidence.status 同步: {n_status} 处"
          + (f" → {status_changes}" if status_changes else ""))

    # ---- 5/6. 声明重算 ----
    new_nan = status_nan_set(basic)
    old_nan = sorted(str(c) for c in kg.get("fund_basic_status_nan", []))
    print(f"\n[5] market_e_count: {old_count} → {n_e}")
    print(f"[6] fund_basic_status_nan: {len(old_nan)} → {len(new_nan)}"
          f"（多={sorted(set(new_nan) - set(old_nan))[:5]} "
          f"少={sorted(set(old_nan) - set(new_nan))[:5]}）")

    # ---- 7. 重建补表 ----
    new_patch = rebuild_patch(basic, first_trade_dates())
    old_patch = pd.read_parquet(PATCH_PATH) if os.path.exists(PATCH_PATH) else None
    patch_same = old_patch is not None and old_patch.equals(new_patch)
    print(f"\n[7] 补表：旧 {0 if old_patch is None else len(old_patch)} 行 → "
          f"新 {len(new_patch)} 行 | early_days>0="
          f"{int((new_patch['early_days'] > 0).sum())} | "
          f"category={new_patch['category'].value_counts().to_dict()}")
    if old_patch is not None:
        diffs, added, dropped = _diff_patch(old_patch, new_patch)
        print(f"    与旧表按码对齐：新增码={added} 丢失码={dropped} 差异列={diffs}")
    else:
        print("    旧补表不存在（将全量新建）")

    # ---- 判定是否需要变更 ----
    kg_changed = (bool(n_stale) or bool(to_register) or bool(n_status)
                  or old_count != n_e or old_nan != new_nan)
    needs = kg_changed or not patch_same
    print(f"\n[变更判定] known_gaps={kg_changed} patch={'有变化' if not patch_same else '无变化'}")

    if not apply:
        print("\n[DRY-RUN] 未写盘；加 --apply 执行")
        return 0 if not probe_fail else 1

    bak_dir = None
    if not needs:
        print("\n[幂等] 零变更，未写盘")
    else:
        # ---- 备份 → 写盘 ----
        bak_dir = os.path.join(PROJECT_DIR, "备份", "归档文件",
                               f"bak-{datetime.now():%Y%m%d}-pre-meta-close")
        os.makedirs(bak_dir, exist_ok=True)
        # 备份只写一次：同日重跑不得用「已被改过的现状」覆盖最早的原始备份
        _bk_kg = os.path.join(bak_dir, "known_gaps.json")
        if not os.path.exists(_bk_kg):
            shutil.copy2(KG_PATH, _bk_kg)
        _bk_patch = os.path.join(bak_dir, "fund_basic_patch.parquet")
        if os.path.exists(PATCH_PATH) and not os.path.exists(_bk_patch):
            shutil.copy2(PATCH_PATH, _bk_patch)
        print(f"\n[备份] {bak_dir}")

        if n_stale:
            apply_fix(det, backup_dir=bak_dir, verbose=verbose)
            kg = _load_kg()
        sync_evidence_status(kg, status_map)
        for code in to_register:
            nm, st = status_map.get(code, ("", None))
            register_source_empty(kg, code, nm, st, today)
        kg["market_e_count"] = n_e
        kg["fund_basic_status_nan"] = new_nan
        _check_invariants(kg)
        _save_kg(kg)
        print(f"[写入] known_gaps.json：market_e_count={n_e}、status_nan={len(new_nan)}、"
              f"新登记={to_register}")

        if not patch_same:
            os.makedirs(os.path.dirname(PATCH_PATH), exist_ok=True)
            new_patch.to_parquet(PATCH_PATH, index=False)
            print(f"[写入] {PATCH_PATH}（{len(new_patch)} 行）")

    # ---- 8. 自检 ----
    print("\n[8] 自检：main.py regress")
    rc, tail = _regress_tail()
    print("\n".join(tail))
    print(f"[8] regress rc={rc}")

    # ---- 9. 摘要 ----
    print("\n[9] 变更摘要：")
    print(f"    E 码: {old_count} → {n_e}")
    print(f"    status_nan: {len(old_nan)} → {len(new_nan)}")
    print(f"    新登记源端空: {to_register or '无'}")
    print(f"    应补数（未自动补）: {should_fill or '无'}")
    print(f"    探针失败（未登记）: {probe_fail or '无'}")
    print(f"    补表: {0 if old_patch is None else len(old_patch)} → {len(new_patch)} 行")
    print(f"    备份: {bak_dir or '零变更未写盘'}")
    return 0 if rc == 0 and not probe_fail else 1
