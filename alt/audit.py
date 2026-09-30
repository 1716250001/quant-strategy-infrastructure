# -*- coding: utf-8 -*-
"""
alt/audit.py — alt 库数据体检
==============================
对应主库 tools/db_audit.py 的能力，但适配 alt 库的形态（63 张表、两种布局）。

五项检查（全部**只读**，零网络请求）：

  1. **断点 × 落盘交叉校验**
       断点说完成但目录无文件 → 异常（写失败被吞的痕迹）
       有文件但断点无记录      → 异常（断点丢失，会重复拉取）
     ⚠ 例外：快照表在"无数据日"不产生文件（实测跌停股池弱市日为空），
        这类表按"断点有记录"即视为正常，但会单独列出提示。

  2. **重复检测（二分）**
       - 复合主键重复：按 spec.subset 分组后同键多行
       - 真重复：整行完全相同
     ⚠ 必须二分：单日多行是业务事实（多标的/多类别/多池），
        只按单列主键判重会产生大量假阳性。

  3. **覆盖缺口**
       逐张 by_year 表：库内最新日期 → 目标日期之间缺哪些交易日。
     ⚠ 必须排除交易日历里的**未来交易日**，否则缺口会报到未来某年。
     ⚠ 窗口限制内的缺口才算"可补"，超出窗口的属"永久缺口"。

  4. **规模统计**
       表数 / 文件数 / 行数 / 体积 / 日期跨度，按 tier 汇总。

  5. **主库隔离校验**
       确认 alt 代码未触碰主库：比对主库文件数与最新修改时间
       （基线存 _meta/main_db_snapshot.json）。

用法：
  python -m alt.audit                    # 全部检查
  python -m alt.audit --checkpoint       # 只查断点×落盘
  python -m alt.audit --dupes            # 只查重复
  python -m alt.audit --gaps             # 只查覆盖缺口
  python -m alt.audit --scale            # 只做规模统计
  python -m alt.audit --isolation        # 只做主库隔离校验
  python -m alt.audit --snapshot         # 记录主库基线（供隔离校验用）
  python -m alt.audit --json             # 输出 JSON 摘要
"""
import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime

import pandas as pd

from config_alt import (ALT_DATA_DIR, ALT_META_DIR, TS_DATA_DIR,
                        assert_writable, ensure_alt_dirs)
from common import reader as _reader
from common.calendar import open_dates, recent_trade_dates, trade_dates_between
from alt import spec as sp_mod
from alt.backfill import AltCheckpoint, latest_trade_date
from alt.reader import read_alt

SNAPSHOT_FILE = os.path.join(ALT_META_DIR, "main_db_snapshot.json")
ISO_LOG_FILE = os.path.join(ALT_META_DIR, "isolation_log.json")
ISO_LOG_KEEP = 30          # 夹逼校验日志保留条数
KNOWN_GAPS_FILE = os.path.join(ALT_META_DIR, "known_gaps.json")

# alt 代码**只读**主库的这个顶层目录（交易日历等）。
# 据此可把主库变更区分为"alt 可能引起的"与"主库自身盘后流水线的"。
_ALT_READ_SCOPE_TOP = {"metadata"}


# ============================================================
# 工具
# ============================================================
def _tables_named():
    """{表名: spec}（含 DROPPED，便于核对弃用表是否误建）"""
    out = {s.name: s for s in sp_mod.ALL_SPECS}
    for d in sp_mod.DROPPED:
        out.setdefault(d["name"], None)
    return out


def _dir_files(name):
    d = os.path.join(ALT_DATA_DIR, name)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".parquet"))


def _load(name, sp=None):
    """读某表全部数据（清缓存后读，避免落盘后读到旧布局缓存）"""
    _reader.clear_cache(name, root=ALT_DATA_DIR)
    return read_alt(name)


def _count_rows(name):
    d = os.path.join(ALT_DATA_DIR, name)
    n = 0
    for f in _dir_files(name):
        try:
            n += len(pd.read_parquet(os.path.join(d, f)))
        except Exception:
            pass
    return n


# ============================================================
# 1. 断点 × 落盘交叉校验
# ============================================================
def check_checkpoint(verbose=True):
    ck = AltCheckpoint()
    ck_data = ck.data
    rows = []

    for name, sp in _tables_named().items():
        files = _dir_files(name)
        has_ck = name in ck_data
        done = ck_data.get(name, {}).get("done", [])
        state, note = "OK", ""

        # ⚠ DROPPED（刻意弃用）表单独归类，**不计入「未建」**（2026-09-21 修复）:
        #   本函数经 _tables_named() 把 DROPPED 表也纳入了遍历（原本是为了核对
        #   "弃用表是否被误建"）。但它无文件、无断点 → 命中下面的"未建"分支 →
        #   汇总打印「未建 1 张: macro_ch_cpi」，**读起来像"还差一张没建"**，
        #   实际那是刻意不建的表（原准备用瑞士 CPI，因数据不完整而弃用）。
        #   属统计口径误导。现改为独立 state="已弃用"：
        #     · DROPPED 且无文件 → "已弃用"（正常，不报警）
        #     · DROPPED 却有文件 → "异常"（才是真问题：弃用表被建了）
        if sp is None:
            state = "异常" if files else "已弃用"
            note = ("弃用表却被建库（应确认是否误建）" if files
                    else "刻意不建（DROPPED 登记）")
            rows.append({"table": name, "state": state, "files": len(files),
                         "ckpt_done": len(done), "note": note})
            continue

        if has_ck and not files:
            # 快照表在无数据日不产文件属正常，需按"是否有日期型断点"区分
            if sp is not None and sp.layout == "by_year" and done:
                state, note = "提示", f"断点有 {len(done)} 条日期记录但无文件（快照表无数据日属正常）"
            else:
                state, note = "异常", "断点显示已完成但目录无任何 parquet（疑似写失败被吞）"
        elif files and not has_ck:
            state, note = "异常", f"有 {len(files)} 个文件但断点无记录（断点丢失，会重复拉取）"
        elif not files and not has_ck:
            state, note = "未建", "尚未建库"

        rows.append({"table": name, "state": state, "files": len(files),
                     "ckpt_done": len(done), "note": note})

    if verbose:
        print("=" * 96)
        print("【1】断点 × 落盘交叉校验")
        print("=" * 96)
        bad = [r for r in rows if r["state"] == "异常"]
        tip = [r for r in rows if r["state"] == "提示"]
        built = [r for r in rows if r["state"] == "OK"]
        notyet = [r for r in rows if r["state"] == "未建"]
        dropped = [r for r in rows if r["state"] == "已弃用"]
        print(f"  正常 {len(built)} | 提示 {len(tip)} | 异常 {len(bad)} | 未建 {len(notyet)}")
        if dropped:
            print(f"  ⓘ 另有 {len(dropped)} 张**刻意弃用**表（DROPPED 登记，不算缺口）: "
                  f"{', '.join(r['table'] for r in dropped)}")
        for r in bad:
            print(f"    [异常] {r['table']:<26} 文件{r['files']:>3} 断点{r['ckpt_done']:>3}  {r['note']}")
        for r in tip:
            print(f"    [提示] {r['table']:<26} {r['note']}")
        if notyet:
            names = [r["table"] for r in notyet]
            print(f"    未建 {len(names)} 张: {', '.join(names[:10])}"
                  + ("..." if len(names) > 10 else ""))
    return rows


# ============================================================
# 1.5 断点日期 × 落盘日期的**集合级**交叉校验（N1，2026-09-19 二次审查）
# ============================================================
def _load_known_gaps():
    """已确认的『断点有、磁盘无』日期登记（表 → 日期列表）。

    ⚠ 为什么需要这份登记（N6 衍生的设计决定）：
      "快照表存在独有日期"这一事实**本身是稳定的**（已删的 0916/0917
      永远不会回来）。若每次审计都把它计入"待处理"，就会形成
      **长期红灯** —— 我们自己一直在批评的"狼来了"效应。

      正确做法是区分「**新出现的**独有日期」（需排查）与
      「**已确认过的**」（不再打扰）。故引入本登记：
        · 首次发现 → 判【需排查】，计入待处理
        · 人工确认后写入本文件 → 此后判【已确认】，不计入
        · 将来若又出现新删除 → 仍会判【需排查】

      维护：确认某批独有日期确实不可回补后，运行
            `python -m alt.audit --confirm-gaps`
            自动把当前所有快照表独有日期登记为已确认。
    """
    if not os.path.exists(KNOWN_GAPS_FILE):
        return {}
    try:
        v = json.load(open(KNOWN_GAPS_FILE, encoding="utf-8"))
        return v.get("confirmed", {}) if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_known_gaps(mapping, verbose=True):
    """写入已确认缺口登记（原子写）"""
    ensure_alt_dirs()
    payload = {
        "note": "已确认的『断点有、磁盘无』日期（不可回补）；审计时不再报『需排查』",
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "confirmed": {k: sorted(v) for k, v in sorted(mapping.items()) if v},
    }
    # M1（2026-09-19 四轮审查）：落盘前过写入守卫。
    # 目标虽是模块常量（必在授权区内），但 config_alt 明示"任何落盘函数
    # 都应先调用它"——纪律一致，且防止将来有人把路径改成可配置的。
    assert_writable(KNOWN_GAPS_FILE)
    tmp = KNOWN_GAPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, KNOWN_GAPS_FILE)
    if verbose:
        n = sum(len(v) for v in payload["confirmed"].values())
        print(f"已登记 {len(payload['confirmed'])} 张表 / {n} 个已确认缺口 → "
              f"{KNOWN_GAPS_FILE}")
    return payload


def confirm_current_gaps(verbose=True):
    """把当前**快照表**的所有独有日期登记为「已确认」。

    只登记快照表：传参型的独有日期是"源端返空"的业务事实，
    会随窗口滚动不断变化，登记没有意义（且会掩盖真正的异常）。
    """
    rows = check_date_sets(verbose=False)
    mapping = _load_known_gaps()
    for r in rows:
        if not r.get("is_snapshot") or not r.get("ckpt_only"):
            continue
        prev = set(mapping.get(r["table"], []))
        mapping[r["table"]] = sorted(prev | set(r["ckpt_only"]))
    return save_known_gaps(mapping, verbose=verbose)


def check_date_sets(only=None, verbose=True):
    """比对每张 by_year 表"断点记过的日期"与"磁盘真有的日期"。

    ⚠ 为什么 check_checkpoint 不够（N1）：
      `check_checkpoint` 只看"有无 parquet 文件"，不看"日期是否都在"。
      于是有两种情况会被掩盖：

        · 数据被删（如人为清理污染数据）→ 断点仍说完成 → 看起来一切正常
        · 断点虚记（记了但从未真正落盘）

      而 `check_gaps` 又会把"已 done 的日期"排除，于是这类缺失
      **在缺口报告里也看不见** —— 即"断点说完成 ≠ 有数据"换个形式复现。

    ⚠ 但不能把"断点有、磁盘无"一律当异常（否则又是"狼来了"）：
      按三态纪律，"成功但返回空数据"的日期**本来就会记断点**。
      实测 zt_pool 族 20260824~0828 五天源端返空、跌停股池弱市日无跌停股，
      这些都是**业务事实**，不是故障。

    ⚠ **分层呈现**（N6，2026-09-19 三轮审查）：
      原实现把 16 张表全部平铺列出，而 16/16 全部命中该类别 ——
      等于没有筛选，用户看不出重点（信噪比为零）。

      三轮审查建议按"① 快照表 → 需排查；② 传参型窗口内 → 可疑、
      窗口外 → 正常"分层。**规则① 采纳，规则② 经实测证伪**：

        · 规则① 成立：快照表只采当日（且已禁用复核窗口），
          其断点里的历史日期**不可能来自"业务性空数据"**——
          只能来自"数据被删"或"修复前的污染遗留"。故可高亮为需排查。
          实测：10 张快照表的独有日期完全一致（0916/0917），
          可聚合成一条呈现。

        · 规则② **不成立**：断点里的日期 ⊆ 曾被尝试的日期 ⊆ 展开窗口内。
          也就是说"窗口外"的日期**根本不会进断点**（从未被尝试），
          故按窗口内外分层必然把 100% 的传参型表判为可疑。
          实测：6 张 zt_pool 族独有日期 5~18 天，窗口外均为 **0 天**。
          照该规则做会制造**新的狼来了**。

      故本实现：快照表 → 单列高亮；传参型 → 降级为聚合摘要（源端返空属业务事实）。
    """
    rows = []
    specs = [sp_mod.get(only)] if only else list(sp_mod.ALL_SPECS)
    ck_all = AltCheckpoint().data          # ⚠ N9：循环外读一次，循环内复用
    known = _load_known_gaps()             # ⚠ N6：已确认缺口（不再报"需排查"）
    skipped_list = []                      # mode="list" 的表（断点按标的，见下）
    for sp in specs:
        if sp.layout != "by_year":
            continue
        # ⚠ mode="list"（按标的逐只拉）**不适用日期级交叉校验**（2026-09-21 新增）:
        #   这类表的断点键是**标的**（如 'AAPL'），而本节比对的是**日期**
        #   （且只取 'date:' 前缀的键）→ 断点集合恒为空 → 全部磁盘日期落入
        #   disk_only → 误报「磁盘有数据但断点无记录（会重复拉取）」。
        #   实证: us_daily_qfq 被误报 55 个年度文件对应的全部日期。
        #   ⚠ 必须**显式跳过并计数**（而非静默 continue）——否则用户会误以为
        #     "没报=没问题"（ckpt 工具曾踩过同类坑: 标注写在 `if disk is not None`
        #     分支内 → 未覆盖模式永不执行）。
        if sp.mode == "list":
            skipped_list.append(sp.name)
            continue
        files = _dir_files(sp.name)
        if not files:
            continue
        try:
            df = _load(sp.name, sp)
        except Exception as e:  # noqa: BLE001
            rows.append({"table": sp.name, "state": f"读取失败:{type(e).__name__}",
                         "disk": 0, "ckpt": 0, "ckpt_only": [], "disk_only": []})
            continue
        have = set()
        if sp.date_col in df.columns:
            have = set(df[sp.date_col].astype(str).unique())
        done = {str(k)[5:] for k in ck_all.get(sp.name, {}).get("done", [])
                if str(k).startswith("date:")}
        ckpt_only = sorted(done - have)
        disk_only = sorted(have - done)
        # 已确认的独有日期与"新出现的"分开
        known_set = set(known.get(sp.name, []))
        fresh_only = [d for d in ckpt_only if d not in known_set]
        confirmed = [d for d in ckpt_only if d in known_set]

        if disk_only:
            state = "异常"                      # 磁盘有、断点无 → 会重复拉取
        elif fresh_only and sp.is_snapshot:
            state = "需排查"                    # 快照表新出现 → 只能是被删/污染
        elif fresh_only:
            state = "空数据日"                  # 传参型 → 多为源端返空（业务事实）
        elif confirmed:
            state = "已确认"                    # 登记过的历史缺口
        else:
            state = "OK"
        rows.append({"table": sp.name, "state": state, "disk": len(have),
                     "ckpt": len(done), "ckpt_only": ckpt_only,
                     "fresh_only": fresh_only, "confirmed": confirmed,
                     "disk_only": disk_only, "is_snapshot": sp.is_snapshot})

    if verbose:
        print()
        print("=" * 96)
        print("【1.5】断点日期 × 落盘日期（集合级交叉校验）")
        print("=" * 96)
        ok = [r for r in rows if r["state"] == "OK"]
        suspect = [r for r in rows if r["state"] == "需排查"]
        empty = [r for r in rows if r["state"] == "空数据日"]
        conf = [r for r in rows if r["state"] == "已确认"]
        bad = [r for r in rows if r["state"] == "异常"]
        print(f"  日期完全吻合 {len(ok)} | 需排查 {len(suspect)} | 空数据日 {len(empty)}"
              f" | 已确认 {len(conf)} | 磁盘有断点无 {len(bad)}")
        if skipped_list:
            print(f"  ⓘ 另有 {len(skipped_list)} 张 list 模式表**未纳入本校验**"
                  f"（其断点按标的记、无日期维度）: {', '.join(skipped_list)}")
            print(f"     这些表的覆盖情况请用各自专用入口查看"
                  f"（如 `python -m alt.us_backfill --status`）")

        # ① 需排查：快照表**新出现**的独有日期（只能是数据被删/污染）
        if suspect:
            print("\n  [需排查] 快照表出现**新的**断点独有日期")
            print("           快照表只采当日、已禁用复核窗口，其断点里的历史日期")
            print("           不可能来自业务性空数据 → 只能是「数据被删」或「污染遗留」。")
            print("           确认不可回补后，可运行 `python -m alt.audit --confirm-gaps` 登记。")
            groups = {}
            for r in suspect:
                groups.setdefault(tuple(r["fresh_only"]), []).append(r["table"])
            for dates, tables in sorted(groups.items(), key=lambda x: -len(x[1])):
                print(f"    {len(tables)} 张表，独有 {len(dates)} 天: {list(dates)}")
                names = ", ".join(tables)
                print(f"      涉及: {names[:96]}{'...' if len(names) > 96 else ''}")

        # ② 已确认：登记过的历史缺口（不再打扰）
        if conf:
            n_days = sum(len(r["confirmed"]) for r in conf)
            print(f"\n  [已确认] {len(conf)} 张表 / {n_days} 天已登记为不可回补"
                  f"（快照表历史删除，见 {os.path.basename(KNOWN_GAPS_FILE)}）：")
            groups = {}
            for r in conf:
                groups.setdefault(tuple(r["confirmed"]), []).append(r["table"])
            for dates, tables in sorted(groups.items(), key=lambda x: -len(x[1])):
                print(f"    {len(tables)} 张表: {list(dates)}")

        # ③ 传参型：聚合摘要（源端返空属业务事实，不逐张铺开）
        if empty:
            n_days = sum(len(r["fresh_only"]) for r in empty)
            print(f"\n  [正常] 传参型表 {len(empty)} 张 / 共 {n_days} 天无落盘 —— "
                  f"源端返空（业务事实），非故障")
            print(f"         注：'窗口内/外'无法进一步分层——能进断点的日期必然在窗口内，")
            print(f"             窗口外日期从未被尝试、不会进断点（实测窗口外均为 0 天）")
            for r in sorted(empty, key=lambda x: -len(x["fresh_only"]))[:6]:
                n = len(r["fresh_only"])
                # M3（2026-09-19 四轮审查）：原按升序取前 6 天 → 展示的是**最旧**的，
                # 最近的反而看不到（实测 zt_pool_dtgc 有 18 天，只显示 0810~0817）。
                # 改为倒序：最近的在最前，符合"先看有没有新问题"的阅读意图。
                # ⚠ 仅改善可见性，不是检测手段——传参型的内部空数据日无法自动
                #   鉴别（实测 dtgc 的 0901/0908 夹在已落盘日期中间，却是真实
                #   业务空数据），故不加任何"按位置判断"的规则以免误报。
                recent = sorted(r["fresh_only"], reverse=True)
                print(f"    {r['table']:<24}断点{r['ckpt']:>3}天/磁盘{r['disk']:>3}天"
                      f"  独有 {n} 天（近→远）  {recent[:6]}"
                      f"{'...' if n > 6 else ''}")
            if len(empty) > 6:
                print(f"    ...（其余 {len(empty) - 6} 张同理）")

        # ④ 真异常：磁盘有数据但断点无记录
        if bad:
            print("\n  [异常] 磁盘有数据但断点无记录（会重复拉取）：")
            for r in bad:
                print(f"    {r['table']:<24} {r['disk_only'][:10]}")
    return rows


# ============================================================
# 2. 重复检测（二分）
# ============================================================
def check_dupes(only=None, verbose=True):
    rows = []
    specs = [sp_mod.get(only)] if only else list(sp_mod.ALL_SPECS)
    for sp in specs:
        if not _dir_files(sp.name):
            continue
        try:
            df = _load(sp.name, sp)
        except Exception as e:  # noqa: BLE001
            rows.append({"table": sp.name, "state": f"读取失败:{type(e).__name__}",
                         "n": 0, "pk_dup": 0, "exact_dup": 0, "note": ""})
            continue
        n = len(df)
        # 复合主键重复
        pk_dup = 0
        if isinstance(sp.subset, list):
            cols = [c for c in sp.subset if c in df.columns]
            if len(cols) == len(sp.subset):
                pk_dup = int(df.duplicated(subset=sp.subset, keep=False).sum())
        # 真重复（整行相同）
        exact_dup = int(df.duplicated(keep=False).sum())

        if exact_dup:
            state, note = "异常", f"整行重复 {exact_dup} 行 → 真重复，需清理"
        elif pk_dup and isinstance(sp.subset, list):
            state, note = "提示", f"主键重复 {pk_dup} 行（确认是否单日多行的业务事实）"
        else:
            state, note = "OK", ""
        rows.append({"table": sp.name, "state": state, "n": n,
                     "pk_dup": pk_dup, "exact_dup": exact_dup, "note": note})

    if verbose:
        print()
        print("=" * 96)
        print("【2】重复检测（二分：真重复 vs 业务多行）")
        print("=" * 96)
        ok = [r for r in rows if r["state"] == "OK"]
        tip = [r for r in rows if r["state"] == "提示"]
        bad = [r for r in rows if r["state"] == "异常"]
        print(f"  干净 {len(ok)} | 提示 {len(tip)} | 异常 {len(bad)} | 共查 {len(rows)} 张")
        for r in bad:
            print(f"    [异常] {r['table']:<26} {r['n']:>7,} 行  真重复 {r['exact_dup']:>5}  {r['note']}")
        for r in tip:
            print(f"    [提示] {r['table']:<26} {r['n']:>7,} 行  主键重复 {r['pk_dup']:>5}  {r['note']}")
    return rows


# ============================================================
# 3. 覆盖缺口
# ============================================================
def check_gaps(target_date=None, verbose=True):
    tdate = target_date or latest_trade_date()
    ck_data = AltCheckpoint().data
    rows = []
    for sp in sp_mod.ALL_SPECS:
        if sp.layout != "by_year":
            continue
        if not _dir_files(sp.name):
            rows.append({"table": sp.name, "state": "未建", "latest": "",
                         "gap": 0, "permanent": 0, "note": ""})
            continue
        _reader.clear_cache(sp.name, root=ALT_DATA_DIR)
        from common.reader import latest_date
        latest = latest_date(sp.name, date_col=sp.date_col, root=ALT_DATA_DIR)
        if not latest:
            rows.append({"table": sp.name, "state": "读不到日期", "latest": "",
                         "gap": 0, "permanent": 0, "note": "仅有无日期数据"})
            continue

        # ⚠ 排除未来交易日：open_dates 含未来日期，不截断会把缺口报到未来
        gaps = trade_dates_between(latest, tdate)
        gaps = [d for d in gaps if d <= tdate]

        # ⚠ 排除"已采集但当日无数据"的日期（否则会造成假警报）
        #   快照表在特定行情下本就可能为空（实测跌停股池 20260918 = 0 行），
        #   此时空数据也记了断点 → 该日期并非"未采集"，不应算缺口。
        done_dates = set()
        for k in ck_data.get(sp.name, {}).get("done", []):
            k = str(k)
            if k.startswith("date:"):
                done_dates.add(k[5:])
        if done_dates:
            gaps = [d for d in gaps if d not in done_dates]

        # 窗口限制：超出窗口的属永久缺口（源端已不提供）
        # ⚠ 口径统一（2026-09-19 三轮后）：max_backfill_days 的单位是
        #   **交易日**，须用 recent_trade_dates() 换算，不得用 Timedelta。
        #   本处原实现用自然日，与 backfill 的交易日口径不一致。
        permanent = 0
        if sp.max_backfill_days:
            _win = recent_trade_dates(sp.max_backfill_days, end_date=tdate)
            floor = _win[0] if _win else None
            if floor:
                permanent = sum(1 for d in gaps if d < floor)
                rest = [d for d in gaps if d >= floor]
            else:
                rest = gaps
        else:
            rest = gaps
        n_rest = len(rest)
        if n_rest:
            state, note = "缺口", f"可补 {n_rest} 天（未采集）"
        elif permanent:
            state, note = "永久缺口", f"{permanent} 天超出源端保留窗口，无法回补"
        else:
            state, note = "OK", ""
        rows.append({"table": sp.name, "state": state, "latest": latest,
                     "gap": n_rest, "permanent": permanent, "note": note})

    if verbose:
        print()
        print("=" * 96)
        print("【3】覆盖缺口（by_year 表）")
        print("=" * 96)
        print(f"  目标交易日: {tdate}    （已排除交易日历中的未来日期）")
        ok = [r for r in rows if r["state"] == "OK"]
        g = [r for r in rows if r["state"] == "缺口"]
        pg = [r for r in rows if r["state"] == "永久缺口"]
        other = [r for r in rows if r["state"] not in ("OK", "缺口", "永久缺口")]
        print(f"  无缺口 {len(ok)} | 可补缺口 {len(g)} | 永久缺口 {len(pg)} | 其他 {len(other)}")
        for r in sorted(g, key=lambda x: -x["gap"]):
            print(f"    [缺口] {r['table']:<26} 库内 {r['latest']}  可补 {r['gap']:>3} 天  {r['note']}")
        for r in pg:
            print(f"    [永久] {r['table']:<26} 库内 {r['latest']}  {r['note']}")
        for r in other:
            print(f"    [{r['state']}] {r['table']:<26} {r['note']}")
    return rows


# ============================================================
# 3.5 清理历史重复（可选动作，带备份）
# ============================================================
def fix_dupes(only=None, dry_run=True, verbose=True):
    """清理 alt 库中已固化的"整行重复"。

    ⚠ 为什么需要独立清理动作（不能靠日常更新自愈）：
       复用的 common.parquet_store.merge_append 在"已存在"路径下会先算
       added = 去重后行数 − 现有行数，**added<=0 就直接 return 不写盘**。
       于是文件里一旦有重复，后续更新算出的 added 恒为负值 →
       该表**永远不再写盘、静默冻结**（不报错、日志还显示 ok）。
       故必须用"直接读→去重→写回"的方式一次性清掉。

    ⚠ 为什么按 spec.subset 而不是全列判重：
       merge_append 的默认去重键就是 spec.subset，清理必须与之一致，
       否则会出现"清理后下次更新又判为重复"的抖动。
       对 subset="*" 的表则用全列判重。

    安全：仅改 alt 库；写入前经 assert_writable；原文件先备份到
          _meta/dupes_backup/{时间戳}/。
    """
    import shutil
    from config_alt import assert_writable

    specs = [sp_mod.get(only)] if only else list(sp_mod.ALL_SPECS)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk_root = os.path.join(ALT_META_DIR, "dupes_backup", ts)
    results = []

    print("=" * 96)
    print(f"清理历史重复{'【DRY-RUN】' if dry_run else '【实跑】'}")
    print("=" * 96)

    for sp in specs:
        files = _dir_files(sp.name)
        if not files:
            continue
        total_before = total_after = 0
        touched = []
        for f in files:
            p = os.path.join(ALT_DATA_DIR, sp.name, f)
            try:
                df = pd.read_parquet(p)
            except Exception as e:  # noqa: BLE001
                print(f"  [跳过] {sp.name}/{f}: 读取失败 {type(e).__name__}")
                continue
            before = len(df)
            if sp.subset == "*" or not isinstance(sp.subset, list):
                dedup = df.drop_duplicates(keep="last")
            else:
                cols = [c for c in sp.subset if c in df.columns]
                dedup = (df.drop_duplicates(subset=cols, keep="last")
                         if len(cols) == len(sp.subset)
                         else df.drop_duplicates(keep="last"))
            after = len(dedup)
            total_before += before
            total_after += after
            if after < before:
                touched.append((f, before, after))
                if not dry_run:
                    assert_writable(p)
                    os.makedirs(os.path.join(bk_root, sp.name), exist_ok=True)
                    shutil.copy2(p, os.path.join(bk_root, sp.name, f))
                    dedup.reset_index(drop=True).to_parquet(p, index=False)

        if touched:
            for f, b, a in touched:
                print(f"  {'[将清理]' if dry_run else '[已清理]'} "
                      f"{sp.name}/{f}: {b} → {a} 行（去 {b-a}）")
            results.append({"table": sp.name, "before": total_before,
                            "after": total_after, "removed": total_before - total_after,
                            "files": len(touched)})

    print()
    if results:
        tr = sum(r["removed"] for r in results)
        print(f"  涉及 {len(results)} 张表，共去重 {tr} 行")
        if dry_run:
            print("  ※ DRY-RUN：未改动任何文件。确认后加 --apply 执行。")
        else:
            print(f"  原文件备份于: {bk_root}")
    else:
        print("  [OK] 无整行重复需要清理")
    return results


# ============================================================
# 4. 规模统计
# ============================================================
def check_scale(verbose=True):
    by_tier = {}
    total_rows = total_files = total_bytes = 0
    detail = []
    for sp in sp_mod.ALL_SPECS:
        d = os.path.join(ALT_DATA_DIR, sp.name)
        files = _dir_files(sp.name)
        if not files:
            continue
        rows = 0
        size = 0
        for f in files:
            p = os.path.join(d, f)
            size += os.path.getsize(p)
            try:
                rows += len(pd.read_parquet(p))
            except Exception:
                pass
        total_rows += rows
        total_files += len(files)
        total_bytes += size
        t = by_tier.setdefault(sp.tier, {"tables": 0, "files": 0, "rows": 0, "bytes": 0})
        t["tables"] += 1
        t["files"] += len(files)
        t["rows"] += rows
        t["bytes"] += size
        detail.append({"table": sp.name, "tier": sp.tier, "layout": sp.layout,
                       "files": len(files), "rows": rows, "bytes": size})

    # 弃用表是否被误建
    misbuilt = [d["name"] for d in sp_mod.DROPPED
                if _dir_files(d["name"])]

    if verbose:
        print()
        print("=" * 96)
        print("【4】规模统计")
        print("=" * 96)
        print(f"{'tier':<8}{'表数':>6}{'文件':>7}{'行数':>12}{'体积':>12}")
        # ⚠ 2026-09-22 修复（P1-4）: 改用 alt.spec.TIERS 动态派生。
        #   原硬编码 ("P0","P1","P3") 漏了 P4（us_universe / us_daily_qfq），
        #   而合计行用 sum(by_tier.values()) → 出现"分级之和 ≠ 合计"的误导。
        for t in sp_mod.TIERS:
            v = by_tier.get(t)
            if not v:
                continue
            print(f"{t:<8}{v['tables']:>6}{v['files']:>7}{v['rows']:>12,}"
                  f"{v['bytes']/1048576:>10.2f} MB")
        print("-" * 96)
        print(f"{'合计':<8}{sum(v['tables'] for v in by_tier.values()):>6}"
              f"{total_files:>7}{total_rows:>12,}{total_bytes/1048576:>10.2f} MB")
        if misbuilt:
            print(f"\n  [异常] 已弃用的表被误建: {misbuilt}")
        else:
            print(f"\n  [OK] 无弃用表被误建（DROPPED 共 {len(sp_mod.DROPPED)} 张）")
    return {"detail": detail, "by_tier": by_tier, "total_rows": total_rows,
            "total_files": total_files, "total_bytes": total_bytes,
            "misbuilt": misbuilt}


# ============================================================
# 5. 主库隔离校验
# ============================================================
def _scan_full_fingerprint():
    """主库全量指纹 {相对路径: mtime}（只读，零请求）。

    ⚠ 为什么要全量指纹而非只数文件个数（R2，2026-09-19）：
      只比"文件数 + 最新 mtime"无法区分"新增了文件"与"既有文件被改写"。
      后者才是越界的铁证（alt 若误写主库，最可能是改写当天分区），
      前者多为主库自身的盘后流水线。故夹逼校验需要逐文件指纹。
    """
    fp = {}
    for dp, dn, fns in os.walk(TS_DATA_DIR):
        for f in fns:
            if not f.endswith(".parquet"):
                continue
            p = os.path.join(dp, f)
            try:
                fp[os.path.relpath(p, TS_DATA_DIR)] = os.path.getmtime(p)
            except OSError:
                continue
    return fp


def _fingerprint_summary(fp):
    """指纹 → 摘要（{count, newest_mtime, newest_file}）"""
    newest, newest_f = 0.0, ""
    for k, m in fp.items():
        if m > newest:
            newest, newest_f = m, k
    return {"count": len(fp), "newest_mtime": newest, "newest_file": newest_f}


def _scan_main_db():
    """主库摘要（兼容旧调用方；内部已改用全量指纹）"""
    return _fingerprint_summary(_scan_full_fingerprint())


def _diff_fingerprint(before, after):
    """比对两份指纹 → (新增, 删除, 既有被改写)"""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    touched = sorted(k for k in (set(before) & set(after))
                     if after[k] > before[k] + 1.0)
    return added, removed, touched


def _classify_changes(added, removed, touched):
    """把主库变更分为 (suspicious, external)。

    suspicious：可能由 alt 引起，或属破坏性变更（文件被删除）
    external  ：落在 alt **只读范围之外**，可归因为主库自身盘后流水线

    ⚠ 为什么要分类（2026-09-19 实测，避免制造新的"狼来了"）：
      主库盘后流水线与 alt 可能**并发运行**。实测一次仅 1.3 秒的 alt 单表
      更新期间，主库就有 10 个文件被改写（express / pledge_detail /
      stk_managers 等）—— 这些表 alt 代码从不触碰（alt 只读 metadata/）。
      若一律打印"需立即排查"，用户很快又会对告警脱敏。
    """
    suspicious, external = [], []
    suspicious.extend(removed)          # 删除文件属破坏性，一律警惕
    for k in list(touched) + list(added):
        top = str(k).replace("/", os.sep).split(os.sep)[0].lower()
        (suspicious if top in _ALT_READ_SCOPE_TOP else external).append(k)
    return sorted(set(suspicious)), sorted(set(external))


def _write_snapshot(summary):
    """写入静态基线（原子写）"""
    ensure_alt_dirs()
    s = dict(summary)
    s["snapshot_at"] = datetime.now().isoformat(timespec="seconds")
    assert_writable(SNAPSHOT_FILE)          # M1：落盘前过写入守卫
    tmp = SNAPSHOT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SNAPSHOT_FILE)
    return s


def save_snapshot(verbose=True):
    ensure_alt_dirs()
    s = _write_snapshot(_scan_main_db())
    if verbose:
        print(f"主库基线已记录 → {SNAPSHOT_FILE}")
        print(f"  parquet {s['count']:,} 个，最新修改 "
              f"{datetime.fromtimestamp(s['newest_mtime']):%Y-%m-%d %H:%M:%S}")
        print("  ⚠ 静态基线仅供信息展示；越界判定以 isolation_guard 的前后夹逼为准")
    return s


def _load_iso_log():
    if not os.path.exists(ISO_LOG_FILE):
        return []
    try:
        v = json.load(open(ISO_LOG_FILE, encoding="utf-8"))
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _append_iso_log(rec):
    try:
        logs = _load_iso_log()
        logs.append(rec)
        ensure_alt_dirs()
        assert_writable(ISO_LOG_FILE)       # M1：落盘前过写入守卫
        tmp = ISO_LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(logs[-ISO_LOG_KEEP:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, ISO_LOG_FILE)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def isolation_guard(verbose=True, record=True, label="alt 写操作"):
    """alt 写操作期间的**主库隔离前后夹逼**校验（R2 的主判据）。

    ⚠ 为什么必须换成夹逼（R2，2026-09-19 实证）：
      主库**每个交易日都在被盘后流水线合法写入**（gap-update 等）。
      原实现拿"数天前的静态基线"与实际比对，只要 alt 与主库流水线存在
      时间差，文件数/mtime 就必然不等 → 校验**长期误报** →
      产生"狼来了"效应，真正越界时反而被忽视。
      （实测：基线 6879 vs 实际 7003，差 +124，全部来自主库自身
        0919 凌晨的合法补数，与 alt 无关。）

      改为在 alt 操作**开始前**记指纹、**结束后**立即比对：
      时间窗压缩到本次操作内，任何差异都可直接归因。

    用法：
        with isolation_guard() as before:
            ... alt 写操作 ...

    夹逼通过时自动刷新静态基线（承认主库的合法演进），
    这样静态基线不会永远红灯。
    """
    before = _scan_full_fingerprint()
    from config_alt import violation_count as _vc
    v_before = _vc()
    if verbose:
        s = _fingerprint_summary(before)
        print(f"  [隔离] 操作前主库指纹：{s['count']:,} 个 parquet")
    outcome = {"label": label, "t0": datetime.now().isoformat(timespec="seconds")}
    try:
        yield before
    finally:
        after = _scan_full_fingerprint()
        added, removed, touched = _diff_fingerprint(before, after)
        suspicious, external = _classify_changes(added, removed, touched)
        # ⚠ N2（2026-09-19 二次审查）：归因是**推断**（"变更不在 metadata/ 下
        #   → 必然是主库自己写的"），不是证据。补一个**可直接观测**的证据：
        #   alt 所有写路径都过 assert_writable()，若本次操作期间发生过越界尝试，
        #   计数器会 >0 —— 那是"试图越界"的铁证，即使被拦下也必须报警。
        v_after = _vc()
        attempted = v_after - v_before
        from config_alt import violation_records as _vr
        attempted_detail = _vr()[v_before:] if attempted else []
        verdict = ("clean" if not (added or removed or touched)
                   else ("suspicious" if (suspicious or attempted) else "external"))
        outcome.update({
            "t1": datetime.now().isoformat(timespec="seconds"),
            "before_count": len(before), "after_count": len(after),
            "added": len(added), "removed": len(removed), "touched": len(touched),
            "suspicious": len(suspicious), "external": len(external),
            "suspicious_sample": suspicious[:10], "external_sample": external[:10],
            "write_violations": attempted,
            "write_violation_sample": attempted_detail[:5],
            "verdict": verdict,
            "clean": verdict == "clean",
            "attribution_note": ("external 归因为推断：变更发生在 alt 只读范围"
                                 "（metadata/）之外，故推定为并行主库流水线；"
                                 "本次 write_violations=0 表明 alt 未尝试越界"),
        })
        if verbose:
            vtxt = (f"本次操作期间 alt 发生 {attempted} 次越界写入尝试"
                    if attempted else "本次操作 alt 未发生越界写入尝试（计数器 0）")
            if verdict == "clean":
                print("  [隔离] [OK] 操作后主库指纹零变更 —— alt 未触碰主库")
                print(f"         {vtxt}")
            elif verdict == "external":
                print(f"  [隔离] [提示] 操作期间主库有 {len(external)} 个文件变动，"
                      f"**全部落在 alt 只读范围之外**（alt 只读 metadata/）")
                print(f"         → 推定（**推断，非证明**）为并行的主库盘后流水线")
                print(f"         {vtxt} —— 未见越界尝试，故倾向支持该推定。样例：")
                for k in external[:5]:
                    print(f"           {k}")
            else:
                print(f"  [隔离] [!!] 操作期间主库发生**可疑变更**："
                      f"新增 {len(added)} | 删除 {len(removed)} | 改写 {len(touched)}")
                if attempted:
                    print(f"         ⚠⚠ alt 越界写入尝试 {attempted} 次（被拦截）：")
                    for r in attempted_detail[:5]:
                        print(f"           {r['kind']}: {r['path']}")
                for k in suspicious[:5]:
                    print(f"           {k}")
                print("         ⚠ 需立即排查是否越界写入")
        if record:
            _append_iso_log(outcome)
        # 零变更、或已归因（主库自身流程）时刷新静态基线；
        # **可疑变更不刷新** —— 把差异留在基线里等排查
        if verdict in ("clean", "external"):
            try:
                _write_snapshot(_fingerprint_summary(after))
            except Exception:  # noqa: BLE001
                pass


def check_isolation(verbose=True):
    """主库隔离校验。

    ⚠ R2（2026-09-19 重设计）：本函数**不再是越界判据**。
      原因：主库每个交易日都被盘后流水线合法写入，与静态基线比对必然
      长期不等（实测差 +124），造成"狼来了"效应——用户无法区分
      "alt 越界"与"主库正常更新"，真的越界时反而被忽视。

      真正的判据是 isolation_guard() 的**前后夹逼**：它把时间窗压缩到
      单次 alt 操作内，差异可直接归因。本函数现在只做两件事：
        1. 展示静态基线的变化（**信息用，不判罪**）
        2. 展示最近一次夹逼校验的结论（证据链）
    """
    now = _scan_main_db()
    base = None
    if os.path.exists(SNAPSHOT_FILE):
        try:
            base = json.load(open(SNAPSHOT_FILE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            base = None
    res = {"now": now, "base": base}
    if verbose:
        print()
        print("=" * 96)
        print("【5】主库隔离校验")
        print("=" * 96)
        print("  判据说明：本节①为信息展示，②的前后夹逼才是越界判据")
        if not base:
            print("  ① 静态基线：尚未记录（可运行 python -m alt.audit --snapshot）")
        else:
            d = now["count"] - base["count"]
            newer = now["newest_mtime"] > base["newest_mtime"] + 1
            print(f"  ① 静态基线: {base['count']:,} 个 @ "
                  f"{datetime.fromtimestamp(base['newest_mtime']):%Y-%m-%d %H:%M:%S}")
            print(f"     主库现状: {now['count']:,} 个 @ "
                  f"{datetime.fromtimestamp(now['newest_mtime']):%Y-%m-%d %H:%M:%S}")
            if d == 0 and not newer:
                print("     [OK] 与基线一致")
            else:
                print(f"     [信息] 相对基线变化：文件数 {d:+d}，最新时间推进={newer}")
                print("     ⚠ 这**不能**判定为 alt 越界 —— 主库盘后流水线本身每日写库")
                print("       最新的文件：" + str(now["newest_file"]))
        logs = _load_iso_log()
        if not logs:
            print("  ② 夹逼校验：尚无记录（下次跑 alt.update / alt.backfill 会自动记录）")
        else:
            last = logs[-1]
            v = last.get("verdict") or ("clean" if last.get("clean") else "unknown")
            vtxt = {"clean": "零变更 ✓",
                    "external": "已归因主库自身流程 ○（推断）",
                    "suspicious": "**可疑变更** ✗"}.get(v, str(v))
            print(f"  ② 夹逼校验（最近一次）: {last.get('t0','?')} → {last.get('t1','?')}")
            print(f"     操作: {last.get('label','?')}   结论: {vtxt}")
            print(f"     变更: 新增 {last.get('added',0)} | 删除 {last.get('removed',0)}"
                  f" | 改写 {last.get('touched',0)}"
                  f"（外部归因 {last.get('external',0)}"
                  f" | 可疑 {last.get('suspicious',0)}）")
            wv = last.get("write_violations", 0)
            print(f"     越界写入尝试: {wv} 次"
                  + ("（计数器 0 = 本次操作 alt 未尝试越界，为该推定的支持证据）"
                     if not wv else " ⚠⚠ 需立即排查"))
            print("     注：'外部归因'是**推断**（变更在 alt 只读范围外 → 推定为主库"
                  "流水线），非证明；越界计数器提供直接证据。")
            n_susp = sum(1 for x in logs if x.get("verdict") == "suspicious")
            n_ext = sum(1 for x in logs if x.get("verdict") == "external")
            n_wv = sum(x.get("write_violations", 0) for x in logs)
            print(f"     历史 {len(logs)} 次夹逼：零变更 {len(logs)-n_susp-n_ext}"
                  f" | 推定归因 {n_ext} | 可疑 {n_susp} | 累计越界尝试 {n_wv} 次")
        res["clean_recent"] = (logs[-1].get("verdict") if logs else None)
        res["changed"] = None      # 静态比对不再作为判定结果
    return res


# ============================================================
# 6. 结构报告（Markdown，对应主库 tools/db_schema + gen_db_report）
# ============================================================
def gen_report(path=None, verbose=True):
    """生成 alt 库结构报告（Markdown）。

    内容：库根/规模概览、按 tier 分组逐表清单（接口/布局/分区列/去重键/
    日期范围/行数/新鲜度/已知问题），供人工查阅与留档。
    """
    ensure_alt_dirs()
    today = pd.Timestamp.now().normalize()
    lines = []
    A = lines.append

    A("# alt 库结构报告（akshare 备用数据源）")
    A("")
    A(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}  ")
    A(f"> 库根：`{ALT_DATA_DIR}`  ")
    A("> 说明：本库与 tushare 主库物理分离，只读主库、绝不写入。")
    A("")
    A("## 一、概览")
    A("")
    scale = check_scale(verbose=False)
    A("| 层级 | 表数 | 文件 | 行数 | 体积 |")
    A("|---|---:|---:|---:|---:|")
    for t in sp_mod.TIERS:      # P1-4: 动态派生（原硬编码漏 P4）
        v = scale["by_tier"].get(t)
        if not v:
            continue
        A(f"| {t} | {v['tables']} | {v['files']} | {v['rows']:,} | "
          f"{v['bytes']/1048576:.2f} MB |")
    A(f"| **合计** | **{sum(v['tables'] for v in scale['by_tier'].values())}** | "
      f"**{scale['total_files']}** | **{scale['total_rows']:,}** | "
      f"**{scale['total_bytes']/1048576:.2f} MB** |")
    A("")

    if sp_mod.DROPPED:
        A("### 已弃用表（登记留痕）")
        A("")
        A("| 表 | 接口 | 原因 |")
        A("|---|---|---|")
        for d in sp_mod.DROPPED:
            A(f"| `{d['name']}` | `{d['func']}` | {d['reason'][:80]} |")
        A("")

    if sp_mod.KNOWN_ISSUES:
        A("### 已知数据质量问题")
        A("")
        for k in sp_mod.KNOWN_ISSUES:
            A(f"- **[{k['kind']}] {k['name']}**  ")
            A(f"  现象：{k['detail'][:100]}  ")
            A(f"  影响：{k['impact'][:100]}  ")
            A(f"  检测：{k['detect'][:100]}")
        A("")

    A("## 二、逐表明细")
    A("")
    for tier in sp_mod.TIERS:   # P1-4: 动态派生（原硬编码漏 P4）
        specs = sp_mod.BY_TIER.get(tier, [])
        if not specs:
            continue
        A(f"### {tier}（{len(specs)} 张）")
        A("")
        A("| 表名 | 接口 | 布局 | 分区列 | 去重键 | 行数 | 日期范围 | 新鲜度 | 说明 |")
        A("|---|---|---|---|---|---:|---|---|---|")
        for sp in specs:
            n = _count_rows(sp.name)
            rng, fresh = "—", "—"
            if n:
                try:
                    df = _load(sp.name, sp)
                    if sp.date_col in df.columns and len(df):
                        d = pd.to_datetime(df[sp.date_col].astype(str),
                                           format="%Y%m%d", errors="coerce").dropna()
                        if len(d):
                            past = d[d <= today]
                            latest = past.max() if len(past) else d.max()
                            age = int((today - latest).days)
                            rng = f"{d.min():%Y%m%d} ~ {d.max():%Y%m%d}"
                            fresh = (f"{age}天前" if age <= sp.max_age_days
                                     else f"⚠陈旧{age}天")
                except Exception:
                    fresh = "读取失败"
            sub = sp.subset if isinstance(sp.subset, str) else (
                "+".join(map(str, sp.subset)) if sp.subset else "—")
            note = sp.note[:44]
            A(f"| `{sp.name}` | `{sp.func}` | {sp.layout} | `{sp.date_col}` | "
              f"`{sub}` | {n:,} | {rng} | {fresh} | {note} |")
        A("")

    A("## 三、用法速查")
    A("")
    A("```bash")
    A("python -m alt.backfill --list           # 看表规格")
    A("python -m alt.backfill --dry-run        # 回补预演")
    A("python -m alt.backfill --tier P0        # 回补某层级")
    A("python -m alt.verify_spec --tier P1     # 建库前校验规格（七项）")
    A("python -m alt.update                    # 每日增量")
    A("python -m alt.update --status           # 新鲜度体检")
    A("python -m alt.audit                     # 数据体检（五項）")
    A("python -m alt.audit --fix-dupes         # 清理重复（预演）")
    A("python -m alt.audit --confirm-gaps      # 登记已确认不可回补的缺口")
    A("python -m alt.reader status             # 库现状")
    A("```")
    A("")

    # M2（2026-09-19 四轮审查）：补"断点缺口登记"一节。
    #   原报告不含此信息 → 留档时看不出"哪些日期已确认不可回补"，
    #   事后翻报告无法解释"为什么库内少几天而审计说 0 项待处理"。
    known = _load_known_gaps()
    if known:
        A("## 四、断点缺口登记（已确认不可回补）")
        A("")
        A(f"> 登记文件：`{KNOWN_GAPS_FILE}`  ")
        A("> 含义：这些日期曾成功采集（断点在案），但磁盘无数据，且**源端已不再提供**，")
        A(">       属永久缺口。已在审计中排除出『需排查』，避免长期红灯。")
        A("")
        A("| 表 | 已确认不可回补日期 |")
        A("|---|---|")
        for k in sorted(known):
            ds = known[k]
            A(f"| `{k}` | {', '.join(ds)}（{len(ds)} 天） |")
        A("")
        A(f"合计 **{len(known)} 张表 / "
          f"{sum(len(v) for v in known.values())} 天**。")
        A("")
        A("> 判据：快照表只采当日、已禁用复核窗口，其断点里的历史日期不可能来自"
          "业务性空数据，只能是被删或修复前的污染遗留。")
        A("")

    text = "\n".join(lines)
    if path is None:
        path = os.path.join(ALT_META_DIR, "alt_db_report.md")

    # M1（2026-09-19 四轮审查）——**这是一条真实可达的越界通道，不是纪律问题**：
    #   gen_report 的 path 可由用户指定（`python -m alt.audit --report <任意路径>`）。
    #   若用户把路径指向主库（如 D:\全量数据\market_data\xxx.md），就会写进主库；
    #   更糟的是隔离夹逼会把它判为 **external**（因不在 metadata/ 下）→
    #   归咎于主库流水线，**真凶被掩盖**（即 N2 所述假阴性的可达实例）。
    #   故必须最先把用户路径纳入守卫。
    assert_writable(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if verbose:
        print(f"结构报告已生成: {path}")
        print(f"  {len(text.splitlines())} 行 / {len(text.encode('utf-8'))/1024:.1f} KB")
    return path


# ============================================================
# 汇总入口
# ============================================================
def run(which=None, only=None, as_json=False):
    which = which or ["checkpoint", "date_sets", "dupes", "gaps", "scale", "isolation"]
    out = {"audited_at": datetime.now().isoformat(timespec="seconds")}
    if "checkpoint" in which:
        out["checkpoint"] = check_checkpoint()
    if "date_sets" in which:
        out["date_sets"] = check_date_sets(only=only)
    if "dupes" in which:
        out["dupes"] = check_dupes(only=only)
    if "gaps" in which:
        out["gaps"] = check_gaps()
    if "scale" in which:
        out["scale"] = check_scale()
    if "isolation" in which:
        out["isolation"] = check_isolation()

    # 汇总异常数
    # ⚠ 口径说明（N1/N6，2026-09-19）：
    #   · "需排查"（快照表**新出现**的独有日期）**计入**——只能是被删/污染，需人确认；
    #   · "已确认"（登记过的历史缺口）不计入——避免长期红灯（狼来了）；
    #   · "空数据日"（传参型源端返空）不计入——属业务事实。
    n_bad = 0
    for k in ("checkpoint", "dupes", "gaps", "date_sets"):
        for r in out.get(k, []):
            if r.get("state") in ("异常", "缺口", "需排查"):
                n_bad += 1
    out["issues"] = n_bad
    n_empty = sum(1 for r in out.get("date_sets", []) if r.get("state") == "空数据日")
    n_conf = sum(1 for r in out.get("date_sets", []) if r.get("state") == "已确认")
    out["date_set_hints"] = n_empty
    print()
    print("=" * 96)
    print(f"体检完成：{n_bad} 项待处理")
    if n_conf:
        print(f"  已登记不可回补缺口 {n_conf} 张（不计入待处理，"
              f"见 {os.path.basename(KNOWN_GAPS_FILE)}）")
    if n_empty:
        print(f"  传参型表 {n_empty} 张存在'源端返空'的断点日期（业务事实，不计入）")
    print("=" * 96)

    if as_json:
        ensure_alt_dirs()
        p = os.path.join(ALT_META_DIR, "audit_report.json")
        assert_writable(p)                  # M1：落盘前过写入守卫
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
        print(f"JSON 摘要已存: {p}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="alt 库数据体检（只读，零请求）")
    for flag, key, help_ in (
        ("--checkpoint", "checkpoint", "断点×落盘交叉校验"),
        ("--date-sets", "date_sets", "断点日期×落盘日期（集合级交叉校验）"),
        ("--dupes", "dupes", "重复检测"),
        ("--gaps", "gaps", "覆盖缺口"),
        ("--scale", "scale", "规模统计"),
        ("--isolation", "isolation", "主库隔离校验"),
    ):
        ap.add_argument(flag, action="store_true", help=help_)
    ap.add_argument("--only", help="只查某张表（用于重复检测/清理）")
    ap.add_argument("--snapshot", action="store_true", help="记录主库基线")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--fix-dupes", action="store_true",
                    help="清理历史整行重复（默认只预演，加 --apply 才落盘）")
    ap.add_argument("--confirm-gaps", action="store_true",
                    help="把当前快照表的独有日期登记为「已确认不可回补」（不再报需排查）")
    ap.add_argument("--apply", action="store_true", help="配合 --fix-dupes 实际执行")
    ap.add_argument("--report", nargs="?", const=True, default=False,
                    help="生成结构报告 Markdown（可指定输出路径）")
    a = ap.parse_args(argv)

    if a.snapshot:
        save_snapshot()
        return 0

    if a.fix_dupes:
        fix_dupes()
        return 0
    if a.confirm_gaps:
        confirm_current_gaps()
        return 0
    if a.snapshot:
        save_snapshot()
        return 0


    if a.report:
        gen_report(a.report if isinstance(a.report, str) else None)
        return 0

    which = [k for f, k, _ in (
        (a.checkpoint, "checkpoint", ""), (a.date_sets, "date_sets", ""),
        (a.dupes, "dupes", ""), (a.gaps, "gaps", ""),
        (a.scale, "scale", ""), (a.isolation, "isolation", "")) if f]
    run(which or None, only=a.only, as_json=a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
