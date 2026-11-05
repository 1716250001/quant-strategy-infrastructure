# -*- coding: utf-8 -*-
"""cli.research_cmds — 研究与指数/基金命令（W7b 自 main.py 原样搬出）。"""



from cli._shared import _banner


def cmd_micro_index(args):
    """本地自建微盘指数"""
    from tools.research.build_micro_index import main as micro_main
    _banner("micro-index 本地自建微盘指数")
    argv = []
    if args.start:
        argv += ["--start", args.start]
    if args.out_dir:
        argv += ["--out-dir", args.out_dir]
    if args.exclude_st:
        argv.append("--exclude-st")
    micro_main(argv)

def cmd_factor_lib(args):
    """因子库概览（全收益基准 + 风格因子）"""
    from tools.research.factor_library import main as factor_main
    _banner("factor-lib 因子库")
    argv = []
    if args.no_style:
        argv.append("--no-style")
    if args.no_matrix:
        argv.append("--no-matrix")
    factor_main(argv)

def cmd_fund_pool(args):
    """公募主动权益基金池构建"""
    from tools.research.fund_pool_builder import main as pool_main
    _banner("fund-pool 基金池构建")
    argv = []
    if args.out:
        argv += ["--out", args.out]
    pool_main(argv)

def cmd_manager_profile(args):
    """基金经理风格画像（风格/跟踪指数/β）"""
    from tools.research.manager_profile import main as mp_main
    _banner("manager-profile 基金经理风格画像")
    if not args.code and not args.manager:
        print("  需指定 --code <基金代码> 或 --manager <经理姓名>")
        print("  例: python main.py manager-profile --code 004685")
        return
    argv = []
    if args.code:
        argv += ["--code", args.code]
    if args.manager:
        argv += ["--manager", args.manager]
    if args.topn is not None:
        argv += ["--topn", str(args.topn)]
    if args.all:
        argv.append("--all")
    if args.min_days is not None:
        argv += ["--min-days", str(args.min_days)]
    if args.json:
        argv.append("--json")
    mp_main(argv)

def cmd_leverage(args):
    """杠杆风险监控（两融B口径，零请求读本地库）"""
    from tools.research.leverage_monitor import main as lm_main
    _banner("leverage 杠杆风险监控")
    lm_main([args.date] if args.date else [])

def cmd_etf_flow(args):
    """ETF份额/申赎资金流"""
    from tools.research.etf_flow import main as ef_main
    _banner("etf-flow ETF份额/申赎资金流")
    ef_main([args.date] if args.date else [])
