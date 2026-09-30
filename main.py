# -*- coding: utf-8 -*-
"""
main.py — A股三层轮动金字塔策略 · 统一CLI入口
=============================================
v5.1 子命令注册表：每个子命令独立声明参数与实现，
替代原先的 if 链 + 全局参数混用，新增命令只需注册一个函数。

【日常流水线】
  python main.py fetch              # 数据采集（大盘β+宽基ETF+杠杆+IV）
  python main.py report             # 生成HTML日报
  python main.py all                # 采集 + 日报（一键完成）
  python main.py fix-resonance      # 补算四因子 + ETF份额 + 多层共振
  python main.py daily-report       # 量化日报（大盘分析 + 宽基ETF持仓调仓）

【数据维护】
  python main.py gap-update         # 差额补全（数据库最新日→今天）
  python main.py intraday-update    # 尾盘增量更新（只拉今天）
  python main.py redtide-supply     # 赤潮数据补全（adj_factor/stk_limit/market_state）
  python main.py fund-nav           # 基金净值增量更新（回溯N个交易日）
  python main.py download-full      # 全市场数据下载（建库/重建，断点续跑）
  python main.py download-cb        # 可转债数据下载
  python main.py backfill           # 通用补数（有权限但未入库的接口）
  python main.py check-coverage     # 数据覆盖体检（可转债 + ETF/LOF）
  python main.py check-consumer     # 决策侧消费标的覆盖体检（A2池+作战包+持仓，逐标的验行数）

【库维护与体检】
  python main.py db-clean           # 存量数据清理（版本冗余压缩，默认干跑 + 自动备份）
  python main.py db-audit           # 全量数据库体检（断点/重复/缺口/规模）
  python main.py db-schema          # 结构扫描（输出机器可读 schema JSON）
  python main.py db-report          # 从 schema JSON 生成结构报告 Markdown
  python main.py db-migrate         # 存储布局迁移（by_code → by_year）
  python main.py ckpt               # 断点管理（查看/交叉核对/回填/清虚记）
  python main.py config-check       # config.py 完整性检查（主键/日期列/清单覆盖）
  python main.py regress            # 回归测试（盘后流水线 + 存储布局约束）

【数据速查】
  python main.py freshness          # 数据新鲜度速查（各表最新日期）
  python main.py peek               # 数据预览（看某张表的实际数据）
  python main.py doctor             # 环境自检（依赖/路径/磁盘/断点/凭据）

【alt 库（另类/海外数据，物理隔离于主库）】
  python main.py alt-update         # alt 库每日增量更新（P0~P4 分级 + 新鲜度自检）
  python main.py alt-audit          # alt 库体检（断点/重复/缺口/规模/隔离）
  python main.py alt-backfill       # alt 库历史回补（akshare 备用源）
  python main.py alt-us             # 美股建库（mode=list 专用入口）
  python main.py alt-verify         # alt 表规格校验（只读）

【基金与指数分析】
  python main.py wind-index         # 【已剥离停用 2026-09-25】Wind 研究暂停，代码暂存 _scratch/wind-研究暂存
  python main.py micro-index        # 本地自建微盘指数（替代 Wind 8841431.WI）
  python main.py factor-lib         # 因子库概览（全收益基准 + 风格因子）
  python main.py fund-pool          # 公募主动权益基金池构建
  python main.py manager-profile    # 基金经理风格画像（风格/跟踪指数/β）

【分析扫描】
  python main.py qidian             # 奇点战法每日双重信号扫描
  python main.py scan-divergence    # [已归档 2026-09-24] 背离扫描（运行显示停用提示）
  python main.py divergence-trigger # [已归档 2026-09-24] 背离触发（运行显示停用提示）

【监控推送】
  python main.py monitor            # [已归档 2026-09-25] 盘中背离监控（运行显示停用提示）
  python main.py push-test          # PushPlus推送测试/发送

【工具】
  python main.py lof-list           # 场内ETF/LOF清单统计
  python main.py lof-export         # 场内LOF导出Excel

本清单与 build_parser() 的注册表保持一致（共 41 个命令）。
alt 库另有等价模块入口：python -m alt.update / alt.audit / alt.backfill
"""
import argparse
import datetime
import sys


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _banner(text):
    """打印与原有风格一致的命令行横幅：[tag] title"""
    tag, _, title = text.partition(" ")
    print("=" * 60)
    print(f"  [{tag}] {title}" if title else f"  [{tag}]")
    print("=" * 60)


# ============================================================
# 【日常流水线】
# ============================================================
def cmd_fetch(args):
    """数据采集 —— 已停用（2026-09-25 F4：旧宽基ETF轮动采集链整体归档）"""
    _banner("fetch 数据采集")
    print("  [停用] 旧宽基ETF轮动采集链（pipeline + market_beta/iv_fetch/wide_etf/leverage 采集版）")
    print("         已于 2026-09-25 归档: _archive/legacy链-20260925/")
    print("         数据新鲜度现由 A1 库日更接管（main.py gap-update --group all，工作日 17:30 automation）。")
    print("         杠杆读数入口保留: python -m tools.leverage_monitor")


def cmd_report(args):
    """日报生成 —— 已停用（2026-09-24 决策）"""
    _banner("report 日报生成")
    print("  [停用] 日报链路已于 2026-09-24 停用 —— "
          "它服务的「宽基ETF轮动 v3.0」与 v7.7 选股驱动范式不同源。")
    print("         代码归档: 代码/_archive/日报链路-20260924/")
    print("         保留能力: 杠杆风险监控 / 宽基ETF份额资金流（见归档 README 的接入待办）")
    return


def cmd_all(args):
    """采集 + 日报（一键完成）—— 两步均已停用"""
    _banner("all 采集 + 日报")
    print("  [停用] 第 1 步「数据采集」2026-09-25 归档（_archive/legacy链-20260925/，")
    print("         数据新鲜度由 A1 库日更接管）；第 2 步「日报生成」2026-09-24 停用。")
    print("         日常维护入口: main.py gap-update --group all")


def cmd_fix_resonance(args):
    """[已拆分归档 2026-09-25 D2] 原 fix_and_resonance 混合体"""
    _banner("fix-resonance 已拆分归档（2026-09-25 D2）")
    print("  保留部分已拆出为独立模块（活代码）：")
    print("    杠杆风险监控 → python -m tools.leverage_monitor [YYYYMMDD]")
    print("    ETF份额/申赎 → python -m tools.etf_flow [YYYYMMDD]")
    print("  β合成/仓位映射/多层共振等择时旧逻辑随原文件归档：")
    print("    _archive/日报链路-20260924/fix_and_resonance.py")


# ============================================================
# 【数据维护】
# ============================================================
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
    """尾盘增量更新（只关心最近一个交易日）"""
    from fetch.daily_update import run_daily_update
    _banner("intraday-update 尾盘增量更新")
    target_date = args.date.replace("-", "") if args.date else _today().replace("-", "")
    run_daily_update(target_date=target_date, dry_run=args.dry_run,
                     groups=("core",), lookback=1)


def cmd_redtide_supply(args):
    """赤潮数据补全（adj_factor/stk_limit/market_state）"""
    from fetch.redtide_supply import run_adj_factor, run_stk_limit, run_market_state
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
    from fetch.backfill import run_backfill, list_targets
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
                 force=getattr(args, "force", False))


def cmd_daily_report(args):
    """量化日报生成 —— 已停用（2026-09-24 决策）"""
    _banner("daily-report 量化日报生成")
    print("  [停用] 日报链路已于 2026-09-24 停用 —— "
          "它服务的「宽基ETF轮动 v3.0」与 v7.7 选股驱动范式不同源。")
    print("         代码归档: 代码/_archive/日报链路-20260924/")
    print("         保留能力: 杠杆风险监控 / 宽基ETF份额资金流（见归档 README 的接入待办）")
    return


def cmd_check_coverage(args):
    """数据覆盖体检（可转债 + ETF/LOF）"""
    from tools.check_coverage import run
    _banner("check-coverage 数据覆盖体检")
    run(args.date)


def cmd_check_consumer(args):
    """决策侧消费标的覆盖体检（511180/511380 教训的系统性防线）"""
    from tools.consumer_coverage import main as cc_main
    _banner("check-consumer 决策侧消费标的覆盖体检")
    cc_main()


def cmd_db_clean(args):
    """存量数据清理（版本冗余压缩）"""
    from tools.db_clean import run_clean, DB_CLEAN_TARGETS
    if args.list:
        print(f"{'目标':22s} {'说明':14s} {'主键':32s} 策略")
        for n, c in DB_CLEAN_TARGETS.items():
            print(f"{n:22s} {c.get('desc',''):14s} "
                  f"{str(c.get('key')):32s} {c.get('strategy','')}")
        return
    _banner("db-clean 存量数据清理")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    run_clean(targets=only, apply=args.apply, backup=not args.no_backup)


def cmd_db_audit(args):
    """全量数据库体检"""
    from tools.db_audit import run_audit, DB_AUDIT_PKEYS
    if args.list:
        print(f"{'目录':22s} 主键")
        for n, pk in DB_AUDIT_PKEYS.items():
            print(f"{n:22s} {pk if pk else '(跳过重复检查)'}")
        return
    _banner("db-audit 全量数据库体检")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    if args.section == "all":
        sections = ("checkpoint", "dup", "gap", "scale")
    else:
        sections = tuple(x.strip() for x in args.section.split(",") if x.strip())
    run_audit(sections=sections, only=only, exact=getattr(args, "exact", False))


def cmd_db_schema(args):
    """数据库结构扫描（输出机器可读 schema JSON）"""
    from tools.db_schema import run_schema
    _banner("db-schema 数据库结构扫描")
    run_schema(out=getattr(args, "out", None), verbose=not args.quiet, quiet=args.quiet)


def cmd_db_report(args):
    """数据库结构报告生成（从 db_schema.json 生成 Markdown）"""
    from tools.gen_db_report import run as gen_report
    _banner("db-report 数据库结构报告")
    gen_report(out=getattr(args, "out", None))


def cmd_regress(args):
    """回归测试（盘后流水线 + 存储布局约束）"""
    from tools.regress_pipeline import run as run_regress
    _banner("regress 回归测试")
    n_ok, n_fail = run_regress()
    if n_fail:
        raise SystemExit(1)


def cmd_freshness(args):
    """数据新鲜度速查"""
    from tools.quicklook import run_freshness
    _banner("freshness 数据新鲜度")
    tables = ([x.strip() for x in args.tables.split(",")]
              if getattr(args, "tables", None) else None)
    run_freshness(tables=tables, show_all=args.all, stale_days=args.stale)


def cmd_peek(args):
    """数据预览"""
    from tools.quicklook import run_peek
    _banner("peek 数据预览")
    run_peek(args.table, code=args.code, date=args.date,
             rows=args.rows, cols=args.cols)


def cmd_doctor(args):
    """环境自检"""
    from tools.doctor import run_doctor
    _banner("doctor 环境自检")
    r = run_doctor(network=args.network, as_json=args.json)
    if r.n_fail:
        raise SystemExit(1)


def cmd_ckpt(args):
    """断点管理（查看 / 交叉核对 / 按磁盘回填 / 清虚记）"""
    from tools.ckpt_tool import run_ckpt
    _banner("ckpt 断点管理")
    run_ckpt(show=args.show, fix=args.fix, clear=args.clear,
             table=args.table, dry_run=args.dry_run, execute=args.execute)


def cmd_db_migrate(args):
    """存储布局迁移（by_code → by_year）"""
    from tools.db_migrate import run_migrate, PENDING_TABLES, DAILY_TABLES
    if args.list:
        print(f"{'待迁移表':24s} 分组")
        for t in PENDING_TABLES:
            print(f"  {t:22s} {'daily' if t in DAILY_TABLES else 'periodic'}")
        return
    _banner("db-migrate 存储布局迁移")
    if args.all:
        tables = list(PENDING_TABLES)
    elif args.daily:
        tables = list(DAILY_TABLES)
    elif args.tables:
        tables = [x.strip() for x in args.tables.split(",") if x.strip()]
    else:
        tables = None
    run_migrate(tables=tables, dry_run=args.dry_run,
                keep_backup=not args.no_backup)


# ============================================================
# 【基金与指数分析】
# ============================================================
def cmd_wind_index(args):
    """Wind 自编指数枚举/采集（默认 DRY-RUN）—— 已剥离停用（2026-09-25 老大令：Wind 研究暂停）"""
    _banner("wind-index Wind 自编指数枚举")
    print("【已剥离停用】Wind 研究暂停（2026-09-25），代码已整体剥离至临时脚本区：")
    print("    _scratch\\wind-研究暂存\\wind_index_enum.py（含 wind_probe 9 脚本）")
    print("恢复方式：把该目录文件 copy 回 fetch\\ 后，还原 main.py 的 cmd_wind_index")
    print("（备份与恢复指引见 _scratch\\wind-研究暂存\\README.md）")


def cmd_micro_index(args):
    """本地自建微盘指数"""
    from tools.build_micro_index import main as micro_main
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
    from tools.factor_library import main as factor_main
    _banner("factor-lib 因子库")
    argv = []
    if args.no_style:
        argv.append("--no-style")
    if args.no_matrix:
        argv.append("--no-matrix")
    factor_main(argv)


def cmd_fund_pool(args):
    """公募主动权益基金池构建"""
    from tools.fund_pool_builder import main as pool_main
    _banner("fund-pool 基金池构建")
    argv = []
    if args.out:
        argv += ["--out", args.out]
    pool_main(argv)


def cmd_manager_profile(args):
    """基金经理风格画像（风格/跟踪指数/β）"""
    from tools.manager_profile import main as mp_main
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


# ============================================================
# 【分析扫描】
# ============================================================
def cmd_qidian(args):
    """奇点战法每日双重信号扫描"""
    from strategies.qidian_daily_scan import run_scan_pipeline
    _banner("qidian 奇点战法每日双重信号扫描")
    run_scan_pipeline(
        date=args.date,
        dry_run=args.dry_run,
        report=args.report,
        push_single_buy=args.push_single_buy,
        push_single_sell=args.push_single_sell,
    )


def cmd_scan_divergence(args):
    """[已归档 2026-09-24] 全市场MACD背离扫描——实证负增量，停止使用（见 _archive/背离扫描器-20260924/README.md）"""
    _banner("scan-divergence 已归档（2026-09-24 决议）")
    print("  背离扫描器经 133 信号日 / 34,945 样本回测为负增量（L2 低估值池内 -0.24%~-1.06%，t=-1.65~-3.41），")
    print("  与 v7.5+ 选股驱动范式不同源，已决议归档。报告: reports/背离扫描器有效性验证-20260924.html")
    print("  代码保留于 _archive/背离扫描器-20260924/；若要重启须换假设而非调参。")


def cmd_divergence_trigger(args):
    """[已归档 2026-09-24] 分钟级背离触发扫描——随背离扫描器一并归档"""
    _banner("divergence-trigger 已归档（2026-09-24 决议）")
    print("  与 scan-divergence 同链路同假设，一并归档。详见 _archive/背离扫描器-20260924/README.md")


# ============================================================
# 【监控推送】
# ============================================================
def cmd_monitor(args):
    """[已归档 2026-09-25 G6] 盘中MACD背离监控——背离假设已否决，随 D1 链路归档"""
    _banner("monitor 已归档（2026-09-25，随背离扫描器决议）")
    print("  盘中多周期MACD背离监控与 scan-divergence 同假设同链路（假设已实证否决）。")
    print("  代码归档于 _archive/背离扫描器-20260924/（convergence.py + intraday_monitor.py）。")
    print("  若要重启须换假设而非调参。")


def cmd_push_test(args):
    """PushPlus 推送测试 / 发送"""
    from push.pushplus import run_push
    _banner("push-test PushPlus推送")
    # 未提供 --content 时按测试消息发送
    run_push(
        token=args.token,
        channel=args.channel,
        template=args.template,
        title=args.title,
        content=args.content,
        test=args.test or not args.content,
    )


# ============================================================
# 【工具】
# ============================================================
def cmd_lof_list(args):
    """场内ETF/LOF清单统计"""
    from tools.list_lof import run
    _banner("lof-list 场内ETF/LOF清单统计")
    run()


def cmd_lof_export(args):
    """场内LOF导出Excel"""
    from tools.export_lof import run
    _banner("lof-export 场内LOF导出Excel")
    out_path = run(args.out)
    print(f"\n输出文件: {out_path}")


# ============================================================
# 【alt 库（另类/海外数据，物理隔离于主库）】
# ============================================================
# ⚠ 为什么需要这一组入口（2026-09-22 新增）:
#   alt 库是三大数据资产之一（68 表 / 597 MB），日常必须跑，但此前只能
#   `python -m alt.update` 调用 —— main.py 中完全没有 alt 的影子。后果:
#     ① 写操作开关（--force / --apply / --fix-dupes / --rebuild）散落在 5 个
#        独立 argparse 里，没有像主库那样的统一安全栅栏；
#     ② alt 有独立断点与独立限速（akshare 通道），误用后果与主库不同；
#     ③ 从 `main.py --help` 完全看不出 alt 库存在，发现成本高。
#   此处只做**参数转发**，不做业务逻辑；tier 可选值由 alt.spec.TIERS
#   动态派生（单一真源），避免在两个文件各写一份硬编码造成漂移。
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


def cmd_config_check(args):
    """config.py 完整性检查（与 doctor / regress 共用同一份校验逻辑）"""
    from tools.config_check import main as cc_main
    _banner("config-check config.py 完整性检查")
    # 返回非 0 = 检查未通过，须让退出码传播（脚本/CI 依赖此语义）
    sys.exit(cc_main())


# ============================================================
# 参数解析
# ============================================================
_EPILOG = """
示例:
  # 日常流水线
  python main.py fetch                          采集今天数据
  python main.py report                         生成最新HTML日报
  python main.py report --format brief          仅控制台摘要
  python main.py all                            数据采集+日报一键完成
  python main.py fix-resonance 20260914         补算四因子+共振
  python main.py daily-report                   量化日报（大盘+持仓调仓）

  # 数据维护
  python main.py gap-update                     每日增量更新（走 backfill 引擎）
  python main.py gap-update --dry-run           只算缺口，不发请求
  python main.py gap-update --group all         追加资金面/事件面接口
  python main.py gap-update --lookback 10       扩大复核窗口（修历史残缺）
  python main.py intraday-update                尾盘增量（只关心最近1个交易日）
  python main.py redtide-supply                 赤潮派生（market_state）
  python main.py fund-nav                       基金净值增量更新
  python main.py fund-nav --repair              历史回补（行数过少的文件）
  python main.py fund-nav --days 10 --limit 200 自定义回溯与限量
  python main.py download-full --phase meta     仅下载元数据
  python main.py download-full --reset          清除断点重新下载
  python main.py download-cb                    可转债数据下载
  python main.py check-coverage                 数据覆盖体检
  python main.py check-coverage 20260911        指定日期体检

  # 库维护
  python main.py db-audit                       全量数据库体检
  python main.py db-audit --section gap         只看缺口
  python main.py db-audit --list                列出体检主键配置
  python main.py db-schema                      结构扫描（输出 schema JSON）
  python main.py db-report                      生成结构报告 Markdown
  python main.py db-migrate --dry-run           布局迁移计划（by_code→by_year）
  python main.py db-migrate --all               执行迁移（自动备份+行数校验）
  python main.py db-clean                       存量清理（默认干跑）
  python main.py db-clean --apply --only fina_indicator   执行清理
  python main.py ckpt --show                    断点×磁盘交叉核对
  python main.py config-check                   配置完整性检查
  python main.py regress                        回归测试（改数据层后必跑）

  # 数据速查
  python main.py freshness --all                全部表最新日期
  python main.py freshness --stale 3            只看落后超过3个交易日的表
  python main.py peek daily --code 600519.SH    预览某张表的实际数据
  python main.py doctor                         环境自检

  # alt 库（另类/海外数据，物理隔离于主库；tier = P0/P1/P3/P4）
  python main.py alt-update --status            新鲜度体检（零请求）
  python main.py alt-update --dry-run           只输出计划，不发请求
  python main.py alt-update --tier P0           只跑 P0 核心表
  python main.py alt-audit --scale              规模统计
  python main.py alt-audit --gaps               覆盖缺口
  python main.py alt-backfill --list            列出规格概览
  python main.py alt-us --status                美股建库进度
  python main.py alt-verify --list-todo         列待校验表（零请求）

  # 分析扫描
  python main.py qidian --dry-run               奇点战法扫描（不推送）
  python main.py scan-divergence                [已归档] 运行显示停用提示（2026-09-24 决议）
  python main.py divergence-trigger --push      [已归档] 运行显示停用提示（2026-09-24 决议）

  # 监控推送
  python main.py monitor                        [已归档] 运行显示停用提示（2026-09-25 决议）
  python main.py push-test                      发送推送测试消息
  python main.py push-test --title T --content C  发送自定义内容

  # 工具
  python main.py lof-list                       场内ETF/LOF清单统计
  python main.py lof-export                     导出LOF清单Excel

全量下载也可直接调用模块（与子命令等价）:
  python -m fetch.full_download --phase all
  python -m fetch.cb_download --reset
"""


def _add_date(p, help_text="目标日期 YYYY-MM-DD (默认: 今天)"):
    p.add_argument("--date", default=None, help=help_text)


def _add_dry_run(p, help_text="只统计不写入"):
    p.add_argument("--dry-run", action="store_true", help=help_text)


def _add_format(p):
    p.add_argument("--format", default="html", choices=["html", "brief"],
                   help="日报输出格式 (html=完整网页日报, brief=仅控制台摘要)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="A股三层轮动金字塔策略 · 统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── 日常流水线 ──
    p = sub.add_parser("fetch", help="数据采集（大盘β+宽基ETF+杠杆+IV）")
    _add_date(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("report", help="生成HTML日报")
    _add_date(p)
    _add_format(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("all", help="采集 + 日报（一键完成）")
    _add_date(p)
    _add_format(p)
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("fix-resonance", help="补算四因子 + ETF份额 + 多层共振")
    p.add_argument("date", nargs="?", default=None, help="交易日 YYYYMMDD (默认今天)")
    p.set_defaults(func=cmd_fix_resonance)

    # ── 数据维护 ──
    p = sub.add_parser("gap-update", help="每日增量更新（数据库最新日→今天，走 backfill 引擎）")
    _add_date(p)
    _add_dry_run(p)
    p.add_argument("--group", default="core",
                   help="接口组: core（默认）/ extend / all（逗号可组合）")
    p.add_argument("--lookback", type=int, default=None,
                   help="复核窗口交易日数（默认3；用于修复断点已记但当日数据残缺的情况）")
    p.add_argument("--only", default=None, help="只跑指定表, 逗号分隔")
    p.set_defaults(func=cmd_gap_update)

    p = sub.add_parser("intraday-update", help="尾盘增量更新（只关心最近一个交易日）")
    _add_date(p)
    _add_dry_run(p)
    p.set_defaults(func=cmd_intraday_update)

    p = sub.add_parser("redtide-supply", help="赤潮数据补全")
    _add_dry_run(p)
    p.set_defaults(func=cmd_redtide_supply)

    p = sub.add_parser("fund-nav", help="基金净值增量更新（回溯最近N个交易日）")
    _add_date(p, help_text="回溯截止日 YYYYMMDD (默认最新)")
    _add_dry_run(p)
    p.add_argument("--days", type=int, default=None, help="回溯交易日数 (默认5)")
    p.add_argument("--repair", action="store_true",
                   help="历史回补模式: 对行数过少的文件按代码补拉全历史")
    p.add_argument("--repair-threshold", type=int, default=5,
                   help="回补阈值(行数)，默认5")
    p.add_argument("--limit", type=int, default=None,
                   help="限制处理数量 (调试/分批用)")
    p.set_defaults(func=cmd_fund_nav)

    p = sub.add_parser("download-full", help="全市场数据下载（建库/重建）")
    p.add_argument("--reset", action="store_true", help="清除断点, 从头开始")
    p.add_argument("--phase", type=str, default="all",
                   help="指定阶段: meta / daily / fina / all")
    p.add_argument("--workers", type=int, default=None,
                   help="并发线程数 (默认4, 传1=串行)")
    _add_dry_run(p, help_text="空跑模式, 只统计不下载")
    p.set_defaults(func=cmd_download_full)

    p = sub.add_parser("download-cb", help="可转债数据下载")
    p.add_argument("--reset", action="store_true", help="清除断点, 从头开始")
    p.add_argument("--workers", type=int, default=None,
                   help="并发线程数 (默认4, 传1=串行)")
    _add_dry_run(p, help_text="空跑, 只统计不下载")
    p.set_defaults(func=cmd_download_cb)

    p = sub.add_parser("backfill", help="通用补数（有权限但未入库的接口）")
    p.add_argument("--tier", default="A,B", help="分档: A/B/C/D, 逗号分隔 (默认 A,B)")
    p.add_argument("--only", default=None, help="只跑指定接口, 逗号分隔")
    p.add_argument("--workers", type=int, default=None, help="每接口并发线程数 (默认4)")
    p.add_argument("--parallel-targets", type=int, default=None,
                   help="同时跑几个接口 (默认取 config.BACKFILL_PARALLEL_TARGETS=4; "
                        "传 1 则退化为串行)")
    p.add_argument("--limit", type=int, default=None, help="每接口最多跑N个任务")
    p.add_argument("--list", action="store_true", dest="list_targets",
                   help="列出全部补数目标（接口/档位/模式/布局/完成度）后退出")
    p.add_argument("--force", action="store_true",
                   help="忽略断点与'库内已有标的'，全部重拉"
                        "（修复'断点说完成但数据不完整'，如 index_dailybasic 早期截断）")
    _add_dry_run(p, help_text="只估算请求数, 不发请求")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("check-coverage", help="数据覆盖体检（可转债 + ETF/LOF）")
    p.add_argument("date", nargs="?", default=None,
                   help="检查日期 YYYYMMDD (默认: 自动获取最近交易日)")
    p.set_defaults(func=cmd_check_coverage)

    p = sub.add_parser("check-consumer", help="决策侧消费标的覆盖体检（A2池+作战包+持仓）")
    p.set_defaults(func=cmd_check_consumer)

    p = sub.add_parser("daily-report",
                       help="量化日报生成（大盘分析 + 宽基ETF持仓调仓）")
    p.add_argument("date", nargs="?", default=None,
                   help="交易日 YYYYMMDD (默认: 最新)")
    p.set_defaults(func=cmd_daily_report)

    p = sub.add_parser("db-clean", help="存量数据清理（版本冗余压缩）")
    p.add_argument("--only", default=None, help="只清理指定目标, 逗号分隔")
    p.add_argument("--apply", action="store_true",
                   help="真正写入(默认干跑); 执行时自动备份整个目录")
    p.add_argument("--no-backup", action="store_true", help="跳过自动备份(不推荐)")
    p.add_argument("--list", action="store_true", help="列出清理目标后退出")
    p.set_defaults(func=cmd_db_clean)

    p = sub.add_parser("db-audit", help="全量数据库体检（断点/重复/缺口/规模）")
    p.add_argument("--section", default="all",
                   help="检查项: all / checkpoint / dup / gap / scale (可逗号组合)")
    p.add_argument("--only", default=None, help="重复检查只针对指定目录, 逗号分隔")
    p.add_argument("--exact", action="store_true",
                   help="无主键目录做全量整行判重(慢, 约18分钟; 默认抽样)")
    p.add_argument("--list", action="store_true", help="列出主键配置后退出")
    p.set_defaults(func=cmd_db_audit)

    p = sub.add_parser("db-schema", help="数据库结构扫描（输出机器可读 schema JSON）")
    p.add_argument("--out", default=None, help="输出路径（默认 文档/数据与工程/db_schema.json）")
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印明细")
    p.set_defaults(func=cmd_db_schema)

    p = sub.add_parser("db-report", help="数据库结构报告（从 schema JSON 生成 Markdown）")
    p.add_argument("--out", default=None, help="输出路径（默认 文档/数据与工程/数据库结构报告_YYYYMMDD.md）")
    p.set_defaults(func=cmd_db_report)

    p = sub.add_parser("regress", help="回归测试（盘后流水线 + 存储布局约束）")
    p.set_defaults(func=cmd_regress)

    # ── 数据速查 ──
    p = sub.add_parser("freshness", help="数据新鲜度速查（各表最新日期）")
    p.add_argument("tables", nargs="?", default=None,
                   help="逗号分隔的表名（默认核心表）")
    p.add_argument("--all", action="store_true", help="显示全部表")
    p.add_argument("--stale", type=int, default=None,
                   help="只显示落后超过N个交易日的表")
    p.set_defaults(func=cmd_freshness)

    p = sub.add_parser("peek", help="数据预览（看某张表的实际数据）")
    p.add_argument("table", help="表名，如 daily")
    p.add_argument("--code", default=None, help="标的代码，如 600519.SH")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD")
    p.add_argument("--rows", type=int, default=10, help="显示行数（默认10）")
    p.add_argument("--cols", default=None, help="只显示指定列（逗号分隔）")
    p.set_defaults(func=cmd_peek)

    p = sub.add_parser("doctor", help="环境自检（依赖/路径/磁盘/断点/凭据）")
    p.add_argument("--network", action="store_true",
                   help="追加 tushare 连通性实测（消耗 1 次 API 调用）")
    p.add_argument("--json", action="store_true", dest="json",
                   help="输出 JSON")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("ckpt", help="断点管理（查看/交叉核对/回填/清虚记）")
    p.add_argument("--show", action="store_true", help="显示断点与磁盘交叉核对")
    p.add_argument("--fix", action="store_true",
                   help="按磁盘实际数据回填断点（幂等，只增不减）")
    p.add_argument("--clear", action="store_true",
                   help="清除'断点说完成但磁盘无数据'的虚记条目")
    p.add_argument("--table", default=None, help="指定表（逗号分隔）；默认全部")
    p.add_argument("--dry-run", action="store_true", help="只显示计划不写入")
    p.add_argument("--execute", action="store_true", help="真正写入（默认干跑）")
    p.set_defaults(func=cmd_ckpt)

    p = sub.add_parser("db-migrate", help="存储布局迁移（by_code → by_year）")
    p.add_argument("--tables", default=None, help="指定表, 逗号分隔")
    p.add_argument("--all", action="store_true", help="迁移全部待迁移表")
    p.add_argument("--daily", action="store_true", help="只迁移每日更新链路表")
    p.add_argument("--dry-run", action="store_true", help="只报告计划, 不改动文件")
    p.add_argument("--no-backup", action="store_true", help="不留备份（不推荐）")
    p.add_argument("--list", action="store_true", help="列出待迁移表后退出")
    p.set_defaults(func=cmd_db_migrate)

    p = sub.add_parser("config-check",
                       help="config.py 完整性检查（主键/日期列/清单覆盖/归属）")
    p.set_defaults(func=cmd_config_check)

    # ── alt 库（另类/海外数据，物理隔离于主库）──
    p = sub.add_parser("alt-update", help="alt 库每日增量更新（P0~P4 分级 + 新鲜度自检）")
    p.add_argument("--tier", choices=_alt_tier_choices(),
                   help="按优先级执行（可选值动态取自 alt.spec.TIERS）")
    p.add_argument("--only", default=None, help="只更新某张表")
    _add_dry_run(p, help_text="只输出计划，不发请求")
    p.add_argument("--lookback", type=int, default=None,
                   help="复核窗口（交易日数），默认取模块内 DEFAULT_LOOKBACK")
    p.add_argument("--force", action="store_true", help="⚠忽略刷新间隔强制全刷")
    p.add_argument("--status", action="store_true", help="只做新鲜度体检（零请求）")
    p.add_argument("--list", action="store_true", help="列出可更新表与策略")
    p.set_defaults(func=cmd_alt_update)

    p = sub.add_parser("alt-audit", help="alt 库数据体检（断点/重复/缺口/规模/隔离）")
    p.add_argument("--checkpoint", action="store_true", help="断点×落盘交叉校验")
    p.add_argument("--date-sets", action="store_true", help="断点日期×落盘日期（集合级）")
    p.add_argument("--dupes", action="store_true", help="重复检测")
    p.add_argument("--gaps", action="store_true", help="覆盖缺口")
    p.add_argument("--scale", action="store_true", help="规模统计")
    p.add_argument("--isolation", action="store_true", help="主库隔离校验")
    p.add_argument("--only", default=None, help="只查某张表（用于重复检测/清理）")
    p.add_argument("--snapshot", action="store_true", help="记录主库基线")
    p.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    p.add_argument("--fix-dupes", action="store_true",
                   help="⚠清理历史整行重复（默认只预演，需配合 --apply 才落盘）")
    p.add_argument("--confirm-gaps", action="store_true",
                   help="⚠把当前快照表独有日期登记为「已确认不可回补」")
    p.add_argument("--apply", action="store_true", help="配合 --fix-dupes 实际执行")
    p.add_argument("--report", nargs="?", const=True, default=False,
                   help="生成结构报告 Markdown（可指定输出路径）")
    p.set_defaults(func=cmd_alt_audit)

    p = sub.add_parser("alt-backfill", help="alt 库历史回补（akshare 备用源）")
    p.add_argument("--tier", choices=_alt_tier_choices(),
                   help="按优先级执行（可选值动态取自 alt.spec.TIERS）")
    p.add_argument("--only", default=None, help="只执行某张表")
    _add_dry_run(p, help_text="只预演，不发请求不写库")
    p.add_argument("--force", action="store_true", help="⚠忽略断点强制重跑")
    p.add_argument("--limit", type=int, default=None, help="最多执行前 N 张表")
    p.add_argument("--date", default=None,
                   help="按日型表的目标日期 YYYYMMDD（默认取最近交易日）")
    p.add_argument("--list", action="store_true", help="列出规格概览")
    p.set_defaults(func=cmd_alt_backfill)

    p = sub.add_parser("alt-us", help="美股建库（alt 库 mode=list 专用入口）")
    p.add_argument("--universe", action="store_true", help="⚠只重建标的全市场清单")
    p.add_argument("--universe-from-cache", default=None,
                   help="从本地缓存(parquet/pkl)生成清单，免重复拉取（省 7~8 分钟）")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 只（试跑用）")
    p.add_argument("--rebuild", action="store_true", help="⚠忽略断点整表重建")
    p.add_argument("--workers", type=int, default=None, help="并发线程数（默认 4）")
    p.add_argument("--delay", type=float, default=None,
                   help="每只请求后延迟秒数（降速防风控，默认 0）")
    p.add_argument("--status", action="store_true", help="查看进度")
    p.add_argument("--prune-ckpt", action="store_true",
                   help="⚠清理断点虚记（断点已记但磁盘无数据），使它们重拉")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_alt_us)

    p = sub.add_parser("alt-verify", help="alt 表规格校验（只读）")
    p.add_argument("--tier", choices=_alt_tier_choices(),
                   help="按优先级筛选（可选值动态取自 alt.spec.TIERS）")
    p.add_argument("--only", default=None, help="只校验某张表")
    p.add_argument("--full-enum", action="store_true",
                   help="enum 表跑全部枚举值（默认只跑首个）")
    p.add_argument("--list-todo", action="store_true", help="只列待校验表（零请求）")
    p.set_defaults(func=cmd_alt_verify)

    # ── 基金与指数分析 ──
    p = sub.add_parser("wind-index", help="【已剥离停用 2026-09-25】Wind 研究暂停，暂存于 _scratch\\wind-研究暂存")
    p.add_argument("--plan", action="store_true", help="只输出分块计划（不消耗额度）")
    p.add_argument("--execute", action="store_true", help="真正调用 Wind（消耗额度，谨慎）")
    p.add_argument("--segments", default=None, help="段位，逗号分隔（如 881,882）")
    p.add_argument("--budget", type=int, default=None, help="调用次数硬上限（默认100）")
    p.add_argument("--out", default=None, help="产出目录（须与 tushare 库分离）")
    p.set_defaults(func=cmd_wind_index)

    p = sub.add_parser("micro-index", help="本地自建微盘指数（替代 Wind 8841431.WI）")
    p.add_argument("--start", default=None, help="起始日期 YYYYMMDD（默认20160101）")
    p.add_argument("--out-dir", default=None, help="输出目录")
    p.add_argument("--exclude-st", action="store_true",
                   help="剔除当前ST名单（含前视偏差，仅作敏感性对照）")
    p.set_defaults(func=cmd_micro_index)

    p = sub.add_parser("factor-lib", help="因子库概览（全收益基准 + 风格因子）")
    p.add_argument("--no-style", action="store_true", help="不含风格因子")
    p.add_argument("--no-matrix", action="store_true", help="不打印相关矩阵")
    p.set_defaults(func=cmd_factor_lib)

    p = sub.add_parser("fund-pool", help="公募主动权益基金池构建（清洗口径锁定版）")
    p.add_argument("--out", default=None, help="输出CSV路径")
    p.set_defaults(func=cmd_fund_pool)

    p = sub.add_parser("manager-profile", help="基金经理风格画像（风格/跟踪指数/β）")
    p.add_argument("--code", default=None, help="基金代码，如 004685")
    p.add_argument("--manager", default=None, help="经理姓名，如 缪玮彬")
    p.add_argument("--topn", type=int, default=None, help="候选指数取前N（默认8）")
    p.add_argument("--all", action="store_true", help="含历史任期（默认仅当前在任）")
    p.add_argument("--min-days", type=int, default=None, help="任期内最少样本日（默认250）")
    p.add_argument("--json", action="store_true", help="输出JSON摘要")
    p.set_defaults(func=cmd_manager_profile)

    # ── 分析扫描 ──
    p = sub.add_parser("qidian", help="奇点战法每日双重信号扫描")
    _add_date(p, help_text="扫描日期 YYYYMMDD (默认: 最近交易日)")
    _add_dry_run(p, help_text="仅扫描不推送")
    p.add_argument("--report", action="store_true", help="生成HTML报告")
    p.add_argument("--push-single-buy", action="store_true",
                   help="方案B: 加推单边买入信号（带观察标记+纪律提示）")
    p.add_argument("--push-single-sell", action="store_true",
                   help="方案C: 加推单边卖出信号（带仅观察标注）")
    p.set_defaults(func=cmd_qidian)

    p = sub.add_parser("scan-divergence", help="[已归档] 全市场MACD背离扫描（停用提示）")
    p.set_defaults(func=cmd_scan_divergence)

    p = sub.add_parser("divergence-trigger", help="[已归档] 分钟级背离触发扫描（停用提示）")
    p.set_defaults(func=cmd_divergence_trigger)

    # ── 监控推送 ──
    p = sub.add_parser("monitor", help="[已归档] 盘中MACD背离监控（停用提示）")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("push-test", help="PushPlus推送测试/发送")
    p.add_argument("--token", default=None, help="PushPlus token (默认取config.py)")
    p.add_argument("--channel", default=None, help="发送渠道 (wechat/clawbot)")
    p.add_argument("--template", default=None, help="消息模板 (txt/markdown/html)")
    p.add_argument("--title", default="通知", help="消息标题")
    p.add_argument("--content", default="", help="消息内容 (不填则发送测试消息)")
    p.add_argument("--test", action="store_true", help="强制发送测试消息")
    p.set_defaults(func=cmd_push_test)

    # ── 工具 ──
    p = sub.add_parser("lof-list", help="场内ETF/LOF清单统计")
    p.set_defaults(func=cmd_lof_list)

    p = sub.add_parser("lof-export", help="场内LOF导出Excel")
    p.add_argument("--out", default=None, help="输出路径 (默认 文档/场内LOF完整列表.xlsx)")
    p.set_defaults(func=cmd_lof_export)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return

    args.func(args)
    print("\n完成。")


if __name__ == "__main__":
    main()
