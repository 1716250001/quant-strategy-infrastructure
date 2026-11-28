# -*- coding: utf-8 -*-
"""cli.db_cmds — 数据库治理/体检命令与 db 命令族定义（W7b 自 main.py 原样搬出）。"""

from cli._shared import _add_json, _banner


def cmd_db_backup(args):
    """日备（默认干跑；--execute 才真跑；--status 只读复用 doctor.backup_status）"""
    from tools.doctor import backup_status

    if args.status or not args.mirror:
        st = backup_status()
        _banner("db-backup 日备状态")
        if not st["exists"]:
            print(f"  备份根: {st['root']}（不存在 ⇒ 从未备份）")
            return 1 if args.status else 0
        d = st["stale_days"]
        print(f"  备份根 : {st['root']}")
        print(f"  最新快照: {st['latest']}（{d} 天前）")
        print(f"  版本数 : {st['n_versions']}/{st['keep']}")
        print(f"  校验清单: {'有' if st['sha_ok'] else '缺'}（{st['sha_rows']:,} 行）")
        if not args.mirror:
            return 0
    # --mirror：默认干跑（与 db clean / ckpt --fix 同风格）
    apply = bool(args.execute) and not bool(getattr(args, "dry_run", False))
    _banner("db-backup 日备" + ("" if apply else "（干跑）"))
    if not apply:
        st = backup_status()
        from tools.data_backup import DEFAULT_ROOT

        print("  [干跑] 未写任何文件。计划：")
        print(f"    源  : {DEFAULT_ROOT}")  # R10-1b：源＝数据根（不是日备根）
        print(f"    备至: {st['root']}")
        print("    方式: 硬链增量快照 + sha256 校验 → 7 版轮转／周备 4 版")
        print(f"    上一版: {st['latest'] or '无（首次全量）'}")
        print("    真跑: 加 --execute")
        return 0
    from tools.data_backup import cli_rc, mirror

    return cli_rc(mirror())


def cmd_db_clean(args):
    """存量数据清理（版本冗余压缩；默认干跑，--apply 才写入）"""
    from tools.db.clean import DB_CLEAN_TARGETS, run_clean

    if args.list:
        print(f"{'目标':22s} {'说明':14s} {'主键':32s} 策略")
        for n, c in DB_CLEAN_TARGETS.items():
            print(f"{n:22s} {c.get('desc', ''):14s} {c.get('key')!s:32s} {c.get('strategy', '')}")
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
    run_audit(
        sections=sections,
        only=only,
        exact=getattr(args, "exact", False),
        as_json=as_json,
        data_check=getattr(args, "data_check", False),
    )


def cmd_db_schema(args):
    """数据库结构扫描（输出机器可读 schema JSON）"""
    from tools.db.schema import run_schema

    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("db-schema 数据库结构扫描")
    run_schema(
        out=getattr(args, "out", None), verbose=not args.quiet, quiet=args.quiet, as_json=as_json
    )


def cmd_db_report(args):
    """数据库结构报告（--refresh: 先扫描刷新 schema 再生成，两步合一）"""
    from tools.db.gen_report import run as gen_report

    as_json = getattr(args, "json", False)
    quiet = bool(getattr(args, "quiet", False))
    if not as_json and not quiet:
        _banner("db-report 数据库结构报告")
    gen_report(
        out=getattr(args, "out", None),
        verbose=not quiet,
        as_json=as_json,
        refresh=getattr(args, "refresh", False),
    )


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
    """数据新鲜度速查（CLI 默认 strict；--no-strict 恒 exit 0）"""
    from tools.quicklook import run_freshness

    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("freshness 数据新鲜度")
    tables = [x.strip() for x in args.tables.split(",")] if getattr(args, "tables", None) else None
    rows = run_freshness(tables=tables, show_all=args.all, stale_days=args.stale, as_json=as_json)
    if getattr(args, "strict", True):
        from tools.db import freshness as _f

        if any(r["verdict"] in ("红", "黄") for r in rows):
            raise SystemExit(1)
        if _f._UNREADABLE:
            raise SystemExit(3)


def cmd_peek(args):
    """数据预览"""
    from tools.quicklook import run_peek

    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("peek 数据预览")
    run_peek(
        args.table, code=args.code, date=args.date, rows=args.rows, cols=args.cols, as_json=as_json
    )


def cmd_ckpt(args):
    """断点管理（查看 / 交叉核对 / 按磁盘回填 / 清虚记）"""
    from tools.ckpt_tool import run_ckpt

    as_json = getattr(args, "json", False)
    if not as_json:
        _banner("ckpt 断点管理")
    run_ckpt(
        show=args.show,
        fix=args.fix,
        clear=args.clear,
        table=args.table,
        dry_run=args.dry_run,
        execute=args.execute,
        as_json=as_json,
    )


def cmd_db_migrate(args):
    """存储布局迁移（by_code → by_year）"""
    from tools.db.migrate import DAILY_TABLES, PENDING_TABLES, run_migrate

    if args.list:
        print(f"{'待迁移表':24s} 分组")
        for t in PENDING_TABLES:
            print(f"  {t:22s} {'daily' if t in DAILY_TABLES else 'periodic'}")
        return
    _banner("db-migrate 存储布局迁移")
    # ⭐ CLI-2（§430.3）：五条写命令里唯一默认真写的这条改回干跑。
    # 两个开关同时给时必须干跑（沿用 cmd_db_clean 的「冲突时选安全的一边」）。
    dry_run = (not args.apply) or bool(args.dry_run)
    if args.all:
        tables = list(PENDING_TABLES)
    elif args.daily:
        tables = list(DAILY_TABLES)
    elif args.tables:
        tables = [x.strip() for x in args.tables.split(",") if x.strip()]
    else:
        tables = None
    run_migrate(tables=tables, dry_run=dry_run, keep_backup=not args.no_backup)


def cmd_db_group(args):
    """db 命令族入口（无子命令时提示用法）"""
    if not getattr(args, "db_command", None):
        print("db 命令族（14 个子命令）:")
        for name, flat, _add, help_text in _DB_FAMILY:
            print(f"  {name:14s} {help_text}（旧名 {flat}）")
        print("  meta-close     元数据刷新后的 R12 收尾（干跑/--apply）")
        print("  backup         日备（默认干跑；--apply/--execute 才真跑）")
        print("  stale-fix      基金 source_empty 注册表对账（干跑/--apply）")
        print("  health         数据库健康摘要（默认轻量；--full 跑 audit/fingerprint）")
        print("  例: python main.py db audit --section gap")


_HEALTH_CACHE_MAX_DAYS = 7


def _health_cache_path():
    import os

    from config import PROJECT_DIR

    return os.path.join(PROJECT_DIR, "logs", "db_health", "latest.json")


def _read_health_cache(path):
    import json

    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _health_cache_age_days(rec):
    from datetime import datetime

    try:
        ts = datetime.fromisoformat(str(rec.get("generated_at")))
    except (TypeError, ValueError):
        return None
    return max(0, (datetime.now() - ts).days)


def _run_audit_quiet():
    """跑全量 audit 并只留摘要（stdout 静音；判据与 `db audit` 同源）。"""
    import io
    from contextlib import redirect_stdout

    from tools.db.audit import GAP_CAT_DAILY, run_audit

    with redirect_stdout(io.StringIO()):
        result = run_audit(verbose=False)
    n_issue = 0
    cp = result.get("checkpoint")
    if cp:
        n_issue += len(cp[0])
    n_issue += sum(1 for r in result.get("dup") or [] if "⚠" in str(r.get("verdict", "")))
    n_issue += sum(1 for x in result.get("gap") or [] if x.get("category") == GAP_CAT_DAILY)
    return {"ok": n_issue == 0, "n_issue": n_issue}


def _run_fingerprint_quiet():
    """跑 `fingerprint --verify` 并只留 rc（stdout 静音）。"""
    import io
    from contextlib import redirect_stdout

    from tools.db.fingerprint import main as fingerprint_main

    with redirect_stdout(io.StringIO()):
        rc = int(fingerprint_main(["--verify"]))
    return {"ok": rc == 0, "rc": rc}


def cmd_db_health(args):
    """数据库健康摘要（C-P2-7）。

    默认轻量：doctor 摘要 + `db freshness` + 最近一次 audit/fingerprint 缓存
    （超过 7 天或从未跑 ⇒ 标「过期/未运行」）；`--full` 才真跑 audit+fingerprint
    并刷新缓存；`--strict` 任一不健康 ⇒ exit 1；`--json` stdout 单对象。
    """
    import io
    import json
    import os
    from contextlib import redirect_stdout
    from datetime import datetime

    from tools.doctor import run_doctor
    from tools.quicklook import run_freshness

    as_json = bool(getattr(args, "json", False))
    full = bool(getattr(args, "full", False))
    strict = bool(getattr(args, "strict", False))

    sink = io.StringIO()
    n_fail = n_warn = 0
    rows = []
    errors = []
    try:
        with redirect_stdout(sink):
            report = run_doctor(as_json=True)
        n_fail, n_warn = int(report.n_fail), int(report.n_warn)
    except Exception as exc:  # 组件异常也属于「不健康」，不得静默成 0
        errors.append(f"doctor: {type(exc).__name__}: {exc}")
    try:
        with redirect_stdout(sink):
            rows = run_freshness(as_json=True) or []
    except Exception as exc:
        errors.append(f"freshness: {type(exc).__name__}: {exc}")
    n_red = sum(1 for r in rows if r.get("verdict") == "红")
    n_yellow = sum(1 for r in rows if r.get("verdict") == "黄")

    cache_path = _health_cache_path()
    cache = _read_health_cache(cache_path)
    age = _health_cache_age_days(cache) if cache else None
    if full:
        cache = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "audit": _run_audit_quiet(),
            "fingerprint": _run_fingerprint_quiet(),
        }
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = f"{cache_path}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, cache_path)
        age = 0

    audit = cache.get("audit") if cache else None
    fingerprint = cache.get("fingerprint") if cache else None
    if not cache:
        cache_status = "未运行"
    elif age is None or age > _HEALTH_CACHE_MAX_DAYS:
        cache_status = "过期"
    else:
        cache_status = "ok"

    unhealthy = bool(
        n_fail
        or errors
        or n_red
        or n_yellow
        or cache_status != "ok"
        or (audit and not audit.get("ok"))
        or (fingerprint and not fingerprint.get("ok"))
    )
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "doctor": {"n_fail": n_fail, "n_warn": n_warn},
        "freshness": {"n_red": n_red, "n_yellow": n_yellow, "n_rows": len(rows)},
        "audit": audit,
        "fingerprint": fingerprint,
        "cache": {"path": cache_path, "status": cache_status, "age_days": age},
        "errors": errors,
        "full": full,
        "ok": not unhealthy,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _banner("db health 数据库健康摘要")
        print(f"  doctor   : 失败 {n_fail} / 提示 {n_warn}")
        print(f"  freshness: 红 {n_red} / 黄 {n_yellow}（共 {len(rows)} 表）")
        for err in errors:
            print(f"  [ERROR] {err}")
        suffix = f"（{age} 天前）" if age is not None else ""
        print(f"  体检缓存 : {cache_status}{suffix}")
        if cache_status != "ok":
            print("    去路: python main.py db health --full（首次建立缓存）")
        if audit:
            ok = "通过" if audit.get("ok") else "有异常"
            print(f"  audit    : {ok}（n_issue={audit.get('n_issue')}）")
        if fingerprint:
            print(f"  fingerprint: {'通过' if fingerprint.get('ok') else '失败'}")
        print(f"  结论     : {'OK' if not unhealthy else '需关注'}")
    if strict and unhealthy:
        return 1
    return 0


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
    apply = bool(args.apply) and not bool(getattr(args, "dry_run", False))
    det = detect()
    total = sum(len(x["stale"]) for x in det)

    receipt = {"dry_run": not apply, "total_stale": total, "detected": det}
    if apply:
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
        if apply and total:
            print("  ✓ 已清理（evidence/count 已同步）")
        elif apply:
            print("  ✓ 无 stale，未写入")
        elif total:
            print("  [DRY-RUN] 加 --apply 清理")
    return 0


def _add_db_clean_args(p):
    p.add_argument("--only", default=None, help="只清理指定目标, 逗号分隔")
    p.add_argument(
        "--apply", action="store_true", help="真正写入(默认干跑); 执行时自动备份整个目录"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="显式干跑（默认行为；与 --apply 同时给出时以 --dry-run 为准）",
    )
    p.add_argument("--no-backup", action="store_true", help="跳过自动备份(不推荐)")
    p.add_argument("--list", action="store_true", help="列出清理目标后退出")
    p.set_defaults(func=cmd_db_clean)


def _add_db_audit_args(p):
    p.add_argument(
        "--section",
        default="all",
        help="检查项: all / checkpoint / dup / truncation / gap / scale (可逗号组合)",
    )
    p.add_argument("--only", default=None, help="重复检查只针对指定目录, 逗号分隔")
    p.add_argument(
        "--exact", action="store_true", help="无主键目录做全量整行判重(慢, 约18分钟; 默认抽样)"
    )
    p.add_argument("--list", action="store_true", help="列出主键配置后退出")
    p.add_argument(
        "--data-check",
        action="store_true",
        dest="data_check",
        help="附加 by_code 观测：断点已完成但库内零行的代码数（不进判据/退出码）",
    )
    _add_json(p, help_text="输出体检结果 JSON（不打印横幅/明细）")
    p.set_defaults(func=cmd_db_audit)


def _add_db_schema_args(p):
    p.add_argument("--out", default=None, help="输出路径（默认 文档/数据与工程/db_schema.json）")
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印明细")
    _add_json(p, help_text="stdout 输出回执 JSON（schema 文件照写）")
    p.set_defaults(func=cmd_db_schema)


def _add_db_report_args(p):
    p.add_argument(
        "--out", default=None, help="输出路径（默认 文档/数据与工程/数据库结构报告_YYYYMMDD.md）"
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="先扫描刷新 schema 再生成报告（= db-schema + db-report 一步完成）",
    )
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印明细（与 db schema 对称）")
    _add_json(p, help_text="stdout 输出回执 JSON（Markdown 文件照写）")
    p.set_defaults(func=cmd_db_report)


def _add_ckpt_args(p):
    p.add_argument("--show", action="store_true", help="显示断点与磁盘交叉核对")
    p.add_argument("--fix", action="store_true", help="按磁盘实际数据回填断点（幂等，只增不减）")
    p.add_argument("--clear", action="store_true", help="清除'断点说完成但磁盘无数据'的虚记条目")
    p.add_argument("--table", default=None, help="指定表（逗号分隔）；默认全部")
    p.add_argument("--dry-run", action="store_true", help="只显示计划不写入")
    p.add_argument("--execute", action="store_true", help="真正写入（默认干跑）")
    p.add_argument(
        "--apply", action="store_true", dest="execute", help="与 --execute 同义（主推写法）"
    )
    _add_json(p, help_text="输出结果 JSON（含明细；不打印横幅）")
    p.set_defaults(func=cmd_ckpt)


def cmd_db_freshness(args):
    """表级数据新鲜度（CLI 默认 strict；--no-strict 恒 exit 0）

    `--per-code`（REQ-20261126-01 T6）：改为逐标的最新日期体检，
    按披露节奏分档；默认表 fund_share。
    """
    if getattr(args, "per_code", False):
        from tools.db.per_code import run as run_per_code

        raise SystemExit(
            run_per_code(
                table=getattr(args, "table", "fund_share"),
                asof=getattr(args, "asof", None),
                as_json=getattr(args, "json", False),
                json_out=getattr(args, "json_out", None),
                top=getattr(args, "top", 20),
                strict=getattr(args, "strict", True),
            )
        )

    from tools.db.freshness import run

    only = {x.strip() for x in args.only.split(",")} if getattr(args, "only", None) else None
    raise SystemExit(
        run(only=only, as_json=getattr(args, "json", False), strict=getattr(args, "strict", True))
    )


def _add_db_freshness_args(p):
    p.add_argument("--only", default=None, help="只看指定表, 逗号分隔")
    p.add_argument(
        "--per-code",
        action="store_true",
        help="逐标的最新日期体检（T6：按披露节奏分档，默认表 fund_share）",
    )
    p.add_argument("--table", default="fund_share", help="--per-code 的表名（默认 fund_share）")
    p.add_argument("--asof", default=None, help="--per-code 快照日 YYYYMMDD（默认最近交易日）")
    p.add_argument("--top", type=int, default=20, help="--per-code worst 展示条数（默认 20）")
    p.add_argument("--json-out", default=None, help="--per-code JSON 落盘路径（可选）")
    strict = p.add_mutually_exclusive_group()
    strict.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="存在红/黄表时 exit 1（默认）",
    )
    strict.add_argument(
        "--no-strict", dest="strict", action="store_false", help="恒 exit 0（旧行为）"
    )
    _add_json(p, help_text="输出新鲜度 JSON（不打印表格）")
    p.set_defaults(func=cmd_db_freshness)


def cmd_db_scale_check(args):
    """数值域量级锚体检（只读；≠ db audit --section scale 的目录/字节规模）。"""
    from config.scale_anchors import SCALE_ANCHORS
    from tools.db.scale_check import main as _main

    argv = []
    if args.report:
        argv.append("--report")
    if args.stats:
        argv.append("--stats")
    if args.tables:
        names = {x.strip() for x in args.tables.split(",") if x.strip()}
        if not (names & set(SCALE_ANCHORS)):
            print(f"[db scale-check] 未匹配任何锚点表: {sorted(names)[:5]}")
            raise SystemExit(2)
        argv += ["--tables", args.tables]
    if args.json:
        argv += ["--json", args.json]
    if args.out:
        argv += ["--out", args.out]
    raise SystemExit(int(_main(argv)))


def _add_db_scale_check_args(p):
    p.add_argument("--report", action="store_true", help="L1+L2+L3 全跑（默认）")
    p.add_argument("--stats", action="store_true", help="复算 0.1/99.9 分位分片包络")
    p.add_argument(
        "--tables", default=None, help="仅指定表（逗号分隔，完整相对路径）；未匹配任何锚点表 ⇒ RC=2"
    )
    p.add_argument("--json", default=None, metavar="PATH", help="--stats 结果另存 JSON 文件")
    p.add_argument("--out", default=None, help="--report 结果另存 Markdown")
    p.set_defaults(func=cmd_db_scale_check)


def cmd_db_schema_align(args):
    """跨年度分区 schema 漂移扫描/修复（≠ db schema 的建表定义校验）。"""
    from tools.db.schema_align import main as _main

    argv = [args.action, *(args.tables or [])]
    if args.all:
        argv.append("--all")
    raise SystemExit(int(_main(argv)))


def _add_db_schema_align_args(p):
    p.add_argument("action", choices=["scan", "fix"], help="scan=只扫描；fix=修复漂移")
    p.add_argument("tables", nargs="*", help="表名（缺省＝全库扫描）")
    p.add_argument("--all", action="store_true", help="fix：修复全部有漂移的表")
    p.set_defaults(func=cmd_db_schema_align)


def cmd_db_fingerprint(args):
    """全库指纹门禁（独立门禁，不与 db audit 合并）。"""
    from tools.db.fingerprint import main as _main

    argv = []
    for flag, on in (
        ("--build", args.build),
        ("--verify", args.verify),
        ("--report", args.report),
        ("--deep", args.deep),
        ("--allow-added", args.allow_added),
    ):
        if on:
            argv.append(flag)
    if not (args.build or args.verify or args.report):
        argv.append("--report")  # 默认报告模式（有差异返回 1，便于日常接线）
    if args.root:
        argv += ["--root", args.root]
    if args.baseline:
        argv += ["--baseline", args.baseline]
    raise SystemExit(int(_main(argv)))


def _add_db_fingerprint_args(p):
    p.add_argument("--build", action="store_true", help="生成/覆盖基线")
    p.add_argument("--verify", action="store_true", help="校验当前库")
    p.add_argument("--report", action="store_true", help="报告模式（等价 --verify；有差异返回 1）")
    p.add_argument("--deep", action="store_true", help="深度档：重算 sha256")
    p.add_argument("--allow-added", action="store_true", help="--verify 时容忍新增文件")
    p.add_argument("--root", default=None, help="库根目录")
    p.add_argument("--baseline", default=None, help="基线路径")
    p.set_defaults(func=cmd_db_fingerprint)


def _add_db_migrate_args(p):
    p.add_argument("--tables", default=None, help="指定表, 逗号分隔")
    p.add_argument("--all", action="store_true", help="迁移全部待迁移表")
    p.add_argument("--daily", action="store_true", help="只迁移每日更新链路表")
    p.add_argument("--apply", action="store_true", help="真正执行迁移（默认干跑，只打印计划）")
    p.add_argument("--dry-run", action="store_true", help="显式干跑（等同默认行为，保留向后兼容）")
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
    (
        "scale-check",
        "db-scale-check",
        _add_db_scale_check_args,
        "数值域量级锚体检（≠ db audit --section scale 的目录/字节规模）",
    ),
    (
        "schema-align",
        "db-schema-align",
        _add_db_schema_align_args,
        "跨年度分区 schema 漂移扫描/修复（≠ db schema 的建表定义校验）",
    ),
    (
        "fingerprint",
        "db-fingerprint",
        _add_db_fingerprint_args,
        "全库指纹门禁（独立，不与 db audit 合并）",
    ),
    ("ckpt", "ckpt", _add_ckpt_args, "断点管理（查看/交叉核对/回填/清虚记）"),
)
