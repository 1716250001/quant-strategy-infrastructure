# -*- coding: utf-8 -*-
"""
fetch/periodic_update.py — 事件面表的「周期性更新」通道（三轮审查 E-01 专项）
==============================================================================
补齐"这张表每天跑不跑"之外的**第三种归宿**：

    ① DAILY_UPDATE_GROUPS  —— 每天跑（core / extend）
    ② DAILY_UPDATE_EXEMPT  —— 刻意不日更（须写理由）
    ③ 本模块（periodic）    —— **周期性 + 增量窗口**（须写理由）

为什么需要第三种
----------------
`dividend`（公司行动装载源）与 `namechange`（ST 判定源）是 btf 引擎股票侧的
**数据源**，但 mode=by_code ⇒ 既不在日更组、也不在豁免清单（豁免清单的判据只
扫 by_date / freq=day）⇒ **无归宿、无陈旧监控**。实证后果（2026-10-11）：
namechange 库内最新 start_date 停在 20260912（滞后 4 周）而**无人察觉**；
dividend 靠手动续命。危害形态 = 表内「无行」与「真无事件」不可区分 ——
近端股票回测的新分红/送转/ST 事件**静默缺失**，且无 fail-closed 防线。

为什么不并入日更组（成本核算，实测 2026-10-11）
----------------------------------------------
两表 codes=stock_all（约 5,900 标的）⇒ by_code 全量重拉 = 5,900 请求/天/表
（170 次/分 ≈ 35 分钟/表），日更代价与收益完全不成比例。
而实测两接口**都支持按日期拉取**：
  · namechange  start_date / end_date（**公告日期**区间）→ 1 请求/次
  · dividend    ann_date（**公告日期**单日横截面）     → 1 请求/日
⇒ 正确形态 = 每 interval_days 跑一次，只拉最近 lookback_days 窗口，
  落盘按主键去重（幂等、可重放、无状态也能自愈）。

调度与幂等
----------
- **到期判定**：状态文件 `.periodic_state.json`（库根）记 last_run；未到期即跳过
  （零请求）。数据陈旧超过 stale_after_days 时**无视间隔强制补**
  （自愈：状态文件丢了也不会永久停更）。
- **窗口计算**：`(库内最新 recency_col, 今天]` ∩ 最近 lookback_days。
  recency_col 用**公告日**（如 ann_date）而非分区列 —— 分区列可能是"生效日"，
  会含未来值（实证 namechange 有 start_date=20261015 的行），不可作新鲜度信号。
- **重跑安全**：写盘走 `upsert_by_year`（按主键去重，keep=last）⇒ 重复拉同一窗口
  不产生重复行；**同键值修正也会落盘**（E-02 修复后）。

用法
----
  python -m fetch.periodic_update --status          # 只看状态（零请求）
  python -m fetch.periodic_update                   # 到期则更新
  python -m fetch.periodic_update --force           # 无视间隔强制更新
  python -m fetch.periodic_update --only namechange # 只跑一张表
  python -m fetch.periodic_update --dry-run         # 取数但不落盘

⚠ 陈旧监控落点：`tools/doctor.py` 第 10 项「事件面表」；
  归宿登记真源：`config.PERIODIC_UPDATE_TARGETS`（回归 R10 校验）。
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd

from config import MARKET_DATA_DIR, PERIODIC_UPDATE_TARGETS
from common import reader
from common.calendar import trade_dates_between
from common.parquet_store import upsert_by_year
from common.paths import ensure_dir
from fetch.base import get_pro, RateLimiter, ts_call_with_retry

#: 状态文件（库根；与 .checkpoint.json 同层）
STATE_NAME = ".periodic_state.json"

#: by_date 单次运行最多拉的公告日数（防"长期停更后一次拉几百天"）。
#: 超出即截断并 WARN —— 剩余窗口下次运行继续（窗口起点由数据本身推进，不靠状态）。
MAX_DATES_PER_RUN = 90

#: 日期列归一化目标（源端 dtype 混杂：str / Timestamp / int）
_DATE_COLS = ("start_date", "end_date", "ann_date", "ex_date",
              "pay_date", "record_date", "imp_ann_date", "div_listdate")


# ============================================================
# 状态文件
# ============================================================
def state_path():
    return os.path.join(MARKET_DATA_DIR, STATE_NAME)


def load_state():
    p = state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  [WARN] 状态文件损坏（视为空，将触发一次更新）: {str(e)[:60]}")
        return {}


def save_state(state):
    """原子写（先写临时文件再替换）—— 与 fetch.base.Checkpoint 同纪律。"""
    p = state_path()
    ensure_dir(os.path.dirname(p))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)


# ============================================================
# 新鲜度 / 到期判定
# ============================================================
def _days(a, b):
    """YYYYMMDD 差值（b - a，自然日）；任一解析失败返回 None。"""
    try:
        return (datetime.strptime(str(b), "%Y%m%d")
                - datetime.strptime(str(a), "%Y%m%d")).days
    except Exception:
        return None


def _today():
    return datetime.now().strftime("%Y%m%d")


def data_recency(table, col, max_files=3):
    """库内该表 `col` 的最大值（YYYYMMDD 字符串）；无数据/列缺失 → None。

    ⚠ 不用 `reader.latest_date`：它按分区列取 max，而分区列可能是"生效日"
      （namechange.start_date 实测含未来值 20261015）⇒ 新鲜度会被未来的
      生效日掩盖。此处按**公告日类**信号列取 max，且只读最近 max_files 个
      年度文件（by_year 布局下足够；by_code/single 布局回退全量读）。
    """
    layout = reader.detect_layout(table)
    if layout == "none":
        return None
    d = os.path.join(MARKET_DATA_DIR, table)
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    except OSError:
        return None
    if not files:
        return None
    if layout == "by_year":
        files = files[-max_files:]
    best = None
    for f in files:
        try:
            s = pd.read_parquet(os.path.join(d, f), columns=[col])[col]
        except Exception:
            continue
        if not len(s):
            continue
        v = str(s.dropna().astype(str).max()) if len(s.dropna()) else None
        if v and (best is None or v > best):
            best = v
    return best


def is_due(name, spec, state, force=False):
    """到期判定 → (due: bool, reason: str)。"""
    entry = state.get(name) or {}
    last_run = entry.get("last_run")
    interval = int(spec.get("interval_days", 7))
    recency = data_recency(name, spec["recency_col"])
    stale_after = int(spec.get("stale_after_days", 45))
    age = _days(recency, _today()) if recency else None

    if force:
        return True, "强制（--force）"
    if recency is None:
        return True, "库内无数据（自愈全窗口拉取）"
    if age is not None and age > stale_after:
        # 数据本身已陈旧 ⇒ 无视间隔补（状态文件丢失也不会永久停更）
        return True, f"数据陈旧（最新 {recency}，落后 {age} 天 > {stale_after}）"
    if not last_run:
        return True, "首次运行（无状态记录）"
    since = _days(last_run, _today())
    if since is None or since >= interval:
        return True, f"距上次运行 {since} 天 ≥ 间隔 {interval} 天"
    return False, f"未到期（距上次 {since} 天 < {interval} 天）"


def pull_window(spec, recency):
    """本次要拉的窗口 (start, end]；空窗口 → None。

    起点 = max(库内最新公告日, 今天 − lookback_days)
      · 数据新鲜 ⇒ 只拉最近几天（便宜）
      · 数据陈旧 ⇒ 自 recency 起补（一次补齐缺口）
    """
    end = _today()
    lookback = int(spec.get("lookback_days", 60))
    floor = (datetime.strptime(end, "%Y%m%d")
             - timedelta(days=lookback)).strftime("%Y%m%d")
    if not recency:
        start = floor
    else:
        start = recency if recency > floor else floor
    if start >= end:
        return None
    return start, end


# ============================================================
# 取数
# ============================================================
def _normalize(df):
    """日期列统一为 YYYYMMDD 字符串（源端 dtype 混杂）。"""
    if df is None or not len(df):
        return df
    df = df.copy()
    for c in _DATE_COLS:
        if c in df.columns:
            s = df[c]
            if pd.api.types.is_datetime64_any_dtype(s):
                s = s.dt.strftime("%Y%m%d")
            df[c] = s.astype(str).str.replace("-", "", regex=False).str[:8]
            df[c] = df[c].replace({"nan": None, "None": None, "": None})
    return df


def _prepare(df, spec):
    """归一化 + 丢弃「分区列为空」行 → (df, 拉到行数, 丢弃行数, err)。

    ⚠ 必须把「源端返空」（df=None，**不是错误**）与「列缺失」（fail-closed 错误）
      分开表达 —— 二者都用 None 表示时，调用方无法区分，会把"当日无公告"
      误判为"数据损坏"并**跳过后续的补码段**（2026-10-11 实测踩到）。
    """
    df = _normalize(df)
    n_pulled = 0 if df is None else len(df)
    if df is None or not len(df):
        return df, n_pulled, 0, None
    col = spec["date_col"]
    if col not in df.columns:
        msg = f"缺列 {col}"
        print(f"    [ERROR] 返回数据{msg}，跳过落盘（fail-closed）")
        return None, n_pulled, 0, msg
    mask = df[col].notna()
    n_dropped = int((~mask).sum())
    if n_dropped:
        print(f"    [WARN] 丢弃 {n_dropped} 行「{col} 为空」（无法分区；已披露不静默）")
    return df[mask], n_pulled, n_dropped, None


def fetch_by_range(pro, limiter, name, spec, start, end, verbose=True):
    """区间拉取（namechange 形态）：1 请求。"""
    params = {spec["start_param"]: start, spec["end_param"]: end}
    return ts_call_with_retry(pro, name, limiter, params, verbose=verbose)


def fetch_by_date(pro, limiter, name, spec, start, end, verbose=True):
    """逐公告日横截面拉取（dividend 形态）：窗口内交易日各 1 请求。"""
    dates = trade_dates_between(start, end)
    if len(dates) > MAX_DATES_PER_RUN:
        print(f"    [WARN] 窗口内 {len(dates)} 个交易日 > 单次上限 "
              f"{MAX_DATES_PER_RUN}，本次只拉最近 {MAX_DATES_PER_RUN} 天"
              f"（剩余窗口下次运行继续）")
        dates = dates[-MAX_DATES_PER_RUN:]
    frames = []
    for i, d in enumerate(dates, 1):
        df = ts_call_with_retry(pro, name, limiter, {spec["date_param"]: d},
                                verbose=verbose)
        if df is not None and len(df):
            frames.append(df)
        if i % 20 == 0:
            print(f"    [{i}/{len(dates)}] 累计 {sum(len(x) for x in frames)} 行")
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


_CKPT_CACHE: dict[str, dict] = {}


def _completed_codes(name, filename=".backfill_checkpoint.json"):
    """断点里该表"已完成"的标的集合（**只读直读 JSON**，进程内缓存）。

    为什么不直接用 `fetch.base.Checkpoint`：它的构造会打印「[断点] 已加载」
    ——状态查询（`--status`，声称零副作用/零请求）里反复打印会污染输出。
    只读路径直读文件；**写断点仍走 Checkpoint**（沿用其锁 + fsync 纪律）。
    """
    if filename not in _CKPT_CACHE:
        p = os.path.join(MARKET_DATA_DIR, filename)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            _CKPT_CACHE[filename] = data if isinstance(data, dict) else {}
        except Exception:
            _CKPT_CACHE[filename] = {}
    return set((_CKPT_CACHE[filename].get(name) or {}).get("completed", []))


def missing_codes(name, spec):
    """「从未回补过」的标的列表（断点口径；**零 API 请求**）→ (list, err)。

    ⚠ 判据与 db_audit 的 by_code 缺口**同源**（都用 `build_tasks(kind='code')`
      ＋ `.backfill_checkpoint.json`）——本函数补齐后，同一判据即报"无缺口"。
    """
    from config import BACKFILL_TARGETS, DAILY_UPDATE_EXTRA
    from fetch.backfill_spec import build_tasks

    conf = DAILY_UPDATE_EXTRA.get(name) or BACKFILL_TARGETS.get(name)
    if conf is None:
        return [], "无属性定义"
    try:
        tasks, kind = build_tasks(name, conf, dry_run=True)
    except Exception as e:
        return [], f"build_tasks 失败 {str(e)[:60]}"
    if kind != "code":
        return [], ""
    expect = [str(t) for t in tasks]
    return sorted(set(expect) - _completed_codes(name)), ""


def fill_missing_codes(name, spec, pro, limiter, dry_run=False, verbose=False):
    """补齐「从未回补过」的标的（断点口径）→ (成功数, 新增行数, 失败列表)。

    为什么必须放在本通道（2026-10-11 实证，E-01 连带）:
      dividend / namechange 在 config 里仍是 `mode=by_code`，而**磁盘已是 by_year
      布局** ⇒ 走 `main.py backfill --only <表>` 会命中 `common.parquet_store`
      的 `_assert_by_code_target` 硬拦（拒绝在按年目录里写 `{code}.parquet`，
      否则布局被判成 mixed、下游读数静默降级）。于是这些表的
      **"新上市标的从未回补"** 缺口**没有任何既有路径可补** ——
      实证：dividend 断点 5,882 / 应有 5,911（29 只 2026 年新标的未回补）。
      本函数按断点算出缺口标的，逐只拉取后**按年合并落盘**（＝by_year 合法路径），
      并把成功标的记入断点 ⇒ `db audit` 的 by_code 缺口随之闭合（可复算）。

    ⚠ 记断点口径与引擎一致：**调用成功即记**（返空也算完成——"该标的无事件"
      与"没拉过"必须区分；这正是缺口判据的意义）。
    """
    from config import BACKFILL_TARGETS, DAILY_UPDATE_EXTRA
    from fetch.base import Checkpoint, ts_fetch

    conf = DAILY_UPDATE_EXTRA.get(name) or BACKFILL_TARGETS.get(name)
    if conf is None:
        return 0, 0, [f"{name}: 无属性定义"]
    missing, err = missing_codes(name, spec)
    if err:
        return 0, 0, [f"{name}: {err}"]
    if not missing:
        return 0, 0, []
    print(f"    未回补标的 {len(missing)} 只（断点口径）——逐只拉取后按年合并")
    cp = None
    if not dry_run:
        cp = Checkpoint(MARKET_DATA_DIR, filename=".backfill_checkpoint.json")
    n_ok = n_rows = 0
    failed: list[str] = []
    max_rows = int(conf.get("max_rows", 5000) or 5000)
    for i, code in enumerate(missing, 1):
        df, ok = ts_fetch(pro, spec.get("api", name), limiter, code,
                          max_rows=max_rows, verbose=verbose)
        if not ok:
            failed.append(code)
            continue
        n_ok += 1
        if df is not None and len(df):
            d2 = _normalize(df)
            col = spec["date_col"]
            if col in d2.columns:
                d2 = d2[d2[col].notna()]
                if len(d2) and not dry_run:
                    r = upsert_by_year(d2, os.path.join(MARKET_DATA_DIR, name),
                                       date_col=col, subset=spec["subset"],
                                       sort_by=col)
                    n_rows += int(r.get("added", 0))
        if cp is not None:
            cp.mark_done(name, code)        # 成功（含返空）即记断点
        if i % 10 == 0 or i == len(missing):
            print(f"      [{i}/{len(missing)}] 新增 {n_rows} 行")
    if cp is not None:
        cp.save()
        _CKPT_CACHE.pop(".backfill_checkpoint.json", None)   # 写后失效只读缓存
    return n_ok, n_rows, failed


# ============================================================
# 主流程
# ============================================================
def run_periodic_update(only=None, dry_run=False, force=False, status_only=False,
                        verbose=False, quiet=False, record_version=True):
    """执行周期性更新。返回 {表名: 结果字典}。

    参数:
        only:        只跑指定表（序列或逗号分隔字符串）
        dry_run:     取数但不落盘（仍发请求）
        force:       无视间隔强制更新
        status_only: 只打印状态（零请求）
        verbose:     打印每次 API 重试日志
        quiet:       静音（供 daily_update 内嵌调用；只打印必要告警）
        record_version: 是否写库版本（08 §13.5；EE-D11-1 动作 A）。**被日更内嵌时
                     由日更统一记一次** ⇒ 内嵌调用须传 False（防同日双计）。
    """
    if only and isinstance(only, str):
        only = [x.strip() for x in only.split(",") if x.strip()]
    only = set(only) if only else None

    names = [n for n in PERIODIC_UPDATE_TARGETS if not only or n in only]
    if not names:
        print("  [WARN] 无可运行的周期表（检查 --only 是否拼错）")
        return {}

    state = load_state()
    results = {}

    if not quiet:
        print("=" * 78)
        print("  事件面表 · 周期性更新（config.PERIODIC_UPDATE_TARGETS）")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
              f"{'  [DRY-RUN]' if dry_run else ''}")
        print("=" * 78)
        print(f"  {'表':14s} {'库内最新':>10s} {'落后':>5s}  判定")
        print("-" * 78)

    plan = []
    for name in names:
        spec = PERIODIC_UPDATE_TARGETS[name]
        recency = data_recency(name, spec["recency_col"])
        age = _days(recency, _today()) if recency else None
        miss, _err = missing_codes(name, spec)
        due, why = is_due(name, spec, state, force=force)
        if miss and not due:
            # 未回补标的（新上市/历史漏拉）是**与时间窗口无关的独立缺口**：
            # 一旦存在即到期（否则"窗口已最新"会把它们永久掩盖）。
            due, why = True, f"未回补标的 {len(miss)} 只（断点口径）"
        if not quiet:
            extra = f" | 未回补 {len(miss)} 只" if miss else ""
            print(f"  {name:14s} {recency or '无数据':>10s} "
                  f"{(str(age) if age is not None else '-'):>5s}  "
                  f"{'[到期]' if due else '[跳过]'} {why}{extra}")
        if due:
            plan.append((name, spec, recency, why, len(miss)))

    if status_only:
        return {n: {"due": True} for n, _s, _r, _w, _m in plan}

    if not plan:
        if not quiet:
            print("\n  全部未到期，无需更新（零请求）")
        return {}

    pro = get_pro()
    limiter = RateLimiter(rate_per_min=170, daily_limit=90000)
    t0 = time.time()

    for name, spec, recency, why, n_miss in plan:
        window = pull_window(spec, recency)
        if window is None and not n_miss:
            if not quiet:
                print(f"\n  [{name}] 已是最新（窗口为空），跳过")
            state.setdefault(name, {}).update(
                {"last_run": _today(), "last_why": why,
                 "last_window": None, "last_rows_pulled": 0,
                 "last_rows_added": 0, "last_requests": 0})
            results[name] = {"skipped": "窗口为空"}
            continue

        before = limiter.total_requests
        n_pulled = added = 0
        start = end = None
        if window is None:
            print(f"\n  [{name}] 事件窗口已最新；仅补未回补标的 — {why}")
        else:
            start, end = window
            print(f"\n  [{name}] 窗口 ({start}, {end}] — {why}")
            try:
                if spec["pull"] == "by_range":
                    df = fetch_by_range(pro, limiter, name, spec, start, end,
                                        verbose=verbose)
                else:
                    df = fetch_by_date(pro, limiter, name, spec, start, end,
                                       verbose=verbose)
            except Exception as e:
                print(f"    [ERROR] 取数失败: {type(e).__name__}: {str(e)[:100]}")
                results[name] = {"error": str(e)[:120]}
                continue
            df, n_pulled, n_dropped, err = _prepare(df, spec)
            if err:
                results[name] = {"error": err}
                # ⚠ 仍继续到补码段（窗口失败不该连带放弃独立的补码缺口）
                df = None
            if df is not None and len(df):
                print(f"    拉到 {n_pulled} 行（丢弃 {n_dropped}）→ 落盘合并")
                if not dry_run:
                    r = upsert_by_year(
                        df, os.path.join(MARKET_DATA_DIR, name),
                        date_col=spec["date_col"], subset=spec["subset"],
                        sort_by=spec["date_col"])
                    added = int(r.get("added", 0))
                    if r.get("fail"):
                        print(f"    [ERROR] {r['fail']} 个年份分区写入失败")
                        results[name] = {"error": "分区写入失败", **r}
                        continue
                    print(f"    新增 {added} 行（幂等：重复拉取不再增行）")
                else:
                    print("    [DRY-RUN] 不落盘")
            elif not err:
                # 源端返空（窗口内无公告）与"列缺失"必须分述——前者正常，后者
                # 已在 `_prepare` 打过 ERROR（2026-10-11：二者混用会让"无公告日"
                # 被读成"数据损坏"，并连带跳过补码段）。
                print("    窗口内无数据（源端返空；不落盘）")

        # ── 未回补标的补齐（与窗口无关的独立缺口；断点口径）──
        n_codes = n_code_rows = 0
        code_failed: list[str] = []
        if n_miss:
            try:
                n_codes, n_code_rows, code_failed = fill_missing_codes(
                    name, spec, pro, limiter, dry_run=dry_run, verbose=verbose)
            except Exception as e:
                print(f"    [ERROR] 补码失败: {type(e).__name__}: {str(e)[:100]}")
                code_failed = [f"exception: {str(e)[:80]}"]
            if dry_run:
                print(f"    [DRY-RUN] 未回补标的 {n_miss} 只不写盘/不记断点")
            else:
                print(f"    补码完成: {n_codes}/{n_miss} 只 | 新增 {n_code_rows} 行"
                      + (f" | 失败 {len(code_failed)}" if code_failed else ""))

        if not dry_run:
            req = limiter.total_requests - before
            state.setdefault(name, {}).update({
                "last_run": _today(), "last_why": why,
                "last_window": ([start, end] if start else None),
                "last_rows_pulled": n_pulled, "last_rows_added": added,
                "last_codes_filled": n_codes, "last_code_rows_added": n_code_rows,
                "last_requests": req})
            results[name] = {"window": ([start, end] if start else None),
                             "pulled": n_pulled, "added": added,
                             "codes_filled": n_codes,
                             "code_rows_added": n_code_rows,
                             "code_failed": code_failed, "requests": req}

    if not dry_run and plan:
        save_state(state)
        print(f"\n  状态已记录: {state_path()}")
        # ── 库版本（08 §13.5；仅**独立运行**时记——被日更内嵌时由日更统一记，
        #    见 record_version 参数；只记真正处理过的表，跳过的不算变更）──
        if record_version:
            changed = [n for n, r in results.items()
                       if isinstance(r, dict) and "skipped" not in r]
            if changed:
                try:
                    from tools.db_version import record as _dbv
                    _dbv(changed, note="事件面表周期通道")
                except Exception as e:
                    print(f"  [WARN] db_version 记录失败（不影响主流程）: {str(e)[:80]}")

    if not quiet:
        print(f"\n  完成: {len(results)}/{len(plan)} 张表 | "
              f"总耗时 {time.time() - t0:.1f}s")
        if hasattr(limiter, "status_str"):
            print(f"  {limiter.status_str()}")
    return results


# ============================================================
# CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser(
        description="事件面表周期性更新（fetch/periodic_update；E-01 通道）")
    p.add_argument("--only", default=None, help="只跑指定表, 逗号分隔")
    p.add_argument("--force", action="store_true", help="无视间隔强制更新")
    p.add_argument("--dry-run", action="store_true", help="取数但不落盘")
    p.add_argument("--status", action="store_true", help="只看状态（零请求）")
    p.add_argument("--verbose", action="store_true", help="打印每次 API 重试日志")
    a = p.parse_args(argv)
    r = run_periodic_update(only=a.only, dry_run=a.dry_run, force=a.force,
                            status_only=a.status, verbose=a.verbose)
    return 0 if not any("error" in v for v in r.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
