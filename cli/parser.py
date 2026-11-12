# -*- coding: utf-8 -*-
"""cli.parser — 参数解析（_EPILOG / _NoSuppressSubcommands / build_parser，原样搬出）。"""
import argparse

from cli._shared import _add_date, _add_dry_run, _add_json
from cli.alt_cmds import (
    _alt_tier_choices,
    cmd_alt_audit,
    cmd_alt_backfill,
    cmd_alt_update,
    cmd_alt_us,
    cmd_alt_verify,
)
from cli.db_cmds import (
    _DB_FAMILY,
    cmd_db_backup,
    cmd_db_group,
    cmd_db_meta_close,
    cmd_db_stale_fix,
    cmd_freshness,
    cmd_peek,
    cmd_regress,
)
from cli.fetch_cmds import (
    cmd_backfill,
    cmd_download_cb,
    cmd_download_full,
    cmd_fund_nav,
    cmd_gap_update,
    cmd_intraday_update,
    cmd_redtide_supply,
    cmd_schedule,
)
from cli.misc_cmds import (
    cmd_check_consumer,
    cmd_check_coverage,
    cmd_config_check,
    cmd_doctor,
    cmd_lof_export,
    cmd_lof_list,
    cmd_push_test,
)
from cli.research_cmds import (
    cmd_etf_flow,
    cmd_factor_lib,
    cmd_fund_pool,
    cmd_leverage,
    cmd_manager_profile,
    cmd_micro_index,
)

_EPILOG = """
示例:
  # 数据维护
  python main.py gap-update                     每日增量更新（走 backfill 引擎）
  python main.py gap-update --dry-run           只算缺口，不发请求
  python main.py gap-update --group all         追加资金面/事件面接口
  python main.py gap-update --lookback 10       扩大复核窗口（修历史残缺）
  python main.py intraday-update                尾盘增量（= gap-update --lookback 1 的特例）
  python main.py redtide-supply                 赤潮派生（market_state）
  python main.py fund-nav                       基金净值增量更新
  python main.py fund-nav --repair              历史回补（行数过少的文件）
  python main.py fund-nav --days 10 --limit 200 自定义回溯与限量
  python main.py download-full --phase meta     仅下载元数据
  python main.py download-full --reset          清除断点重新下载
  python main.py download-cb                    可转债数据下载
  python main.py check-coverage                 数据覆盖体检
  python main.py check-coverage 20260911        指定日期体检

  # 库维护（db 命令族；旧平铺名 db-audit / db-schema / db-report / db-clean / db-migrate 仍可用）
  python main.py db audit                       全量数据库体检
  python main.py db audit --section gap         只看缺口
  python main.py db audit --json                体检结果 JSON（机器可读）
  python main.py db schema                      结构扫描（输出 schema JSON）
  python main.py db report                      生成结构报告 Markdown
  python main.py db report --refresh            一步完成：先扫描再出报告
  python main.py db migrate --dry-run           布局迁移计划（by_code→by_year，此为默认）
  python main.py db migrate --all --apply       执行迁移（自动备份+行数校验）
  python main.py db clean                       存量清理（默认干跑；--dry-run 显式干跑）
  python main.py db clean --apply --only fina_indicator   执行清理
  python main.py db ckpt --show                 断点×磁盘交叉核对
  python main.py db ckpt --show --json          交叉核对 JSON（机器可读）
  python main.py config-check                   配置完整性检查
  python main.py regress                        回归测试（改数据层后必跑）

  # 数据速查
  python main.py db freshness                   表级新鲜度（双条件 ds+dw；推荐）
  python main.py db freshness --only dividend,index_weight   只看指定表
  python main.py db freshness --json            新鲜度 JSON（机器可读）
  python main.py freshness --all                旧版单条件速览（兼容保留，建议用 db freshness）
  python main.py peek daily --code 600519.SH    预览某张表的实际数据
  python main.py peek daily --json              预览（JSON，机器可读）
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

  # 监控推送
  python main.py push-test                      发送推送测试消息
  python main.py push-test --title T --content C  发送自定义内容

  # 工具
  python main.py lof-list                       场内ETF/LOF清单统计
  python main.py lof-export                     导出LOF清单Excel
  python main.py leverage                       杠杆风险监控（零请求读本地库）
  python main.py etf-flow                       宽基ETF份额/申赎资金流

全量下载也可直接调用模块（与子命令等价）:
  python -m fetch.full_download --phase all
  python -m fetch.cb_download --reset

已归档命令（2026-09-30 P1-1 删除入口，不再占命令槽）: fetch / report / all /
fix-resonance / daily-report / wind-index / scan-divergence / divergence-trigger /
monitor / qidian / qidian-legacy —— 代码与恢复说明见 _archive/ 对应目录。
"""

class _NoSuppressSubcommands(argparse.RawDescriptionHelpFormatter):
    """隐藏 help=argparse.SUPPRESS 的子命令（Python 3.13 argparse 不自动隐藏）。

    仅在 --help 格式化时**临时**过滤 _choices_actions 里的 SUPPRESS 项并在 finally
    恢复 —— 不永久改动 parser 内部状态，替代此前的 `_choices_actions` 摘除 hack。
    说明：invalid-choice 报错走 _name_parser_map，与本格式化器无关、不受影响。
    """

    def _format_action(self, action):
        choices = getattr(action, "_choices_actions", None)
        if choices:
            saved = choices
            action._choices_actions = [
                a for a in saved if getattr(a, "help", None) is not argparse.SUPPRESS
            ]
            try:
                return super()._format_action(action)
            finally:
                action._choices_actions = saved
        return super()._format_action(action)

def build_parser():
    from config import __version__
    parser = argparse.ArgumentParser(
        prog="pyramid",
        description="A股三层轮动金字塔策略 · 统一入口",
        formatter_class=_NoSuppressSubcommands,
        epilog=_EPILOG,
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

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

    p = sub.add_parser("intraday-update",
                       help="尾盘增量更新（= gap-update --lookback 1 的特例，保留入口）")
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
    p.add_argument("--codes-file", default=None,
                   help="代码清单文件（UTF-8，一行一个 ts_code，# 注释）。"
                        "替代 resolve_codes，绕过 fund_etf 白名单；仅允许 by_code 目标")
    p.add_argument("--force-listed", action="store_true",
                   help="与 --codes-file 联用：清单内代码即使库内已有也计入待跑"
                        "（B 类缺口补数专用，不影响断点）")
    _add_dry_run(p, help_text="只估算请求数, 不发请求")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("schedule", help="周期调度执行器（默认干跑；--apply 才实跑）")
    p.add_argument("--freq", default=None, help="频率档，逗号分隔：D,W,M,Q（默认全档）")
    p.add_argument("--only", default=None, help="只跑指定表，逗号分隔")
    p.add_argument("--workers", type=int, default=1, help="每接口并发（默认 1）")
    p.add_argument("--limit", type=int, default=None, help="每表最多跑 N 个任务")
    p.add_argument("--budget", type=int, default=2000,
                   help="单轮请求预算（默认 2000；超限 RC=2 并列跳过清单）")
    p.add_argument("--apply", action="store_true", help="实际执行（不加则等同 --dry-run）")
    p.add_argument("--dry-run", action="store_true", help="只输出计划，不发请求（默认行为）")
    p.add_argument("--refresh", action="store_true",
                   help="忽略断点重拉（裸 --refresh 会被拒；须配 --freq 或 --only）")
    p.add_argument("--exclude", default=None, help="跳过这些表，逗号分隔（R10 备份窗口互斥）")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("check-coverage", help="数据覆盖体检（可转债 + ETF/LOF）")
    p.add_argument("date", nargs="?", default=None,
                   help="检查日期 YYYYMMDD (默认: 自动获取最近交易日)")
    p.add_argument("--history", action="store_true",
                   help="基金历史起点对账（EE-FUND-1 B1，四表×场内2963只逐码核验起点 gap>30d）")
    p.add_argument("--strict", action="store_true",
                   help="与 --history 联用：存在 A/B 缺口时非零退出码（回归门禁）")
    p.add_argument("--scope",
                   choices=("full", "fund_daily", "fund_nav", "fund_adj", "fund_share"),
                   default="full",
                   help="历史对账范围：full（四表全跑，默认）/ 单表名")
    p.set_defaults(func=cmd_check_coverage)

    p = sub.add_parser("check-consumer", help="决策侧消费标的覆盖体检（A2池+作战包+持仓）")
    p.set_defaults(func=cmd_check_consumer)

    # ── 库维护：db 命令族（2026-09-30 P3 / DB-M4）──
    db = sub.add_parser("db", help="数据库命令族（audit/schema/report/clean/migrate/ckpt）")
    db_sub = db.add_subparsers(dest="db_command", metavar="<subcommand>")
    db.set_defaults(func=cmd_db_group)
    for _sub, _flat, _add_args, _help in _DB_FAMILY:
        _add_args(db_sub.add_parser(_sub, help=_help))
        # 旧平铺名：隐藏兼容别名（调用等价，不占 --help 版面）
        #   help=SUPPRESS 的隐藏由 _NoSuppressSubcommands 格式化器处理
        #   （命令仍注册在 choices 中，可正常调用，regress R1 契约依赖）。
        _add_args(sub.add_parser(_flat, help=argparse.SUPPRESS))

    p = db_sub.add_parser("meta-close",
                          help="元数据刷新后的 R12 收尾（声明重算/补表重建；干跑/--apply）")
    p.add_argument("--apply", action="store_true",
                   help="写盘（先自动备份 known_gaps 与补表），并整跑 regress 自检")
    p.add_argument("--dry-run", action="store_true",
                   help="显式干跑（默认行为；与 --apply 同时给出时以 --dry-run 为准）")
    p.set_defaults(func=cmd_db_meta_close)

    p = db_sub.add_parser("backup", help="日备（默认干跑；--execute 才真跑；--status 只读）")
    p.add_argument("--status", action="store_true", help="只看状态（零动作，复用 doctor 检查）")
    p.add_argument("--mirror", action="store_true", help="日备（默认干跑，只打印计划）")
    p.add_argument("--execute", action="store_true", help="真正执行日备（硬链快照＋校验）")
    p.add_argument("--dry-run", action="store_true",
                   help="显式干跑（与 --execute 同时给出时以干跑为准）")
    p.set_defaults(func=cmd_db_backup)

    p = db_sub.add_parser("stale-fix", help="基金 source_empty 注册表对账（干跑/--apply）")
    p.add_argument("--apply", action="store_true",
                   help="清理 stale 并同步 evidence/count（先自备份）")
    _add_json(p, help_text="输出对账回执 JSON")
    p.set_defaults(func=cmd_db_stale_fix)

    p = sub.add_parser("regress", help="回归测试（盘后流水线 + 存储布局约束）")
    _add_json(p, help_text="输出回归结果 JSON（逐项日志静音，退出码语义不变）")
    p.set_defaults(func=cmd_regress)

    # ── 数据速查 ──
    p = sub.add_parser("freshness",
                       help="数据新鲜度速查（旧版单条件；建议用 db freshness 双条件版）")
    p.add_argument("tables", nargs="?", default=None,
                   help="逗号分隔的表名（默认核心表）")
    p.add_argument("--all", action="store_true", help="显示全部表")
    p.add_argument("--stale", type=int, default=None,
                   help="只显示落后超过N个交易日的表")
    _add_json(p, help_text="输出新鲜度 JSON（机器可读；不打印横幅）")
    p.set_defaults(func=cmd_freshness)

    p = sub.add_parser("peek", help="数据预览（看某张表的实际数据）")
    p.add_argument("table", help="表名，如 daily")
    p.add_argument("--code", default=None, help="标的代码，如 600519.SH")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD")
    p.add_argument("--rows", type=int, default=10, help="显示行数（默认10）")
    p.add_argument("--cols", default=None, help="只显示指定列（逗号分隔）")
    _add_json(p, help_text="输出预览 JSON（机器可读；不打印横幅）")
    p.set_defaults(func=cmd_peek)

    p = sub.add_parser("doctor", help="环境自检（依赖/路径/磁盘/断点/凭据）")
    p.add_argument("--network", action="store_true",
                   help="追加 tushare 连通性实测（消耗 1 次 API 调用）")
    p.add_argument("--json", action="store_true", dest="json",
                   help="输出 JSON")
    p.set_defaults(func=cmd_doctor)
    # 注: ckpt / db-migrate 的平铺入口已并入 db 命令族登记（见上方 _DB_FAMILY），
    #     旧名 ckpt / db-migrate 作为隐藏别名保留。

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

    # ── 监控推送 ──
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

    # [2026-09-30 P1-1 收编] fix-resonance 拆分的两个孤儿活工具（此前只能 python -m 调用）
    p = sub.add_parser("leverage", help="杠杆风险监控（两融B口径，零请求读本地库）")
    p.add_argument("date", nargs="?", default=None, help="交易日 YYYYMMDD (默认: 最新)")
    p.set_defaults(func=cmd_leverage)

    p = sub.add_parser("etf-flow", help="ETF份额/申赎资金流（宽基ETF）")
    p.add_argument("date", nargs="?", default=None, help="交易日 YYYYMMDD (默认: 最新)")
    p.set_defaults(func=cmd_etf_flow)

    return parser
