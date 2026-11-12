# -*- coding: utf-8 -*-
"""cli.fetch_cmds — 数据拉取类命令（W7b 自 main.py 原样搬出）。"""



from cli._shared import _banner, _today


def cmd_gap_update(args):
    """差额补全（数据库最新日 → 今天）—— 内部已改走 backfill 引擎"""
    from fetch.daily_update import run_daily_update
    _banner("gap-update 每日增量更新")
    target_date = args.date.replace("-", "") if args.date else None
    groups = tuple(x.strip() for x in args.group.split(",") if x.strip())
    run_daily_update(target_date=target_date, dry_run=args.dry_run, groups=groups,
                     lookback=getattr(args, "lookback", None),
                     only=getattr(args, "only", None))

def cmd_intraday_update(args):
    """尾盘增量更新（= gap-update --lookback 1 的特例；保留兼容入口，语义见 DB-G2）"""
    from fetch.daily_update import run_daily_update
    _banner("intraday-update 尾盘增量更新")
    target_date = args.date.replace("-", "") if args.date else _today().replace("-", "")
    run_daily_update(target_date=target_date, dry_run=args.dry_run,
                     groups=("core",), lookback=1)

def cmd_redtide_supply(args):
    """赤潮数据补全（adj_factor/stk_limit/market_state）"""
    from fetch.redtide_supply import run_adj_factor, run_market_state, run_stk_limit
    _banner("redtide-supply 赤潮数据补全")
    if args.dry_run:
        print("  [DRY-RUN] 只统计不写入")
    run_adj_factor(dry_run=args.dry_run)
    run_stk_limit(dry_run=args.dry_run)
    run_market_state(dry_run=args.dry_run)

def cmd_fund_nav(args):
    """基金净值增量更新 / 历史回补"""
    from fetch.fund_nav_update import repair_incomplete, run_fund_nav_update
    _banner("fund-nav 基金净值增量更新")

    if args.repair:
        print("  [模式] 历史回补（对行数过少的文件按代码补拉全历史）")
        repair_incomplete(
            threshold=args.repair_threshold,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        return

    end_date = args.date.replace("-", "") if args.date else None
    run_fund_nav_update(
        dry_run=args.dry_run,
        lookback_days=args.days,
        end_date=end_date,
    )

def cmd_schedule(args):
    """周期调度执行器（默认干跑；--apply 才实跑）。

    ⭐ P1-1（§435）：薄代理 —— 参数语义、裸 `--refresh` 拒绝、实例锁全部留在
    `fetch/schedule_runner.py`，本函数只做 argparse→run() 的转接，不复制任何判断。
    """
    from fetch.schedule_runner import _InstanceLock, run
    _banner("schedule 周期调度执行器")
    freqs = [x.strip().upper() for x in args.freq.split(",")] if args.freq else None
    only = [x.strip() for x in args.only.split(",")] if args.only else None
    excl = [x.strip() for x in args.exclude.split(",")] if args.exclude else None
    with _InstanceLock():
        return run(freqs, only,
                   dry_run=(not args.apply) or bool(args.dry_run),
                   workers=args.workers, limit=args.limit, refresh=args.refresh,
                   exclude=excl, budget=args.budget)


def cmd_download_full(args):
    """全市场数据下载（建库/重建）"""
    from fetch.full_download import run_download
    _banner("download-full 全市场数据下载")
    run_download(phase=args.phase, reset=args.reset, dry_run=args.dry_run,
                 workers=getattr(args, "workers", None))

def cmd_download_cb(args):
    """可转债数据下载"""
    from fetch.cb_download import run_cb_download
    _banner("download-cb 可转债数据下载")
    run_cb_download(reset=args.reset, dry_run=args.dry_run,
                    workers=getattr(args, "workers", None))

def cmd_backfill(args):
    """通用补数（有权限但未入库的接口）"""
    from fetch.backfill import list_targets, run_backfill
    if getattr(args, "list_targets", False):
        list_targets()
        return
    _banner("backfill 通用补数")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    tiers = tuple(t.strip() for t in args.tier.split(",") if t.strip())
    run_backfill(tiers=tiers, only=only, dry_run=args.dry_run,
                 workers=getattr(args, "workers", None),
                 parallel_targets=getattr(args, "parallel_targets", None),
                 limit=getattr(args, "limit", None),
                 force=getattr(args, "force", False),
                 codes_file=getattr(args, "codes_file", None),
                 force_listed=getattr(args, "force_listed", False))
