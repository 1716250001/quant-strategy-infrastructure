# -*- coding: utf-8 -*-
"""
fetch/daily_update.py — 每日增量更新（统一走 backfill 引擎）
==============================================================
把"每日盘后数据更新"从原来的"按 ts_code 逐文件读写"改为复用
fetch/backfill.py 的 by_date 执行路径：

    按日并发拉取  →  YearBatchWriter 批量落盘  →  断点三态纪律

为什么必须换
------------
1. 全库已统一为按年分区（by_year）布局。旧路径（upsert_grouped 逐 ts_code
   写文件）会在年度目录里凭空生成数千个小文件，并把 reader 的布局探测
   打回 by_code，导致下游全部读数降级。
2. 落盘成本差一个量级：by_code 下 daily 5,903 个文件各做一次
   read→concat→write ≈ 111 秒/天，index_daily ≈ 200 秒/天；
   by_year 每天只改 1 个年度文件，毫秒级。

两个设计要点
------------
1. **复核窗口**（config.DAILY_UPDATE_LOOKBACK，默认 3 个交易日）
   只按"库内最新日期"算缺口，会漏掉"断点已记但当日数据残缺"的情况：
   实测 2026-09-16 的 daily_basic 仅入库 130 行（正常约 5550 行），
   而库内最新日期仍显示 20260917，按最新日期算缺口会判定为已完成。
   故每日强制重拉最近 N 个交易日，靠落盘主键去重保证幂等。

2. **逐表独立**：每个表独立算缺口、独立记断点；某表失败不影响其他表。
   这是原 gap-update 就有的纪律，此处保留。

与旧命令的关系
--------------
main.py gap-update 现在直接调用本模块（命令名不变，定时任务无需改）。
原 fetch/gap_update.py、fetch/intraday_update.py 的逐文件落盘路径已废弃，
仅在需要"重放旧行为"时保留（见各自的 deprecation 说明）。

用法:
  python main.py gap-update                  # 补缺口 + 复核最近3个交易日
  python main.py gap-update --group all      # 追加资金面/事件面接口
  python main.py gap-update --lookback 10    # 扩大复核窗口（修历史残缺）
  python main.py gap-update --dry-run        # 只输出计划, 不发请求
  python -m fetch.daily_update --list        # 列出每日更新接口清单
"""
import os
import time
import argparse
from datetime import datetime

from config import (
    MARKET_DATA_DIR, BACKFILL_TARGETS,
    DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS, DAILY_UPDATE_GROUPS_ALIASES,
    DAILY_UPDATE_LOOKBACK,
    FETCH_MAX_WORKERS, BACKFILL_PARALLEL_TARGETS,
)
from common import reader
from common.calendar import (open_dates, recent_trade_dates,
                             trade_dates_between, open_dates_by_market)
from fetch.base import get_pro, get_latest_trade_date, Checkpoint
from fetch.backfill import run_target


# ============================================================
# 配置解析
# ============================================================
def get_target_conf(name):
    """取 target 配置：先查每日专有定义，再查 backfill 公共定义。

    两者结构同构，因此可共用 run_target 执行；不重复定义是为了避免
    "同一接口两份配置漂移"（如 moneyflow 只在 BACKFILL_TARGETS 里定义）。
    """
    if name in DAILY_UPDATE_EXTRA:
        return DAILY_UPDATE_EXTRA[name]
    if name in BACKFILL_TARGETS:
        return BACKFILL_TARGETS[name]
    return None


def expand_groups(groups):
    """把组别名展开成 target 名列表（保序、去重）。"""
    out = []
    for g in groups:
        for real in DAILY_UPDATE_GROUPS_ALIASES.get(g, [g]):
            for n in DAILY_UPDATE_GROUPS.get(real, []):
                if n not in out:
                    out.append(n)
    return out


# ============================================================
# 引擎能力判定（单一真源）
# ============================================================
def engine_supports_daily(conf):
    """该表的 mode 是否被本引擎**真正执行**（而非进入后静默跳过）。

    ⚠ 为什么必须抽成函数（2026-09-22）:
      回归 R10 的「假纳入」判据要校验"进了 DAILY_UPDATE_GROUPS 的表，
      引擎是否真会跑它"。若 R10 自己重写一份 mode 白名单，就又是
      "两个结构各管一摊"——改一处漏一处、且全程不报错。
      故此处作为**单一真源**，R10 直接 import 引用。

    支持的模式:
      by_date  → 逐日增量（含复核窗口）
      by_range → 按区间拉（"缺口"= 1 个区间任务）
      by_code  → **仅当** conf["daily_full_refresh"] 为真时支持
                 （语义 = 每天全量重拉，用于 index_global 这类
                   "只能按标的一次拉全历史、无日期入参" 的表）
    """
    m = conf.get("mode")
    if m in ("by_date", "by_range"):
        return True
    return m == "by_code" and bool(conf.get("daily_full_refresh"))


# ============================================================
# 缺口计算
# ============================================================
def resolve_gaps(name, conf, target_date, lookback):
    """算出该表今天需要跑的日期列表。

    返回 (dates, info)
      dates = 缺口日期 ∪ 复核窗口（升序）
      info  = {"latest": 库内最新日期, "gap": [...], "refresh": [...]}

    缺口判据: 库内最新日期（由 reader.latest_date 布局无关地取得）
              → target_date 之间的交易日。

    ⚠ 必须按该表所属**市场**的日历取日期（2026-09-19 修复）:
      以前写死 A 股日历（open_dates / recent_trade_dates），对美股表会出错：
        · 多拉 A 股独有的交易日（美股休市）→ 全部返空、白耗请求
        · 漏掉美股独有的交易日（如 A 股国庆调休期间美股照常开市）
      实测 2026-09：A 股 21 个交易日 vs 美股 13 个。
      现改为读 conf["calendar"]（默认 "SSE"；"us" → 美股日历）。

    ⚠ by_range 分支（2026-09-21 新增）:
      by_range 是"传 start_date/end_date 拉一段区间"，**没有逐日任务概念**，
      故 dates 返回的是**区间任务列表** `[(start, end)]`（1 个元素）。
      为什么以前没有它：daily_update 原先 `mode != by_date` 一律跳过，
      by_range 族（shibor/美债 5 张）因此**从不参与日更、静默变旧**
      （实证 shibor 库内最新仅 2026-09-16，而当日已 09-21）。
    """
    table = name
    date_col = conf.get("date_col") or "trade_date"

    # ── by_range: 拉"库内最新 → 目标日"的区间（落盘按主键去重，幂等安全）──
    # ⚠ 不要把上面那个 date_col 传进去：它的默认值是 "trade_date"，
    #   而 by_range 表的 conf 里**没有** date_col 字段 → 会传入错误的默认值
    #   覆盖掉正确列名（实证：shibor 的列名是 "date"，传 trade_date 会读到
    #   None → 误判"库内无数据" → 每次都从 start 全量重拉）。
    #   by_range 的权威列名来源是 TABLE_DATE_COL，由 _resolve_gaps_range 自取。
    if conf.get("mode") == "by_range":
        return _resolve_gaps_range(table, conf, target_date)

    cal = conf.get("calendar", "SSE")
    reader.clear_cache(table)
    latest = reader.latest_date(table, date_col)

    # 该市场截至 target_date 的全部交易日（升序）
    all_dates = open_dates_by_market(cal, end_date=target_date)

    refresh = all_dates[-lookback:] if (lookback and all_dates) else []

    if not latest:
        # 库为空 → 从 start 兜底全量（自愈；会打印醒目提示）
        start = conf.get("start", "20160101")
        gaps = [d for d in all_dates if d >= str(start)]
        return sorted(set(gaps)), {"latest": None, "gap": gaps, "refresh": refresh}

    # 左开右闭: (latest, target_date]
    gaps = [d for d in all_dates if str(latest) < d <= target_date]
    dates = sorted(set(gaps) | set(refresh))
    return dates, {"latest": latest, "gap": gaps, "refresh": refresh}


def _resolve_gaps_range(name, conf, target_date, date_col=None):
    """by_range 表的"缺口" = 一个区间任务 `[(库内最新日, 目标日)]`。

    设计要点:
      ① **为什么不是逐日**: by_range 接口收 start_date/end_date、一次返回整段，
         没有"某一天"的概念，故返回 1 个元组任务而非日期列表。
      ② **为什么起点取库内最新日（而非 +1 天）**: 与 by_date 的"复核窗口"精神
         一致 —— 重拉最新那一天可修"当日数据残缺"；且落盘按主键去重，
         重复拉**幂等安全**，不会产生重复行。
      ③ **已是最新则不跑**: `latest >= target_date` 返回空，不白耗请求
         （对 1次/小时 族尤其重要）。
      ④ **date_col 必须从 TABLE_DATE_COL 取**: 这些表的 conf 里**没有**
         date_col 字段（by_range 原先不做分区），而 resolve_gaps 的默认值是
         "trade_date"，直接用会读错列（实证 shibor 的列名是 "date"）。
    """
    from config import TABLE_DATE_COL
    dc = (date_col or conf.get("date_col")
          or TABLE_DATE_COL.get(name) or "date")
    reader.clear_cache(name)
    latest = reader.latest_date(name, dc)

    info = {"latest": latest, "gap": [], "refresh": [], "kind": "range"}
    if not latest:
        # 库为空 → 从 start 一次拉全（自愈）
        start = conf.get("start", "20160101")
        task = (str(start), str(target_date))
        info["gap"] = [task]
        return [task], info
    if str(latest) >= str(target_date):
        return [], info                     # 已是最新
    task = (str(latest), str(target_date))
    info["gap"] = [task]
    return [task], info


# ============================================================
# 主流程
# ============================================================
def run_daily_update(target_date=None, dry_run=False, groups=("core",),
                     lookback=None, workers=None, only=None,
                     verbose=False, with_market_state=True):
    """每日增量更新。

    参数:
        target_date: YYYYMMDD；None = 自动取最近交易日
        groups:      要跑的组（core / extend / all）
        lookback:    复核窗口交易日数；None = config 默认
        only:        只跑指定的表名（逗号分隔字符串或序列）
        with_market_state: 收尾是否重算 market_state 派生表
    """
    if lookback is None:
        lookback = DAILY_UPDATE_LOOKBACK
    if workers is None:
        workers = FETCH_MAX_WORKERS

    target_date = get_latest_trade_date(target_date)

    names = expand_groups(groups)
    if only:
        # ⚠ 两条入口的解析口径必须一致（2026-09-19 修复）:
        #   本模块 CLI 直跑时（`python -m fetch.daily_update --only a,b`）
        #   argparse 给的是**已 split 的列表**；而 main.py 的 gap-update 传入的
        #   是**原始字符串** "a,b,c"。原实现 `[only] if isinstance(only, str)` 会把它
        #   包成 ["a,b,c"] 当成单个表名 → 报"配置缺失"并静默什么都不做。
        #   实证: `main.py gap-update --only margin,margin_detail,hk_hold --dry-run`
        #         输出 "[跳过] 配置缺失"，而逐个单表跑却正常。
        if isinstance(only, str):
            only = [x.strip() for x in only.split(",") if x.strip()]
        else:
            only = list(only)
        names = [n for n in only if n]

    print("=" * 78)
    print("  每日增量更新（by_date 并发 + 按年批量落盘）")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标交易日: {target_date} | 复核窗口: 最近 {lookback} 个交易日")
    print(f"  并发: 每接口 {workers} 线程 | 存储: {os.path.abspath(MARKET_DATA_DIR)}")
    print(f"  模式: {'DRY-RUN（只算缺口，不发请求）' if dry_run else '正式拉取'}")
    print(f"  接口组: {', '.join(groups)} → {len(names)} 个接口")
    print("=" * 78)

    # ── Step 1: 逐表算缺口 ──
    print("\n[Step 1] 缺口分析")
    print("-" * 78)
    print(f"  {'接口':16s} {'库内最新':>10s} {'缺口':>5s} {'复核':>5s}  待跑日期")
    print("-" * 78)

    plan = []
    for name in names:
        conf = get_target_conf(name)
        if conf is None:
            print(f"  {name:16s}  [跳过] 配置缺失")
            continue
        # ⚠ 2026-09-21: 放开 by_range（原为 `!= "by_date"` 一律跳过）
        #   原行为导致 by_range 族（shibor/美债5张）**从不参与日更、静默变旧**：
        #   把它们加进 DAILY_UPDATE_GROUPS 也是"假纳入"（到这里就被跳过、且不报错）。
        #   2026-09-22: 再放开 by_code（仅 daily_full_refresh 标记的，见函数 docstring）。
        #   其余模式（once/paged/by_param/by_period/by_month）确实不该日更，仍跳过。
        _mode = conf.get("mode")
        if not engine_supports_daily(conf):
            print(f"  {name:16s}  [跳过] 非日更模式({_mode})")
            continue

        # ── by_code + daily_full_refresh → **每天全量重拉** ──
        #   用途: index_global（22 个国际指数）。
        #     该接口**只能按标的一次拉全历史**（不传日期参数，2016 起各约 2700 行，
        #     在 4000 硬顶内不截断），故"日更"的正确形态就是**全量重拉**——
        #     落盘按 (ts_code, trade_date) 去重，天然幂等，无需缺口计算。
        #   ⚠ 成本: 22 请求/天；该接口双重限频 10次/分 + **100次/天**，
        #     占配额 22%（尚安全）。**切勿给它加复核窗口**（那会变 66 请求、
        #     占配额 66% 接近危险区）—— 全量重拉本身就是最彻底的复核。
        #   ⚠ 依赖 run_target 的 `ignore_checkpoint=True`（本模块已传）
        #     → pending = 全部标的，不会被"该标的已在库"跳过。
        if _mode == "by_code":
            print(f"  {name:16s} {'全量重拉':>10s} {0:>5d} {0:>5d}  "
                  f"按标的逐个拉全历史（by_code，幂等）")
            plan.append((name, conf, None, {"latest": "-", "gap": [],
                                            "refresh": [], "full_refresh": True}))
            continue
        try:
            dates, info = resolve_gaps(name, conf, target_date, lookback)
        except Exception as e:
            print(f"  {name:16s}  [错误] {type(e).__name__}: {str(e)[:50]}")
            continue

        latest = info["latest"] or "无数据"
        if not dates:
            print(f"  {name:16s} {latest:>10s} {0:>5d} {0:>5d}  已是最新")
            continue
        # ⚠ 兼容两类任务形态（2026-09-21）:
        #   by_date  → dates = ["20260919", "20260920", ...]  逐日
        #   by_range → dates = [("20260916", "20260921")]     区间段
        if isinstance(dates[0], tuple):
            _s, _e = dates[0]
            show = f"{_s}~{_e}" + (f"(共{len(dates)}段)" if len(dates) > 1 else "")
        else:
            show = ",".join(d[-4:] for d in dates[:8]) + ("..." if len(dates) > 8 else "")
        print(f"  {name:16s} {latest:>10s} {len(info['gap']):>5d} "
              f"{len(info['refresh']):>5d}  {show}")
        plan.append((name, conf, dates, info))

    if not plan:
        print("\n  所有接口均已是最新，无需补全")
        return {"ok": 0, "fail": 0, "rows": 0}

    # ⚠ dates 可能为 None —— 表示"全量重拉"类（by_code + daily_full_refresh），
    #   任务集不在 Step 1 确定，而由 run_target 内 build_tasks 现算（见上）。
    total_calls = sum(len(d) if d else 0 for _, _, d, _ in plan)
    n_full = sum(1 for _, _, d, _ in plan if d is None)
    print("-" * 78)
    print(f"  合计: {len(plan)} 个接口 | {total_calls} 个接口-交易日"
          + (f" | {n_full} 个全量重拉（任务数运行期确定）" if n_full else ""))

    if dry_run:
        print("\n  [DRY-RUN] 只统计不写入")
        return {"ok": 0, "fail": 0, "rows": 0}

    # ── Step 2: 逐表执行 ──
    print("\n[Step 2] 开始拉取")
    pro = get_pro()
    print("[OK] Tushare Pro API 已连接")
    cp = Checkpoint(MARKET_DATA_DIR, filename=".backfill_checkpoint.json")

    results = []
    t_all = time.time()
    try:
        for name, conf, dates, info in plan:
            try:
                r = run_target(pro, name, conf, cp, workers, dry_run=False,
                               verbose=verbose, date_override=dates,
                               ignore_checkpoint=True)
                if r:
                    results.append((name, r))
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"  [ERROR] {name}: {type(e).__name__}: {str(e)[:100]}")
                results.append((name, {"done": 0, "est": len(dates)}))
    except KeyboardInterrupt:
        print("\n  [中断] 断点已保存，下次可续跑")
    finally:
        cp.save()

    # ── Step 3: market_state 派生（依赖 stk_limit + daily）──
    if with_market_state:
        core_done = {n for n, _ in results}
        if "stk_limit" in core_done and "daily" in core_done:
            print("\n[Step 3] market_state 跌停状态派生")
            try:
                from fetch.redtide_supply import run_market_state
                run_market_state(years=[int(target_date[:4])])
            except Exception as e:
                print(f"  [WARN] market_state 派生失败: {str(e)[:80]}")
        else:
            print("\n[Step 3] 跳过 market_state（stk_limit/daily 未完成）")

    # ── 汇总 ──
    print("\n" + "=" * 78)
    print("  每日增量更新完成 — 汇总")
    print("=" * 78)
    ok_n = fail_n = 0
    for name, conf, dates, info in plan:
        done = next((r["done"] for n, r in results if n == name), None)
        if done is None:
            fail_n += 1
            print(f"  [✗] {name:16s} 未完成")
        else:
            ok_n += 1
            # dates=None 表示全量重拉类（任务数在运行期才确定），不显示"目标N天"
            scope = "全量重拉" if dates is None else f"目标{len(dates):>3d}天"
            print(f"  [✓] {name:16s} {scope}  成功{done:>3d}")
    print(f"\n  成功={ok_n} 失败={fail_n} | 总耗时={(time.time()-t_all)/60:.1f} 分钟")
    print(f"  断点文件: {cp.path}")

    # ── Step 4: 权限漏网告警（2026-09-20 新增）──
    #   为什么放在最后：日更期间所有接口都跑完了，才能看到全貌。
    #   为什么必须单独告警：无权限错误与"网络故障/代码bug"在体检里
    #   长得一样（都表现为"数据落后 N 天"），不单独识别就分不出原因。
    _report_permission_denied(dry_run=dry_run)

    return {"ok": ok_n, "fail": fail_n, "rows": 0}


def _report_permission_denied(dry_run=False):
    """日更结束时汇总并告警「权限漏网被收紧」。

    价值：把"某天突然发现策略数据断了一个月"变成"收紧当天就收到告警"。

    ⚠ 本函数不做任何数据写入，只读 fetch.base 的权限拒绝记录。
    """
    try:
        from fetch.base import (permission_denied_records,
                                permission_denied_apis,
                                permission_denied_count)
    except Exception:
        return

    n = permission_denied_count()
    if n <= 0:
        return

    try:
        from config import LEAKY_PERMISSIONS
    except Exception:
        LEAKY_PERMISSIONS = {}

    apis = permission_denied_apis()
    leaked = [a for a in apis if a in LEAKY_PERMISSIONS]
    other = [a for a in apis if a not in LEAKY_PERMISSIONS]

    print("\n" + "!" * 78)
    print("  ★★★ 权限告警：检测到「无权限」错误 ★★★")
    print("!" * 78)
    print(f"  被拒接口 {len(apis)} 个 / 拒绝次数 {n} 次")

    lines = ["## ★ 权限告警：无权限错误", ""]

    if leaked:
        print(f"\n  ⚠ 其中 {len(leaked)} 个是**登记的权限漏网接口** ——")
        print("     很可能是漏网已被平台收紧（不是网络故障）。")
        lines.append(f"**{len(leaked)} 个漏网接口被拒** —— 疑似漏网被收紧：")
        lines.append("")
        for a in leaked:
            info = LEAKY_PERMISSIONS[a]
            print(f"\n     【{a}】官方门槛 {info['claimed']}（{info['kind']}）")
            print(f"       下游用途: {info['use']}")
            print(f"       替代路径: {info['alt']}")
            if info.get("monitor") is False:
                print("       （该表未纳入告警订阅：无长期下游依赖）")
            lines.append(f"- **{a}**（官方门槛 {info['claimed']}）")
            lines.append(f"  - 用途：{info['use']}")
            lines.append(f"  - 替代：{info['alt']}")

    if other:
        print(f"\n  ℹ 另有 {len(other)} 个未登记为漏网的接口被拒: {other}")
        print("     （若确认这些表不在预期内，属正常；否则排查权限档位）")
        lines += ["", f"另有未登记接口被拒：{', '.join(other)}"]

    print("\n  建议动作：")
    print("    ① 若确认漏网收紧 → 启用上表替代路径，或评估升级积分/购买独立权限")
    print("    ② 不要重复重试（权限错误已被引擎识别为永久性，不会自动重试）")
    print("    ③ 查权限明细: https://tushare.pro/weborder/#/permission")
    print("!" * 78)

    if dry_run:
        return

    # ── PushPlus 推送（失败不影响主流程）──
    try:
        from config import PUSHPLUS_TOKEN, PUSHPLUS_CHANNEL
        if PUSHPLUS_TOKEN and len(PUSHPLUS_TOKEN) > 10:
            from push.pushplus import PushPlus
            pp = PushPlus(token=PUSHPLUS_TOKEN, channel=PUSHPLUS_CHANNEL,
                          template="markdown")
            pp.send_markdown("\n".join(lines),
                             title="★ 数据权限告警（漏网可能已收紧）")
            print("  [OK] 权限告警已推送")
    except Exception as e:
        print(f"  [WARN] 权限告警推送失败（不影响主流程）: {str(e)[:80]}")


# ============================================================
# CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser(description="每日增量更新（复用 backfill 引擎）")
    p.add_argument("--date", default=None, help="目标交易日 YYYYMMDD（默认最近交易日）")
    p.add_argument("--group", default="core",
                   help="接口组: core / extend / all（默认 core）")
    p.add_argument("--lookback", type=int, default=None,
                   help=f"复核窗口交易日数（默认 {DAILY_UPDATE_LOOKBACK}）")
    p.add_argument("--only", default=None, help="只跑指定表, 逗号分隔")
    p.add_argument("--workers", type=int, default=None, help="每接口并发线程数")
    p.add_argument("--dry-run", action="store_true", help="只算缺口, 不发请求")
    p.add_argument("--verbose", action="store_true", help="打印重试日志")
    p.add_argument("--no-market-state", action="store_true",
                   help="不重算 market_state 派生表")
    p.add_argument("--list", action="store_true", help="列出接口清单后退出")
    a = p.parse_args(argv)

    if a.list:
        print(f"{'接口':18s} {'说明':20s} {'组':10s} 定义来源")
        print("-" * 70)
        for gname, members in DAILY_UPDATE_GROUPS.items():
            for n in members:
                conf = get_target_conf(n)
                src = "DAILY_UPDATE_EXTRA" if n in DAILY_UPDATE_EXTRA else "BACKFILL_TARGETS"
                print(f"{n:18s} {conf.get('desc','') if conf else '?':20s} "
                      f"{gname:10s} {src}")
        return

    groups = tuple(x.strip() for x in a.group.split(",") if x.strip())
    only = [x.strip() for x in a.only.split(",") if x.strip()] if a.only else None
    run_daily_update(target_date=a.date, dry_run=a.dry_run, groups=groups,
                     lookback=a.lookback, workers=a.workers, only=only,
                     verbose=a.verbose,
                     with_market_state=not a.no_market_state)


if __name__ == "__main__":
    main()
