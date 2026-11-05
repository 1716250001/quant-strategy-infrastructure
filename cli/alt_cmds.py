# -*- coding: utf-8 -*-
"""cli.alt_cmds — alt 库命令（W7b 自 main.py 原样搬出）。"""



from cli._shared import _banner


def _alt_tier_choices():
    """动态取 alt tier 可选值（守住单一真源 alt.spec.TIERS）。

    导入失败则返回 None（argparse 语义 = 不限制取值），
    由 alt 模块自身的 choices 做最终校验，不会静默放行非法值。
    绝不在本文件硬编码 ["P0","P1","P3","P4"] —— 新增 tier 时会漂移。
    """
    try:
        from alt.spec import TIERS
        return list(TIERS)
    except Exception:
        return None

def cmd_alt_update(args):
    """alt 库每日增量更新（P0~P4 分级 + 新鲜度自检）"""
    from alt.update import main as alt_main
    _banner("alt-update alt 库每日增量更新")
    argv = []
    if args.tier:
        argv += ["--tier", args.tier]
    if args.only:
        argv += ["--only", args.only]
    if args.dry_run:
        argv.append("--dry-run")
    if args.lookback is not None:
        argv += ["--lookback", str(args.lookback)]
    if args.force:
        argv.append("--force")
    if args.status:
        argv.append("--status")
    if args.list:
        argv.append("--list")
    alt_main(argv)

def cmd_alt_audit(args):
    """alt 库数据体检（断点/重复/缺口/规模/隔离）"""
    from alt.audit import main as alt_main
    _banner("alt-audit alt 库数据体检")
    argv = []
    for flag, on in (("--checkpoint", args.checkpoint),
                     ("--date-sets", args.date_sets),
                     ("--dupes", args.dupes),
                     ("--gaps", args.gaps),
                     ("--scale", args.scale),
                     ("--isolation", args.isolation),
                     ("--snapshot", args.snapshot),
                     ("--json", args.json),
                     ("--fix-dupes", args.fix_dupes),
                     ("--confirm-gaps", args.confirm_gaps),
                     ("--apply", args.apply)):
        if on:
            argv.append(flag)
    if args.only:
        argv += ["--only", args.only]
    if args.report:
        argv.append("--report")
        if isinstance(args.report, str):
            argv.append(args.report)
    alt_main(argv)

def cmd_alt_backfill(args):
    """alt 库历史回补（akshare 备用源）"""
    from alt.backfill import main as alt_main
    _banner("alt-backfill alt 库历史回补")
    argv = []
    if args.tier:
        argv += ["--tier", args.tier]
    if args.only:
        argv += ["--only", args.only]
    if args.dry_run:
        argv.append("--dry-run")
    if args.force:
        argv.append("--force")
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    if args.date:
        argv += ["--date", args.date]
    if args.list:
        argv.append("--list")
    alt_main(argv)

def cmd_alt_us(args):
    """美股建库（alt 库 mode=list 专用入口）"""
    from alt.us_backfill import main as alt_main
    _banner("alt-us 美股建库")
    argv = []
    if args.universe:
        argv.append("--universe")
    if args.universe_from_cache:
        argv += ["--universe-from-cache", args.universe_from_cache]
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    if args.rebuild:
        argv.append("--rebuild")
    if args.workers is not None:
        argv += ["--workers", str(args.workers)]
    if args.delay is not None:
        argv += ["--delay", str(args.delay)]
    if args.status:
        argv.append("--status")
    if args.prune_ckpt:
        argv.append("--prune-ckpt")
    if args.yes:
        argv.append("--yes")
    alt_main(argv)

def cmd_alt_verify(args):
    """alt 表规格校验（只读）"""
    from alt.verify_spec import main as alt_main
    _banner("alt-verify alt 表规格校验")
    argv = []
    if args.tier:
        argv += ["--tier", args.tier]
    if args.only:
        argv += ["--only", args.only]
    if args.full_enum:
        argv.append("--full-enum")
    if args.list_todo:
        argv.append("--list-todo")
    alt_main(argv)
