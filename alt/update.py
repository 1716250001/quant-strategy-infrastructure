# -*- coding: utf-8 -*-
"""
alt/update.py — alt 库每日增量更新
====================================
按 spec.layout 分两种更新策略，与主库 gap-update 的成熟做法对齐：

  ┌─ single 布局（估值锚 / QVIX日频 / 海外宏观）→ **全量刷新**
  │    数据量小（数百~数千行），且接口本身不支持按日增量参数。
  │    直接重拉全表，靠 subset 去重保证幂等。
  │
  └─ by_year 布局（快照表：分时波指 / 涨停池族 / 赚钱效应）→ **按日增量**
       快照型数据**无法回补历史**，必须逐日采集。
       每张表按目标日期拉当日数据，追加进年度文件。

三条纪律（沿用主库 gap-update 已验证的做法）：

1. **复核窗口（lookback）**：只按"库内最新日期"算缺口，会漏掉
   "断点已记但当日数据残缺"的情况——断点说完成了，数据其实不全。
   故每次强制重拉最近 N 个交易日，靠落盘主键去重保证幂等。

2. **快照日期保护**：按日型表取「最近**已收盘**交易日」。
   盘前/盘中运行若注入"今天"，会把**昨日**数据标成今日
   （数据看着正常、只有日期是错的，属静默错误）。

3. **逐表独立**：每表独立算缺口、独立记断点；某表失败不影响其他表，
   失败**不记断点**留待续跑。

用法：
  python -m alt.update                      # 更新全部已建表
  python -m alt.update --dry-run            # 只输出计划，不发请求
  python -m alt.update --only zt_pool       # 单表
  python -m alt.update --tier P3            # 按优先级
  python -m alt.update --lookback 5         # 扩大复核窗口
  python -m alt.update --force              # 忽略刷新间隔，强制全刷
  python -m alt.update --status             # 只看各表新鲜度（零请求）
  python -m alt.update --list               # 列出可更新表
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd

from config_alt import ALT_DATA_DIR, ALT_META_DIR, assert_writable, ensure_alt_dirs
from common.calendar import recent_trade_dates, trade_dates_between
from common.reader import latest_date
from alt import spec as sp_mod
from alt.io import alt_tables
from alt.rate import AltRateLimiter
from alt.backfill import AltCheckpoint, latest_trade_date, run_spec

STATE_FILE = os.path.join(ALT_META_DIR, "update_state.json")
DEFAULT_LOOKBACK = 3


# ============================================================
# 一、更新状态（记录各表上次刷新时间，用于刷新间隔调度）
# ============================================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    ensure_alt_dirs()
    assert_writable(STATE_FILE)             # M1：落盘前过写入守卫
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def days_since(iso_str):
    if not iso_str:
        return 10 ** 6
    try:
        t = datetime.fromisoformat(iso_str)
        return (datetime.now() - t).total_seconds() / 86400
    except Exception:
        return 10 ** 6


# ============================================================
# 二、计划：算每张表这次要做什么
# ============================================================
def table_dir_exists(name):
    d = os.path.join(ALT_DATA_DIR, name)
    return os.path.isdir(d) and any(f.endswith(".parquet") for f in os.listdir(d))


def is_initialized(sp, ckpt):
    """判断该表是否已"初始化过"（可纳入日更）。

    ⚠ 为什么不能只看文件是否存在：
      快照表在**无数据的日子**不会产生 parquet 文件（实测跌停股池
       20260918 = 0 行，弱市日确无跌停股），若只按文件判断，
       该表会被永远判为"尚未建库"、日更时静默跳过，形成永久盲区。
       故增加"断点里有记录"作为已初始化的凭据。
    """
    if table_dir_exists(sp.name):
        return True
    done = ckpt.data.get(sp.name, {}).get("done", [])
    return bool(done)


def plan_one(sp, target_date, lookback, state, force=False, ckpt=None):
    """为一张表算行动计划。

    返回 dict(action, dates, lookback_dates, reason)
      action = "single"  → 全量刷新
      action = "dates"   → 按日增量（dates 为要跑的日期）
      action = "skip"    → 本次不动
    """
    if ckpt is None or not is_initialized(sp, ckpt):
        return {"action": "skip", "reason": "尚未建库（请先跑 alt.backfill）",
                "dates": [], "lookback_dates": []}

    # ── mode="list"（按标的列表逐只拉）不走日更引擎（2026-09-21）──
    # 原因: ① 本引擎的增量模型建立在"按日期"上，按标的的模式表达不了；
    #       ② 该类表（美股前复权）**前复权基准随新拆股漂移**，本就不允许
    #          增量追加，必须整表重建 ⇒ 不适用日更。
    # 专用入口: python -m alt.us_backfill
    if sp.mode == "list":
        return {"action": "skip", "dates": [], "lookback_dates": [],
                "reason": 'mode="list" 不走日更（前复权须整表重建）；'
                          '请用 python -m alt.us_backfill'}

    # ── single 布局：按刷新间隔决定是否全量刷新 ──
    if sp.layout == "single":
        last = state.get(sp.name, {}).get("last_update")
        age = days_since(last)
        age_s = "首次" if not last else f"距今 {age:.1f} 天"
        if not force and last and age < sp.update_every_days:
            return {"action": "skip", "dates": [], "lookback_dates": [],
                    "reason": f"{age_s} < 刷新间隔 {sp.update_every_days} 天"}
        return {"action": "single", "dates": [], "lookback_dates": [],
                "reason": f"全量刷新（{age_s}）"}

    # ── by_year 布局：算缺口 ∪ 复核窗口 ──
    dc = sp.date_col
    # 清布局缓存后再算缺口，避免用到上次运行遗留的 layout 探测结果
    from common import reader as _reader
    from alt.backfill import is_snapshot_safe_now

    _reader.clear_cache(sp.name, root=ALT_DATA_DIR)
    latest = latest_date(sp.name, date_col=dc, root=ALT_DATA_DIR)

    # ⚠⚠ R1（2026-09-19 修复，属严重静默错误）⚠⚠
    #   纯快照表（分时波指 9 张 + 赚钱效应 1 张）**没有任何日期维度**，
    #   接口无论传什么日期都返回同一份"当前快照"。
    #   而复核窗口会把 [今天-2, 今天-1, 今天] 三个日期都塞进来重拉，
    #   于是每个历史日期都被"今天的数据"逐行覆盖 ——
    #   不报错、行数正常、日期看着对，**只有数值是错的**。
    #   实测：10 张表 0916/0917/0918 三天数据逐行完全相同。
    #   故这类表：只允许采集**当日**，且允许重拉（覆盖）当日以修"当日残缺"。
    if sp.is_snapshot:
        safe, why, warn = is_snapshot_safe_now()
        if not safe:
            return {"action": "skip", "dates": [], "lookback_dates": [],
                    "reason": f"纯快照表暂停：{why}"}
        rsn = (f"纯快照表：仅采集当日 {target_date}"
               f"（无日期维度，禁止复核窗口以免覆盖历史）")
        if warn:
            rsn += f"｜⚠ {warn}"
        return {"action": "dates", "dates": [target_date],
                "lookback_dates": [target_date], "reason": rsn}

    refresh = recent_trade_dates(lookback, end_date=target_date) if lookback else []

    # 窗口裁剪：源端只保留最近 N 天时，超窗口的日期请求会报错或静默返回空，
    # 既浪费请求又会把"窗口限制"误记成"断点已完成"。
    #
    # ⚠ 口径统一（2026-09-19 三轮后）：max_backfill_days 的单位是**交易日**，
    #   必须用 recent_trade_dates() 换算，**不得**用 Timedelta(days=n)——
    #   本处原实现用自然日，与 backfill._backfill_dates 的交易日口径不一致
    #   （实测 30 天档：自然日算 20260819、交易日算 20260810，差 9 天）。
    #   源端语义即"最近 N 个交易日"，故交易日口径才正确。
    floor = None
    if sp.max_backfill_days:
        _win = recent_trade_dates(sp.max_backfill_days, end_date=target_date)
        floor = _win[0] if _win else None

    if not latest:
        # 目录存在但读不到日期 → 保守：只跑复核窗口
        dates = list(refresh) or [target_date]
        if floor:
            dates = [d for d in dates if d >= floor] or [target_date]
        return {"action": "dates", "dates": dates, "lookback_dates": list(refresh),
                "reason": "读不到库内最新日期 → 仅跑复核窗口"}

    gaps = trade_dates_between(latest, target_date)
    if floor:
        clipped = [d for d in gaps if d < floor]
        if clipped:
            gaps = [d for d in gaps if d >= floor]
    dates = sorted(set(gaps) | set(refresh))
    if floor:
        dates = [d for d in dates if d >= floor]
    if not dates:
        return {"action": "skip", "dates": [], "lookback_dates": [],
                "reason": f"已是最新（库内 {latest}）"}
    extra = ""
    if floor:
        extra = f"｜窗口限制 {sp.max_backfill_days} 天（早于 {floor} 的不再尝试）"
    return {"action": "dates", "dates": dates, "lookback_dates": list(refresh),
            "reason": f"缺口 {len(gaps)} 天 + 复核 {len(refresh)} 天"
                      f"（库内最新 {latest}）{extra}"}


# ============================================================
# 三、执行
# ============================================================
def update_single(sp, limiter, ckpt, dry_run=False):
    """single 布局：全量刷新（force=True 绕过断点，靠 subset 去重保幂等）"""
    return run_spec(sp, limiter, ckpt, dry_run=dry_run, force=True, verbose=True)


def update_by_year(sp, limiter, ckpt, dates, lookback_dates, dry_run=False):
    """by_year 布局：逐日期拉取并追加。

    复核窗口内的日期用 force=True 强制重拉（修"断点已记但数据残缺"）。
    """
    agg = {"table": sp.name, "tier": sp.tier, "mode": sp.mode, "requests": 0,
           "rows": 0, "added": 0, "ok": True, "skipped": False,
           "errors": [], "warnings": [],
           "done_dates": [], "refreshed_dates": []}
    lb = set(lookback_dates or [])
    for d in dates:
        force = d in lb
        r = run_spec(sp, limiter, ckpt, dry_run=dry_run, force=force,
                     verbose=False, target_date=d)
        agg["requests"] += r["requests"]
        agg["rows"] += r["rows"]
        agg["added"] += r["added"]
        agg["errors"].extend(r.get("errors", []))
        agg["warnings"].extend(r.get("warnings", []))
        if not r["ok"]:
            agg["ok"] = False
        elif not r.get("skipped"):
            # ⚠ 纯快照表的日期记入 done_dates 而非 refreshed_dates：
            #   它没有"复核窗口"概念，其 force 仅表示"允许覆盖当日"，
            #   若记成"复核刷新"会与实际语义不符（R1 修复的一部分）。
            if force and not sp.is_snapshot:
                agg["refreshed_dates"].append(d)
            else:
                agg["done_dates"].append(d)
        # 单日失败即停该表（避免在风控下持续轰击）
        if r.get("errors"):
            break
    return agg


# ============================================================
# 四、新鲜度自检（更新后）
# ============================================================
def freshness_report(specs, verbose=True):
    """逐表读最新日期，报陈旧/缺失。零请求。

    ⚠ 为什么必须做：接口能调用、行数正常 ≠ 数据是当期的。
    实测踩坑：某系海外宏观全部正常返回但停更 1 年。

    ⚠ 必须先清布局探测缓存：detect_layout 会缓存 (root, table) → 布局。
      本函数常在"落盘之后"调用，而规划阶段可能在文件还不存在时
      已把该表缓存成 layout='none' → 落盘后仍读回空表，
      误报"无数据"（实测踩坑：zt_pool_dtgc 落盘 5 行却报无数据）。
    """
    from common import reader as _reader

    today = pd.Timestamp.now().normalize()
    rows = []
    for sp in specs:
        # 落盘后必须让布局缓存失效，否则刚创建的表读不到
        _reader.clear_cache(sp.name, root=ALT_DATA_DIR)
        if not table_dir_exists(sp.name):
            rows.append({"table": sp.name, "state": "缺失", "latest": "", "age": None})
            continue
        # 快照表（has_date=False，如 us_universe）：**无日期维度，新鲜度判据不适用**
        #   2026-09-21 修复：原实现落入下面 `sp.date_col not in df.columns` → 报「无数据」，
        #   但那是**结构性事实**（该表本就没有日期列），不是数据缺失。
        if not sp.has_date:
            rows.append({"table": sp.name, "state": "无日期维度",
                         "latest": "", "age": None})
            continue
        try:
            df = _read_all(sp)
        except Exception as e:  # noqa: BLE001
            rows.append({"table": sp.name, "state": f"读取失败:{type(e).__name__}",
                         "latest": "", "age": None})
            continue
        if df is None or len(df) == 0 or sp.date_col not in df.columns:
            rows.append({"table": sp.name, "state": "无数据", "latest": "", "age": None})
            continue
        d = pd.to_datetime(df[sp.date_col].astype(str), format="%Y%m%d", errors="coerce").dropna()
        past = d[d <= today]
        latest = past.max() if len(past) else d.max()
        age = int((today - latest).days)
        state = "陈旧" if age > sp.max_age_days else "OK"
        # 已知停更的表降级为「已登记停更」（2026-09-21 新增）:
        #   否则每次自检都刷 15 条已知项（金十系源端停更 1 年）→ 狼来了，
        #   真正的「新出现的断更」反而被淹没。
        if state == "陈旧" and sp.known_stale:
            state = "已登记停更"
        rows.append({"table": sp.name, "state": state,
                     "latest": latest.strftime("%Y%m%d"), "age": age,
                     "threshold": sp.max_age_days})

    if verbose:
        print("=" * 92)
        print("更新后新鲜度自检")
        print("=" * 92)
        # 「不报警」类：结构性事实或已登记项，与「新出现的问题」分开
        INFO_STATES = {"无日期维度", "已登记停更"}
        bad = [r for r in rows if r["state"] != "OK" and r["state"] not in INFO_STATES]
        ok = [r for r in rows if r["state"] == "OK"]
        info = [r for r in rows if r["state"] in INFO_STATES]
        print(f"  正常 {len(ok)} 张 | 异常 {len(bad)} 张")
        if info:
            by_i = {}
            for r in info:
                by_i[r["state"]] = by_i.get(r["state"], 0) + 1
            detail = " | ".join(f"{k} {v}" for k, v in by_i.items())
            print(f"  ⓘ 另有 {len(info)} 张**不报警**（{detail}）:")
            for r in info:
                age = f"{r['age']} 天" if r.get("age") is not None else "—"
                extra = ""
                if r["state"] == "已登记停更":
                    sp = next((s for s in specs if s.name == r["table"]), None)
                    if sp and sp.known_stale:
                        extra = f"  ← {sp.known_stale[:62]}"
                print(f"    {r['table']:<26}{r['state']:<10}最新 {r['latest'] or '—':<10}{age}{extra}")
        if bad:
            print(f"\n  需关注:")
            for r in sorted(bad, key=lambda x: -(x.get("age") or 0)):
                age = f"{r['age']} 天" if r.get("age") is not None else "—"
                print(f"    {r['table']:<26}{r['state']:<10}最新 {r['latest'] or '—':<10}{age}")
        by_state = {}
        for r in bad:
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        if by_state:
            print(f"\n  分类: {by_state}")
    return rows


def _read_all(sp):
    """读某表全部数据（布局无关，只读 alt 库）

    清缓存后再读：避免"落盘后仍读到旧的 layout=none 缓存"。
    """
    from common import reader as _reader
    from alt.reader import read_alt
    _reader.clear_cache(sp.name, root=ALT_DATA_DIR)
    return read_alt(sp.name)


# ============================================================
# 五、主流程
# ============================================================
def run(tier=None, only=None, dry_run=False, lookback=DEFAULT_LOOKBACK,
        force=False, verbose=True):
    ensure_alt_dirs()

    if only:
        specs = [sp_mod.get(only)]
    elif tier:
        specs = list(sp_mod.BY_TIER.get(tier, []))
    else:
        specs = list(sp_mod.ALL_SPECS)

    if not specs:
        print(f"无匹配表（tier={tier}, only={only}）")
        return []

    tdate = latest_trade_date(verbose=verbose)
    state = load_state()
    # 先构造断点对象（plan 需要用它的记录判断"是否已初始化"）
    ckpt = AltCheckpoint(dry_run=dry_run)

    # ── 计划 ──
    plans = []
    for sp in specs:
        p = plan_one(sp, tdate, lookback, state, force=force, ckpt=ckpt)
        plans.append((sp, p))

    print("=" * 92)
    print(f"alt 库每日增量更新{'【DRY-RUN 预演】' if dry_run else '【实跑】'}")
    print(f"  库根     : {ALT_DATA_DIR}")
    print(f"  目标交易日: {tdate}（按日型表使用）")
    print(f"  复核窗口 : 最近 {lookback} 个交易日")
    print(f"  表数     : {len(specs)}（待执行 {sum(1 for _, p in plans if p['action'] != 'skip')}）")
    print(f"  时间     : {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 92)

    limiter = AltRateLimiter(dry_run=dry_run, verbose=verbose)

    results = []
    t_start = time.time()

    # ⚠ R2（2026-09-19）：主库隔离改用**前后夹逼**校验（越界判据）。
    #   静态基线比对会因主库盘后流水线每日合法写库而长期误报
    #   （实测差 +124），造成"狼来了"效应；夹逼把时间窗压缩到本次操作内，
    #   差异可直接归因。dry-run 不发请求不写盘，无需校验。
    from contextlib import nullcontext
    from alt.audit import isolation_guard

    guard = (nullcontext() if dry_run
             else isolation_guard(verbose=verbose, label="alt.update"))

    with guard:
        for i, (sp, p) in enumerate(plans, 1):
            head = f"[{i}/{len(plans)}] {sp.name:<26} {p['action']:<7}"
            if p["action"] == "skip":
                print(f"{head} — {p['reason'][:70]}")
                results.append({"table": sp.name, "action": "skip",
                                "requests": 0, "rows": 0, "added": 0, "ok": True,
                                "errors": [], "warnings": [], "reason": p["reason"]})
                continue

            print(f"{head} — {p['reason'][:70]}")
            try:
                if p["action"] == "single":
                    r = update_single(sp, limiter, ckpt, dry_run=dry_run)
                else:
                    r = update_by_year(sp, limiter, ckpt, p["dates"],
                                       p["lookback_dates"], dry_run=dry_run)
                r["action"] = p["action"]
                r["reason"] = p["reason"]
            except Exception as e:  # noqa: BLE001
                r = {"table": sp.name, "action": p["action"], "requests": 0, "rows": 0,
                     "added": 0, "ok": False, "errors": [f"{type(e).__name__}: {e}"],
                     "warnings": [], "reason": p["reason"]}
            results.append(r)

            stat = "OK" if r["ok"] else "FAIL"
            print(f"      请求 {r['requests']:>3} | 落盘 {r['rows']:>7,} 行"
                  f"（新增 {r['added']:,}） | {stat}")
            if r.get("done_dates"):
                print(f"      新增日期: {r['done_dates'][0]}~{r['done_dates'][-1]}"
                      f"（{len(r['done_dates'])} 天）")
            if r.get("refreshed_dates"):
                print(f"      复核刷新: {r['refreshed_dates']}")
            for w in r.get("warnings", [])[:2]:
                print(f"      [提示] {str(w)[:110]}")
            for e in r.get("errors", [])[:2]:
                print(f"      [错误] {str(e)[:110]}")

            # 记录成功时间（single 全刷 / dates 有落盘或已最新）
            if r["ok"] and not dry_run:
                state.setdefault(sp.name, {})
                state[sp.name]["last_update"] = datetime.now().isoformat(timespec="seconds")
                state[sp.name]["last_target_date"] = tdate

    dur = time.time() - t_start

    if not dry_run:
        save_state(state)

    # ── 汇总 ──
    ran = [r for r in results if r["action"] != "skip"]
    ok_n = sum(1 for r in ran if r["ok"])
    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    print(f"  执行 {len(ran)} 张：成功 {ok_n} | 失败 {len(ran)-ok_n} |"
          f" 跳过 {len(results)-len(ran)}")
    print(f"  请求数 {sum(r['requests'] for r in results)} |"
          f" 落盘 {sum(r['rows'] for r in results):,} 行"
          f"（新增 {sum(r['added'] for r in results):,}） | 耗时 {dur:.1f}s")
    print(f"  {limiter.status_str()}")

    fails = [r for r in ran if not r["ok"]]
    if fails:
        print("\n  失败明细:")
        for r in fails:
            errs = r.get("errors") or ["(无错误信息)"]
            print(f"    {r['table']:<26} {str(errs[0])[:88]}")

    if dry_run:
        print("\n  ※ DRY-RUN：未发起任何网络请求，未写入任何文件。")
    else:
        freshness_report(specs, verbose=True)
    return results


def status_only(tier=None, only=None):
    """只做新鲜度体检（零请求）"""
    specs = ([sp_mod.get(only)] if only else
             (list(sp_mod.BY_TIER.get(tier, [])) if tier else list(sp_mod.ALL_SPECS)))
    print("=" * 92)
    print(f"alt 库新鲜度体检（零请求）   {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 92)
    rows = freshness_report(specs, verbose=False)
    print(f"{'表名':<26}{'状态':<10}{'最新':<12}{'距今':<9}{'阈值':<7}")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: (x["state"] != "OK", -(x.get("age") or 0))):
        age = f"{r['age']} 天" if r.get("age") is not None else "—"
        print(f"{r['table']:<26}{r['state']:<10}{r['latest'] or '—':<12}"
              f"{age:<9}{r.get('threshold', '—'):<7}")
    bad = [r for r in rows if r["state"] != "OK"]
    print("-" * 92)
    print(f"  正常 {len(rows)-len(bad)} | 异常 {len(bad)}")
    return rows


# ============================================================
# 六、CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser(description="alt 库每日增量更新")
    p.add_argument("--tier", choices=sp_mod.TIERS, help="按优先级执行（自动派生自 spec，勿硬编码）")
    p.add_argument("--only")
    p.add_argument("--dry-run", action="store_true", help="只输出计划，不发请求")
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                   help=f"复核窗口（交易日数），默认 {DEFAULT_LOOKBACK}")
    p.add_argument("--force", action="store_true", help="忽略刷新间隔强制全刷")
    p.add_argument("--status", action="store_true", help="只做新鲜度体检（零请求）")
    p.add_argument("--list", action="store_true", help="列出可更新表与策略")
    args = p.parse_args(argv)

    if args.status:
        status_only(args.tier, args.only)
        return 0

    if args.list:
        print("=" * 92)
        print("alt 库可更新表（按布局分策略）")
        print("=" * 92)
        for layout, label in (("single", "全量刷新"), ("by_year", "按日增量")):
            ss = [s for s in sp_mod.ALL_SPECS if s.layout == layout]
            built = [s for s in ss if table_dir_exists(s.name)]
            print(f"\n[{layout}] {label} — {len(built)}/{len(ss)} 已建")
            for s in ss:
                mark = "✓" if table_dir_exists(s.name) else " "
                iv = f"每{s.update_every_days}天" if layout == "single" else "每日"
                print(f"  {mark} {s.tier}  {s.name:<26}{s.note[:34]:<36}{iv}")
        return 0

    run(tier=args.tier, only=args.only, dry_run=args.dry_run,
        lookback=args.lookback, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
