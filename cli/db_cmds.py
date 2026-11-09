# -*- coding: utf-8 -*-
"""cli.db_cmds — 数据库治理/体检命令与 db 命令族定义（W7b 自 main.py 原样搬出）。"""



from cli._shared import _add_json, _banner


def cmd_db_clean(args):
    """存量数据清理（版本冗余压缩；默认干跑，--apply 才写入）"""
    from tools.db.clean import DB_CLEAN_TARGETS, run_clean
    if args.list:
        print(f"{'目标':22s} {'说明':14s} {'主键':32s} 策略")
        for n, c in DB_CLEAN_TARGETS.items():
            print(f"{n:22s} {c.get('desc',''):14s} "
                  f"{c.get('key')!s:32s} {c.get('strategy','')}")
        return
    _banner("db-clean 存量数据清理")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    apply = bool(args.apply)
    if apply and getattr(args, "dry_run", False):
        print("  [提示] --dry-run 优先：本次不写入（去掉 --dry-run 才真正执行）")
        apply = False
    run_clean(targets=only, apply=apply, backup=not args.no_backup)

def cmd_db_audit(args):
    """全量数据库体检"""
    from tools.db.audit import DB_AUDIT_PKEYS, run_audit
    as_json = getattr(args, "json", False)
    if args.list:
        print(f"{'目录':22s} 主键")
        for n, pk in DB_AUDIT_PKEYS.items():
            print(f"{n:22s} {pk if pk else '(跳过重复检查)'}")
        return
    if not as_json:
        _banner("db-audit 全量数据库体检")
    only = tuple(x.strip() for x in args.only.split(",")) if getattr(args, "only", None) else None
    if args.section == "all":
        sections = ("checkpoint", "dup", "truncation", "gap", "scale")
    else:
        sections = tuple(x.strip() for x in args.section.split(",") if x.strip())
    run_audit(sections=sections, only=only, exact=getattr(args, "exact", False),
              as_json=as_json, data_check=getattr(args, "data_check", False))

def cmd_db_schema(args):
    """数据库结构扫描（输出机器可读 schema JSON）"""
    from tools.db.schema import run_schema
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("db-schema 数据库结构扫描")
    run_schema(out=getattr(args, "out", None), verbose=not args.quiet,
               quiet=args.quiet, as_json=as_json)

def cmd_db_report(args):
    """数据库结构报告（--refresh: 先扫描刷新 schema 再生成，两步合一）"""
    from tools.db.gen_report import run as gen_report
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("db-report 数据库结构报告")
    gen_report(out=getattr(args, "out", None), as_json=as_json,
               refresh=getattr(args, "refresh", False))

def cmd_regress(args):
    """回归测试（盘后流水线 + 存储布局约束）"""
    from tools.regress_pipeline import run as run_regress
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("regress 回归测试")
    _n_ok, n_fail = run_regress(verbose=not as_json, as_json=as_json)
    if n_fail:
        raise SystemExit(1)

def cmd_freshness(args):
    """数据新鲜度速查"""
    from tools.quicklook import run_freshness
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("freshness 数据新鲜度")
    tables = ([x.strip() for x in args.tables.split(",")]
              if getattr(args, "tables", None) else None)
    run_freshness(tables=tables, show_all=args.all, stale_days=args.stale,
                  as_json=as_json)

def cmd_peek(args):
    """数据预览"""
    from tools.quicklook import run_peek
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("peek 数据预览")
    run_peek(args.table, code=args.code, date=args.date,
             rows=args.rows, cols=args.cols, as_json=as_json)

def cmd_ckpt(args):
    """断点管理（查看 / 交叉核对 / 按磁盘回填 / 清虚记）"""
    from tools.ckpt_tool import run_ckpt
    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("ckpt 断点管理")
    run_ckpt(show=args.show, fix=args.fix, clear=args.clear,
             table=args.table, dry_run=args.dry_run, execute=args.execute,
             as_json=as_json)

def cmd_db_migrate(args):
    """存储布局迁移（by_code → by_year）"""
    from tools.db.migrate import DAILY_TABLES, PENDING_TABLES, run_migrate
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

def cmd_db_group(args):
    """db 命令族入口（无子命令时提示用法）"""
    if not getattr(args, "db_command", None):
        print("db 命令族: 请指定子命令 —— audit / schema / report / clean / migrate / ckpt")
        print("  例: python main.py db audit --section gap")
        print("  旧平铺名（db-audit / db-schema / db-report / db-clean / db-migrate）仍可用，等价。")

def cmd_db_meta_close(args):
    """元数据刷新后的 R12 收尾流水线（默认干跑，--apply 才写盘）"""
    from tools.db.meta_close import run
    apply = bool(args.apply)
    if apply and getattr(args, "dry_run", False):
        print("  [提示] --dry-run 优先：本次不写入（去掉 --dry-run 才真正执行）")
        apply = False
    raise SystemExit(run(apply=apply))

def cmd_db_stale_fix(args):
    """基金 source_empty 注册表对账（判定与 R17/R18 同源：check_fund_history）"""
    import json as _json

    from tools.research.fund_stale import apply_fix, detect
    as_json = getattr(args, "json", False)
    det = detect()
    total = sum(len(x["stale"]) for x in det)

    receipt = {"dry_run": not args.apply, "total_stale": total, "detected": det}
    if args.apply:
        if total:
            receipt.update(apply_fix(det, verbose=not as_json))
        else:
            receipt.update({"changed": False, "backup": None, "tables": {}})

    if as_json:
        print(_json.dumps(receipt, ensure_ascii=False, indent=1))
    else:
        print(f"  {'表':12s} {'stale':>6s} {'stale_late':>10s}")
        for x in det:
            print(f"  {x['table']:12s} {len(x['stale']):6d} {len(x['stale_late']):10d}")
        print(f"  合计 stale={total}")
        if args.apply and total:
            print("  ✓ 已清理（evidence/count 已同步）")
        elif args.apply:
            print("  ✓ 无 stale，未写入")
        elif total:
            print("  [DRY-RUN] 加 --apply 清理")
    return 0

def _add_db_clean_args(p):
    p.add_argument("--only", default=None, help="只清理指定目标, 逗号分隔")
    p.add_argument("--apply", action="store_true",
                   help="真正写入(默认干跑); 执行时自动备份整个目录")
    p.add_argument("--dry-run", action="store_true",
                   help="显式干跑（默认行为；与 --apply 同时给出时以 --dry-run 为准）")
    p.add_argument("--no-backup", action="store_true", help="跳过自动备份(不推荐)")
    p.add_argument("--list", action="store_true", help="列出清理目标后退出")
    p.set_defaults(func=cmd_db_clean)

def _add_db_audit_args(p):
    p.add_argument("--section", default="all",
                   help="检查项: all / checkpoint / dup / truncation / gap / scale (可逗号组合)")
    p.add_argument("--only", default=None, help="重复检查只针对指定目录, 逗号分隔")
    p.add_argument("--exact", action="store_true",
                   help="无主键目录做全量整行判重(慢, 约18分钟; 默认抽样)")
    p.add_argument("--list", action="store_true", help="列出主键配置后退出")
    p.add_argument("--data-check", action="store_true", dest="data_check",
                   help="附加 by_code 观测：断点已完成但库内零行的代码数（不进判据/退出码）")
    _add_json(p, help_text="输出体检结果 JSON（不打印横幅/明细）")
    p.set_defaults(func=cmd_db_audit)

def _add_db_schema_args(p):
    p.add_argument("--out", default=None, help="输出路径（默认 文档/数据与工程/db_schema.json）")
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印明细")
    _add_json(p, help_text="stdout 输出回执 JSON（schema 文件照写）")
    p.set_defaults(func=cmd_db_schema)

def _add_db_report_args(p):
    p.add_argument("--out", default=None, help="输出路径（默认 文档/数据与工程/数据库结构报告_YYYYMMDD.md）")
    p.add_argument("--refresh", action="store_true",
                   help="先扫描刷新 schema 再生成报告（= db-schema + db-report 一步完成）")
    _add_json(p, help_text="stdout 输出回执 JSON（Markdown 文件照写）")
    p.set_defaults(func=cmd_db_report)

def _add_ckpt_args(p):
    p.add_argument("--show", action="store_true", help="显示断点与磁盘交叉核对")
    p.add_argument("--fix", action="store_true",
                   help="按磁盘实际数据回填断点（幂等，只增不减）")
    p.add_argument("--clear", action="store_true",
                   help="清除'断点说完成但磁盘无数据'的虚记条目")
    p.add_argument("--table", default=None, help="指定表（逗号分隔）；默认全部")
    p.add_argument("--dry-run", action="store_true", help="只显示计划不写入")
    p.add_argument("--execute", action="store_true", help="真正写入（默认干跑）")
    _add_json(p, help_text="输出结果 JSON（含明细；不打印横幅）")
    p.set_defaults(func=cmd_ckpt)

def cmd_db_freshness(args):
    """表级数据新鲜度（默认只读恒 exit 0；--strict 有红/黄才非零）"""
    from tools.db.freshness import run
    only = {x.strip() for x in args.only.split(",")} if getattr(args, "only", None) else None
    raise SystemExit(run(only=only, as_json=getattr(args, "json", False),
                         strict=getattr(args, "strict", False)))

def _add_db_freshness_args(p):
    p.add_argument("--only", default=None, help="只看指定表, 逗号分隔")
    p.add_argument("--strict", action="store_true", help="存在红/黄表时 exit 1")
    _add_json(p, help_text="输出新鲜度 JSON（不打印表格）")
    p.set_defaults(func=cmd_db_freshness)

def _add_db_migrate_args(p):
    p.add_argument("--tables", default=None, help="指定表, 逗号分隔")
    p.add_argument("--all", action="store_true", help="迁移全部待迁移表")
    p.add_argument("--daily", action="store_true", help="只迁移每日更新链路表")
    p.add_argument("--dry-run", action="store_true", help="只报告计划, 不改动文件")
    p.add_argument("--no-backup", action="store_true", help="不留备份（不推荐）")
    p.add_argument("--list", action="store_true", help="列出待迁移表后退出")
    p.set_defaults(func=cmd_db_migrate)

_DB_FAMILY = (
    ("clean", "db-clean", _add_db_clean_args, "存量数据清理（版本冗余压缩）"),
    ("audit", "db-audit", _add_db_audit_args, "全量数据库体检（断点/重复/缺口/规模）"),
    ("schema", "db-schema", _add_db_schema_args, "数据库结构扫描（输出机器可读 schema JSON）"),
    ("report", "db-report", _add_db_report_args, "数据库结构报告（从 schema JSON 生成 Markdown）"),
    ("migrate", "db-migrate", _add_db_migrate_args, "存储布局迁移（by_code → by_year）"),
    ("freshness", "db-freshness", _add_db_freshness_args, "表级数据新鲜度（只读，恒 exit 0）"),
    ("ckpt", "ckpt", _add_ckpt_args, "断点管理（查看/交叉核对/回填/清虚记）"),
)
