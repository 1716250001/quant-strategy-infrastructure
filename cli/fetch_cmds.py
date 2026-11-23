# -*- coding: utf-8 -*-
"""cli.fetch_cmds — 数据拉取类命令（W7b 自 main.py 原样搬出）。

退出码契约（D2）：0=成功；1=受控业务失败。底层可返回失败计数时透传为 1，
异常同样记为 1；不得把失败吞成 0。
"""

import sys

from cli._shared import _banner, _today


def _failed_count(result):
    """从底层返回值提取失败计数：dict 取 fail/failed，元组/列表取第 3 位。"""
    if isinstance(result, dict):
        return int(result.get("fail") or result.get("failed") or 0)
    if isinstance(result, (tuple, list)) and len(result) >= 3:
        return int(result[2] or 0)
    return 0


def _fail(message):
    print(f"  [ERROR] {message}", file=sys.stderr)
    return 1


def cmd_gap_update(args):
    """差额补全（数据库最新日 → 今天）—— 内部已改走 backfill 引擎"""
    from fetch.daily_update import run_daily_update

    _banner("gap-update 每日增量更新")
    target_date = args.date.replace("-", "") if args.date else None
    groups = tuple(x.strip() for x in args.group.split(",") if x.strip())
    # C-P3-2（G3）：--table 是 --only 的兼容别名；两者同给不同值 ⇒ rc=2。
    only = getattr(args, "only", None)
    table = getattr(args, "table", None)
    if only and table and only != table:
        print("  [ERROR] --only 与 --table 取值不同，拒绝执行（只给一个即可）", file=sys.stderr)
        return 2
    only = only or table
    result = run_daily_update(
        target_date=target_date,
        dry_run=args.dry_run,
        groups=groups,
        lookback=getattr(args, "lookback", None),
        only=only,
    )
    failed = _failed_count(result)
    if failed:
        return _fail(f"gap-update 失败表数={failed}")
    return 0


def cmd_intraday_update(args):
    """尾盘增量更新（= gap-update --lookback 1 的特例；保留兼容入口，语义见 DB-G2）"""
    from fetch.daily_update import run_daily_update

    _banner("intraday-update 尾盘增量更新")
    target_date = args.date.replace("-", "") if args.date else _today().replace("-", "")
    result = run_daily_update(
        target_date=target_date, dry_run=args.dry_run, groups=("core",), lookback=1
    )
    failed = _failed_count(result)
    if failed:
        return _fail(f"intraday-update 失败表数={failed}")
    return 0


def cmd_redtide_supply(args):
    """赤潮数据补全（adj_factor/stk_limit/market_state）"""
    from fetch.redtide_supply import run_adj_factor, run_market_state, run_stk_limit

    _banner("redtide-supply 赤潮数据补全")
    if args.dry_run:
        print("  [DRY-RUN] 只统计不写入")
    failed = 0
    for label, step in (
        ("adj_factor", run_adj_factor),
        ("stk_limit", run_stk_limit),
        ("market_state", run_market_state),
    ):
        try:
            rc = step(dry_run=args.dry_run)
            # D4/U6：内层 run_daily_update 的失败表数经返回值上抛
            if isinstance(rc, int) and rc > 0:
                failed += rc
                print(f"  [ERROR] {label} 失败表数={rc}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"  [ERROR] {label} 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failed else 0


def cmd_fund_nav(args):
    """基金净值增量更新 / 历史回补"""
    from fetch.fund_nav_update import repair_incomplete, run_fund_nav_update

    _banner("fund-nav 基金净值增量更新")

    if args.repair:
        print("  [模式] 历史回补（对行数过少的文件按代码补拉全历史）")
        result = repair_incomplete(
            threshold=args.repair_threshold,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    else:
        end_date = args.date.replace("-", "") if args.date else None
        result = run_fund_nav_update(
            dry_run=args.dry_run,
            lookback_days=args.days,
            end_date=end_date,
        )
    failed = _failed_count(result)
    if failed:
        return _fail(f"fund-nav 失败数={failed}")
    return 0


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
        return run(
            freqs,
            only,
            dry_run=(not args.apply) or bool(args.dry_run),
            workers=args.workers,
            limit=args.limit,
            refresh=args.refresh,
            exclude=excl,
            budget=args.budget,
        )


def cmd_download_full(args):
    """全市场数据下载（建库/重建）"""
    from fetch.full_download import run_download

    _banner("download-full 全市场数据下载")
    try:
        result = run_download(
            phase=args.phase,
            reset=args.reset,
            dry_run=args.dry_run,
            workers=getattr(args, "workers", None),
        )
    except Exception as exc:
        return _fail(f"download-full 异常: {type(exc).__name__}: {exc}")
    failed = _failed_count(result)
    if failed:
        return _fail(f"download-full 失败数={failed}")
    return 0


def cmd_download_cb(args):
    """可转债数据下载"""
    from fetch.cb_download import run_cb_download

    _banner("download-cb 可转债数据下载")
    try:
        result = run_cb_download(
            reset=args.reset, dry_run=args.dry_run, workers=getattr(args, "workers", None)
        )
    except Exception as exc:
        return _fail(f"download-cb 异常: {type(exc).__name__}: {exc}")
    failed = _failed_count(result)
    if failed:
        return _fail(f"download-cb 失败数={failed}")
    return 0


def cmd_backfill(args):
    """通用补数（有权限但未入库的接口）"""
    from fetch.backfill import list_targets, run_backfill

    if getattr(args, "list_targets", False):
        list_targets()
        return 0
    _banner("backfill 通用补数")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    tiers = tuple(t.strip() for t in args.tier.split(",") if t.strip())
    try:
        result = run_backfill(
            tiers=tiers,
            only=only,
            dry_run=args.dry_run,
            workers=getattr(args, "workers", None),
            parallel_targets=getattr(args, "parallel_targets", None),
            limit=getattr(args, "limit", None),
            force=getattr(args, "force", False),
            codes_file=getattr(args, "codes_file", None),
            force_listed=getattr(args, "force_listed", False),
        )
    except Exception as exc:
        return _fail(f"backfill 异常: {type(exc).__name__}: {exc}")
    failed = _failed_count(result)
    if failed:
        return _fail(f"backfill 失败数={failed}")
    return 0
