# -*- coding: utf-8 -*-
"""
tools/regress_pipeline.py — 回归测试（改造不破坏既有流水线）
==============================================================
盘后流水线固定三步: main.py fetch → fix_and_resonance.py → gen_split_v3_reports.py
日常更新链路: gap-update / intraday-update / fund-nav / redtide-supply / download-cb

测试项（全部只读或 dry-run, 不改数据、不消耗额度）:
  R1 CLI 注册完整 (main.py 各子命令 --help)
  R2 每日增量引擎的缺口计算 (fetch.daily_update)
  R3 限频器按接口生效
  R4 fund_nav / redtide_supply 模块可导入
  R5 盘后三步关键模块可导入
  R6 限频行为核对
  R7 废弃引擎守卫 (旧模块必须硬拦, 不得静默跑错)
  R8 全库布局一致性 (不应再有 by_code 表, 除刻意的 stock_company; 不应有混合布局)
  R9 存储布局期望 与 写入器守卫
  R10 每日更新清单完整性 (BACKFILL_TARGETS ↔ DAILY_UPDATE_GROUPS)
  R11 配置完整性 (条目数/关键常量/判重主键/组结构/键名有效性/正反向覆盖)
  R12 缺口分析口径一致性 (任务计算必须单一真源)
  R13 by_code 截断防护 (服务端上限可能 < 配置值)
  R14 联查层契约 (alt ↔ 主库: 告警/方向/列名/日期列)
  R15 权限漏网监控 (无权限不重试 / 判据不误伤 / 清单交叉验证)

用法:
  python main.py regress        # 统一入口
  python -m tools.regress_pipeline

何时跑: 改动数据层 / 存储布局 / 每日更新链路之后。
"""
import io
import os
import re
import sys
import subprocess

# 代码根目录（供 sys.path 与子进程 cwd 使用）
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

SEP = "=" * 72
PY = sys.executable
passed, failed = [], []


def _say(msg):
    """输出（适配 Windows 控制台编码）"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def check(name, fn):
    try:
        detail = fn()
        passed.append(name)
        _say(f"  [通过] {name} {detail or ''}")
    except Exception as e:
        failed.append((name, str(e)[:160]))
        _say(f"  [失败] {name} → {str(e)[:160]}")


def run(verbose=True):
    """执行全部回归检查。返回 (passed_n, failed_n)。"""
    global passed, failed
    passed, failed = [], []
    _run_all()
    if verbose:
        _say(f"\n{SEP}")
        _say(f"  回归结果: 通过 {len(passed)} 项, 失败 {len(failed)} 项")
        if failed:
            for n, e in failed:
                _say(f"    [失败] {n}: {e}")
        else:
            _say("  全部通过 — 盘后流水线与存储布局均正常")
        _say(SEP)
    return len(passed), len(failed)


def _run_all():
    """实际检查体"""


    print(SEP)
    print("  回归测试 — 盘后流水线 + 按年分区改造")
    print(SEP)


    # ------------------------------------------------------------
    # R1 CLI 注册
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R1 CLI 子命令注册\n{SEP}")


    def _run_cli(args):
        return subprocess.run([PY, "main.py"] + args, cwd=CODE_DIR,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")


    def _registered_commands():
        """取 main.py 已注册的全部子命令（动态，避免硬编码清单漏登）。

        ⚠ 为什么动态取（2026-09-19 审查）:
          原实现硬编码 17 个命令名的 must 清单。新增 freshness / peek /
          doctor / ckpt / daily-report 后**清单没跟着更新** —— 与 R10 同类的
          "两个本该联动的结构各管一摊"：命令注册表（main.build_parser）与
          回归白名单（本文件）不联动，命令被误删/改名时回归不会报警。
          改为直接从 build_parser() 取全集；"必须存在"只保留核心清单。
        """
        from main import build_parser
        parser = build_parser()
        try:
            sp = parser._subparsers._group_actions[0]
            return sorted(sp.choices.keys())
        except Exception:
            # 兜底：解析 --help 文本中的 {a,b,c}
            r = _run_cli(["--help"])
            m = re.search(r"\{([a-z0-9_\-,]+)\}", r.stdout)
            return sorted(x for x in m.group(1).split(",") if x) if m else []


    def r1():
        subs = _registered_commands()
        assert len(subs) >= 20, (
            f"子命令数异常偏少（{len(subs)}）—— 注册表疑似被误改: {subs}")
        must = ["fetch", "report", "all", "fix-resonance", "gap-update",
                "intraday-update", "fund-nav", "redtide-supply",
                "download-full", "download-cb", "backfill", "check-coverage",
                "daily-report", "db-clean", "db-audit", "db-schema",
                "db-report", "db-migrate", "regress",
                # 2026-09-19 审查补充的速查/运维命令（原先不在白名单内）
                "freshness", "peek", "doctor", "ckpt",
                # 2026-09-25 D1：scan-divergence / divergence-trigger 已归档，白名单移除（argparse 注册保留，显示停用提示）
                "qidian", "monitor"]
        missing = [m for m in must if m not in subs]
        assert not missing, f"缺失核心子命令: {missing}"
        return (f"(动态取到 {len(subs)} 个子命令, 关键 {len(must)} 个齐全)")


    def r1f():
        """速查/运维类命令的参数齐全性（2026-09-19 审查补充）。

        这几个命令是"少跑 db-audit 4 分钟"的日常入口，参数被误删不会有
        任何其它测试发现 —— 单独逐一 --help 验证。
        """
        specs = {
            "freshness": ("--all", "--stale"),
            "peek": ("--code", "--date", "--rows", "--cols"),
            "doctor": ("--network", "--json"),
            "ckpt": ("--show", "--fix", "--clear", "--table",
                     "--dry-run", "--execute"),
            "backfill": ("--list", "--only", "--tier", "--dry-run"),
            "daily-report": (),
        }
        for cmd, flags in specs.items():
            r = _run_cli([cmd, "--help"])
            assert r.returncode == 0, f"{cmd} --help 退出码 {r.returncode}"
            for f in flags:
                assert f in r.stdout, f"{cmd} 的 {f} 未注册"
        return f"({len(specs)} 个速查类命令参数齐全)"


    def r1b():
        r = _run_cli(["download-full", "--help"])
        for f in ("--workers", "--phase", "--reset", "--dry-run"):
            assert f in r.stdout, f"{f} 丢失"
        return "(download-full 参数齐全)"


    def r1c():
        r = _run_cli(["download-cb", "--help"])
        for f in ("--workers", "--reset", "--dry-run"):
            assert f in r.stdout, f"{f} 丢失"
        return "(download-cb 参数齐全)"


    def r1d():
        r = _run_cli(["gap-update", "--help"])
        for f in ("--date", "--dry-run", "--group", "--lookback", "--only"):
            assert f in r.stdout, f"{f} 未注册"
        return "(gap-update: --date/--dry-run/--group/--lookback/--only 齐全)"


    def r1e():
        r = _run_cli(["intraday-update", "--help"])
        for f in ("--date", "--dry-run"):
            assert f in r.stdout, f"{f} 未注册"
        return "(intraday-update 参数齐全)"


    check("main.py 子命令注册完整", r1)
    check("download-full --help", r1b)
    check("download-cb --help", r1c)
    check("gap-update --help", r1d)
    check("intraday-update --help", r1e)
    check("速查类命令参数齐全", r1f)


    # ------------------------------------------------------------
    # R2 每日增量引擎缺口计算（只读，不发请求）
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R2 每日增量引擎缺口计算 (只读)\n{SEP}")


    def r2a():
        """组展开 + 配置齐全。

        ⚠ 不硬编码接口数量（曾写 `== 19`，使得每次新增接口都要改断言，
          属脆弱测试）。改为断言"结构性质"：
            1) core 组必须完整存在（下游策略直接依赖，缺一不可）
            2) 展开结果里每个成员都能取到属性定义
        """
        from fetch.daily_update import get_target_conf, expand_groups
        from config import DAILY_UPDATE_GROUPS
        names = expand_groups(["all"])
        core = list(DAILY_UPDATE_GROUPS.get("core", []))
        assert core, "core 组为空"
        miss_core = [n for n in core if get_target_conf(n) is None]
        assert not miss_core, f"core 组配置缺失: {miss_core}"
        miss = [n for n in names if get_target_conf(n) is None]
        assert not miss, f"配置缺失: {miss}"
        return (f"(core={len(core)} + extend={len(names) - len(core)} = "
                f"{len(names)} 个接口, 配置齐全)")


    def r2b():
        from fetch.daily_update import resolve_gaps
        conf = {"mode": "by_date", "date_col": "trade_date", "start": "19901219"}
        dates, info = resolve_gaps("daily", conf, "20260917", lookback=3)
        assert info["latest"] is not None, "未能取到库内最新日期"
        assert len(info["refresh"]) == 3, f"复核窗口应为3, 实为{len(info['refresh'])}"
        return (f"(库内最新={info['latest']} 缺口={len(info['gap'])} "
                f"复核={info['refresh']})")


    def r2c():
        """布局无关性：reader.detect_layout 必须识别出按年分区"""
        from common import reader
        reader.clear_cache()
        lay = reader.detect_layout("daily")
        assert lay == "by_year", f"daily 布局探测为 {lay}"
        n = len(reader.list_codes("daily"))
        assert n > 5000, f"daily 代码数异常: {n}"
        return f"(daily 布局=by_year, 代码数={n})"


    check("接口组展开与配置齐全", r2a)
    check("resolve_gaps() 缺口与复核窗口", r2b)
    check("reader 布局探测", r2c)


    # ------------------------------------------------------------
    # R3 限频器
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R3 限频器按接口生效\n{SEP}")


    def r3():
        from fetch.base import RateLimiter
        limiter = RateLimiter()
        r1v = limiter._rate_for("daily")
        r2v = limiter._rate_for("daily_basic")
        limiter.wait("daily")
        assert r1v == 250, f"daily 限频应为250, 实为{r1v}"
        return f"(构造OK | daily={r1v}/min, daily_basic={r2v}/min)"


    check("RateLimiter 构造 + wait(api)", r3)


    # ------------------------------------------------------------
    # R4 fund_nav / redtide 模块
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R4 fund-nav / redtide-supply 模块\n{SEP}")


    def r4a():
        import fetch.fund_nav_update as m
        assert hasattr(m, "run_fund_nav_update"), "run_fund_nav_update 丢失"
        assert hasattr(m, "repair_incomplete"), "repair_incomplete 丢失"
        return "(导入OK, 入口齐全)"


    def r4b():
        import fetch.redtide_supply as m
        assert hasattr(m, "run_market_state"), "run_market_state 丢失"
        assert hasattr(m, "run_adj_factor") and hasattr(m, "run_stk_limit")
        return "(导入OK, market_state + 两个改道入口齐全)"


    check("fund_nav_update", r4a)
    check("redtide_supply", r4b)


    # ------------------------------------------------------------
    # R5 盘后三步关键模块
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R5 盘后三步关键模块导入\n{SEP}")


    def r5a():
        import main as m
        for f in ("cmd_fetch", "cmd_report", "cmd_all"):
            assert hasattr(m, f), f"{f} 丢失"
        return "(main.py 三步入口齐全)"


    def r5b():
        # 2026-09-25 D2: fix_and_resonance 已拆分为 leverage_monitor + etf_flow，原文件归档
        import tools.leverage_monitor as lm
        import tools.etf_flow as ef
        assert hasattr(lm, "get_leverage_state") and hasattr(lm, "build_dual_warning")
        assert hasattr(ef, "fetch_etf_shares") and hasattr(ef, "build_shares_output")
        assert hasattr(ef, "compute_fund_flows") and hasattr(ef, "derive_fund_flow")
        return "(leverage_monitor + etf_flow 拆分产物OK)"


    def r5c():
        # 日报链路已于 2026-09-24 停用并归档，改检查归档件是否存在
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "_archive", "日报链路-20260924", "gen_split_v3_reports.py")
        assert os.path.isfile(p), "归档件缺失: " + p
        return "(日报链路已归档, 归档件在位)"


    def r5d():
        # [ARCHIVED 2026-09-25 F4] 旧宽基ETF轮动采集链（market_beta/leverage采集版/wide_etf）
        # 已整体归档 → 改验归档件在位（原 import 验证随之作废）
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "_archive", "legacy链-20260925", "fetch", "market_beta.py")
        assert os.path.isfile(p), "归档件缺失: " + p
        return "(旧采集链已归档, 归档件在位 _archive/legacy链-20260925/)"


    check("main.py 三步入口", r5a)
    check("fix_and_resonance", r5b)
    check("gen_split_v3_reports", r5c)
    check("fetch 采集三模块", r5d)


    # ------------------------------------------------------------
    # R6 限频行为
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R6 限频行为核对\n{SEP}")


    def r6():
        from config import API_RATE_LIMIT, API_RATE_LIMIT_DEFAULT
        assert API_RATE_LIMIT.get("daily") == 250, "daily 限频被改动"
        assert API_RATE_LIMIT_DEFAULT == 170, "默认限频被改动"
        return "(daily=250, 其余默认=170)"


    check("限频配置核对", r6)


    # ------------------------------------------------------------
    # R7 废弃引擎守卫
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R7 废弃引擎守卫（必须硬拦）\n{SEP}")


    def r7a():
        from fetch.gap_update import run_gap_update, DeprecatedEngineError
        try:
            run_gap_update(target_date="20260917", dry_run=True)
        except DeprecatedEngineError:
            return "(gap_update 正确抛错, 未静默执行)"
        raise AssertionError("gap_update 未拦截, 会误判缺口并白拉全量!")


    def r7b():
        from fetch.intraday_update import run_update, DeprecatedEngineError
        try:
            run_update(trade_date="20260917", dry_run=True)
        except DeprecatedEngineError:
            return "(intraday_update 正确抛错)"
        raise AssertionError("intraday_update 未拦截!")


    check("gap_update 废弃守卫", r7a)
    check("intraday_update 废弃守卫", r7b)


    # ------------------------------------------------------------
    # R8 全库布局一致性
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R8 全库布局一致性\n{SEP}")


    def r8():
        """布局一致性：不应再有 by_code 布局（除刻意的 stock_company），也不应存在混合布局。

        三类合法布局:
          by_year —— 绝大多数表（已迁移）
          single  —— 一次性全量的表（cn_cpi / broker_recommend / index_weight 等），合法
          by_code —— 仅 stock_company（静态元数据, 无日期列），刻意保留

        ⚠ 混合布局（年度文件 + 非年度文件并存）必须硬拦:
          detect_layout 会因“文件既有年度名又有非年度名”两条判定都不满足而落到
          single 分支，reader 只读 {table}.parquet，年度文件里的新数据成为
          **静默数据黑洞**（实证: margin 曾漏读 0916/0917）。
        """
        from common import reader
        from config import MARKET_DATA_DIR
        root = MARKET_DATA_DIR
        counts = {"by_year": [], "single": [], "by_code": []}
        mixed = []
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            files = [f for f in os.listdir(d) if f.endswith(".parquet")]
            if not files:
                continue
            # 混合布局检测（早于 detect_layout，后者会给出误导性的 single）
            year_like = [f for f in files if len(f) == 12 and f[:4].isdigit()]
            if year_like and len(year_like) != len(files):
                mixed.append(f"{name}(年度{len(year_like)}+其他{len(files) - len(year_like)})")
            reader.clear_cache(name)
            lay = reader.detect_layout(name)
            counts.setdefault(lay, []).append(name)

        assert not mixed, (
            f"存在混合布局 {mixed} —— reader 会误判为 single 而漏读年度文件里的新数据")
        bad = [n for n in counts.get("by_code", []) if n != "stock_company"]
        assert not bad, f"仍存在未迁移的 by_code 表: {bad}"
        return (f"(by_year={len(counts['by_year'])} single={len(counts['single'])} "
                f"by_code={counts.get('by_code')} ← 仅静态表, 刻意保留)")


    check("全库布局统一", r8)


    # ------------------------------------------------------------
    # R9 存储布局期望 vs 写入器守卫
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R9 存储布局期望 与 写入器守卫\n{SEP}")


    def r9():
        """两件事：

        ① 期望布局 == 磁盘实际布局。
           「取数方式(mode)」与「存储布局(layout)」是两件事，历史上被混为一谈：
           mode="by_code" 既决定逐个标的调接口，也直接决定写 {code}.parquet。
           按年分区迁移只改了磁盘数据、没同步这个隐含假定，于是 17 个表出现
           「配置说 by_code、磁盘是 by_year」。一旦有新标的入库（如新上市股进
           stock_all）跑 backfill，就会往年度目录写 {code}.parquet → 布局探测
           翻转 → read_code 恒空、list_codes 返回 ["2026", ...]。
           现由 fetch.backfill_io.table_storage_layout 作为**单一真源**，
           本断言确保它与磁盘不分歧。

        ② upsert_grouped 必须硬拦按年分区目录。
           它写 {code}.parquet，对 by_year 表使用会造成混合布局。
           仅文档警告拦不住（实测: backfill 的 by_code 写路径就是漏网者），
           必须运行时抛 LayoutGuardError。
        """
        import tempfile
        import shutil
        import pandas as pd
        from common import reader
        from common.parquet_store import (
            upsert_grouped, upsert_by_year, LayoutGuardError,
        )
        from fetch.backfill_io import table_storage_layout
        from config import MARKET_DATA_DIR, BACKFILL_TARGETS, DAILY_UPDATE_EXTRA

        # ① 期望 vs 磁盘
        bad = []
        checked = 0
        for name in sorted(set(BACKFILL_TARGETS) | set(DAILY_UPDATE_EXTRA)):
            reader.clear_cache(name)
            disk = reader.detect_layout(name)
            if disk == "none":
                continue
            expect = table_storage_layout(name)
            checked += 1
            if expect != disk:
                bad.append(f"{name}(期望{expect}/磁盘{disk})")
        assert not bad, (
            f"布局期望与磁盘不一致 {bad} —— 写入器会按错误布局落盘，造成混合布局")

        # ② 写入器守卫
        tmp = tempfile.mkdtemp(prefix="regress_r9_")
        try:
            d = os.path.join(tmp, "guardtbl")
            os.makedirs(d, exist_ok=True)
            upsert_by_year(
                pd.DataFrame({"ts_code": ["600519.SH"],
                              "end_date": ["20260101"], "v": [1]}),
                d, date_col="end_date", subset="*", sort_by="end_date")
            try:
                upsert_grouped(
                    pd.DataFrame({"ts_code": ["000001.SZ"],
                                  "trade_date": ["20260102"], "v": [2]}),
                    d, subset="*", sort_by="trade_date")
                raise AssertionError(
                    "upsert_grouped 未拦截按年分区目录 —— 会静默破坏布局")
            except LayoutGuardError:
                pass    # 正确拦截

            # 合法 by_code 目录（无年度文件）必须放行
            d2 = os.path.join(tmp, "stock_company")
            upsert_grouped(
                pd.DataFrame({"ts_code": ["000001.SZ"],
                              "trade_date": ["20260102"], "v": [2]}),
                d2, subset="*", sort_by="trade_date")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return f"(核对{checked}表布局一致 | upsert_grouped 守卫已生效)"


    check("布局期望与守卫", r9)


    # ------------------------------------------------------------
    # R10 每日更新清单完整性
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R10 每日更新清单完整性\n{SEP}")


    def r10():
        """每张「按日/按区间」表必须明确"跑或不跑"，不允许悬空。

        背景（2026-09-19 实证）:
          "属性定义"（BACKFILL_TARGETS / DAILY_UPDATE_EXTRA）与"执行白名单"
          （DAILY_UPDATE_GROUPS）是**两个独立手工维护的结构**：
            get_target_conf(name) 从前者取属性（怎么拉）
            expand_groups(groups) 从后者取名单（每天跑不跑）
          新加接口时极易只写前者、漏登后者 —— fut_mapping / fut_settle 即如此，
          后果是数据**静默停在补数那天、逐日变旧**（gap-update 一切正常、无告警）。

        ⚠ 2026-09-21 扩展：扫描范围纳入 by_range
          原实现只扫 `mode == "by_date"`，于是 **by_range 全族（13 张）完全在
          断言视野之外** —— 实证 shibor 库内最新仅 2026-09-16（当日已 09-21）
          却无人告警。现把 by_range 一并纳入，判据同构。
          （这属"第六种静默失效（每日清单漏登）"在 by_range 上的延伸：
            两个本该联动的结构各管一摊，且**断言本身也漏了半边**。）

        ⚠ 新增「假纳入」判据（⑤，比漏登更危险）:
          `daily_update` 原对 `mode != "by_date"` 一律 `continue`
          → 把 by_range 写进 DAILY_UPDATE_GROUPS **也不会真的跑、且不报错**。
          故断言必须校验"纳入组的表，其 mode 是引擎真正支持的"，
          否则会造出「看着已登记、实际永不执行」的假象。
          （与 fund_company 判重键"键存在但列不存在"同源的断言盲区：
            只验"存在"，不验"真有效"。）

        判据（缺一即报错）:
          ① 在 DAILY_UPDATE_GROUPS 的某个组里          → 每天跑，OK
          ② 在 DAILY_UPDATE_EXEMPT 里（须写明原因）    → 刻意不跑，OK
          ③ freq != "day"（周线/月线）                  → 机制豁免，OK
             （周线/月线本是"周末/月末跑一次"，不属日更范畴）
          ④ tier="X" 或 enabled=False（死结接口）        → 机制豁免，OK
             （tier X 本身已是"明确不拉"的标记，如停更/限频 1次/小时）

          区分 ③④ 与 ②：前者是"机制上不该跑"，后者是"能跑但选择不跑"。
          能跑而未登记者一律报错，逼出"要么纳入、要么写理由"的决定。

        另附反向检查: 组里的表必须都有属性定义，否则跑到它时会拿不到配置。
        """
        from config import (
            BACKFILL_TARGETS, DAILY_UPDATE_EXTRA, DAILY_UPDATE_GROUPS,
            DAILY_UPDATE_EXEMPT,
        )

        # 引擎真正支持"日更"的判据 —— **从 daily_update 导入，单一真源**
        # （2026-09-22 改造）:
        #   原实现此处**又写了一份** DAILY_MODES = ("by_date","by_range")，
        #   与 daily_update 的跳过逻辑是"两个结构各管一摊"——
        #   一旦 daily_update 放开新模式（本次就有：by_code + daily_full_refresh），
        #   本断言会把"合法纳入"误判为「假纳入」。
        #   现直接引用 daily_update.engine_supports_daily，从结构上消除漂移。
        #   （故障注入验证：该断言仍能识别真·假纳入）
        from fetch.daily_update import engine_supports_daily

        in_group = {}
        for g, members in DAILY_UPDATE_GROUPS.items():
            for m in members:
                in_group[m] = g

        # 汇总候选表（属性定义来源）:
        #   ① 模式属日更范畴（by_date / by_range）
        #   ② **所有已在组里的表** —— 必须纳入，否则无法校验其 mode 是否真被支持
        #      （index_global 是 by_code 且只在组里，漏掉它就漏掉了「假纳入」检查）
        DAILY_MODES = ("by_date", "by_range")
        cand = {}
        for src, tag in ((DAILY_UPDATE_EXTRA, "EXTRA"), (BACKFILL_TARGETS, "BACKFILL")):
            for name, conf in src.items():
                if conf.get("mode") in DAILY_MODES or name in in_group:
                    cand[name] = {
                        "mode": conf.get("mode"),
                        "freq": conf.get("freq", "day"), "src": tag,
                        "tier": conf.get("tier"),
                        "enabled": conf.get("enabled", True),
                    }

        # ① 前向：每张表都有归宿
        leaked = []
        for name, info in cand.items():
            if info["freq"] != "day":
                continue                     # ③ 机制豁免: 周线/月线
            if info["tier"] == "X" or info["enabled"] is False:
                continue                     # ④ 机制豁免: 死结接口
            if name in in_group:
                continue                     # ① 已纳入
            if name in DAILY_UPDATE_EXEMPT:
                continue                     # ② 刻意豁免
            leaked.append(f"{name}(tier={info['tier'] or '-'},{info['mode']})")

        assert not leaked, (
            f"以下「按日/按区间」表既未纳入每日更新、也未登记豁免: {leaked}\n"
            f"    → 请二选一：加入 DAILY_UPDATE_GROUPS，或写入 DAILY_UPDATE_EXEMPT（附原因）\n"
            f"    （这类漏登不报错，只会让数据静默逐日变旧。\n"
            f"      by_range 族在 2026-09-21 前一直漏在断言视野外，实证 shibor 静默落后 3 天）")

        # ② 反向：组里的表必须有属性定义
        orphan = [m for m in in_group
                  if m not in BACKFILL_TARGETS and m not in DAILY_UPDATE_EXTRA]
        assert not orphan, f"每日更新组里有表缺少属性定义: {orphan}"

        # ③ 豁免清单本身要写原因（空值说明是"随手加的"）
        no_reason = [k for k, v in DAILY_UPDATE_EXEMPT.items() if not str(v).strip()]
        assert not no_reason, f"豁免清单缺少原因说明: {no_reason}"

        # ④ 假纳入检查 —— 在组里，但 mode 不被 daily_update 支持
        fake = []
        for name in in_group:
            conf = DAILY_UPDATE_EXTRA.get(name) or BACKFILL_TARGETS.get(name)
            if conf is None:
                continue                     # 由 ② 负责报
            if not engine_supports_daily(conf):
                fake.append(f"{name}(mode={conf.get('mode')})")
        assert not fake, (
            f"以下表被列入 DAILY_UPDATE_GROUPS，但其 mode 不被 daily_update 支持: {fake}\n"
            f"    → 引擎会**跳过它们且不报错**，形成「看着已登记、实际永不执行」的假象\n"
            f"    → 二选一：扩 daily_update 支持该 mode（并同步 engine_supports_daily），"
            f"或移出组并登记 DAILY_UPDATE_EXEMPT")

        # ── 汇总 ──
        n_cand = len(cand)
        n_daily = sum(1 for i in cand.values() if i["freq"] == "day")
        n_grp = sum(1 for n in in_group if n in cand)      # 只统计本断言范围内的
        n_ex = len(DAILY_UPDATE_EXEMPT)
        n_dead = sum(1 for i in cand.values()
                     if i["freq"] == "day"
                     and (i["tier"] == "X" or i["enabled"] is False))
        n_week = n_cand - n_daily
        n_by_range = sum(1 for i in cand.values() if i["mode"] == "by_range")
        return (f"({n_cand}表[by_date {n_cand - n_by_range} + by_range {n_by_range}]: "
                f"日更候选{n_daily} = 纳入{n_grp} + 豁免{n_ex} + 死结{n_dead} | "
                f"周/月线{n_week} | 反向无孤儿 | 假纳入无 | 豁免均含原因)")


    check("每日更新清单完整性", r10)


    # ------------------------------------------------------------
    # R11 配置完整性（防"编辑共享配置时误删条目"）
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R11 配置完整性\n{SEP}")


    def r11():
        """配置结构自检 —— 防止编辑 config.py 时误删条目。

        背景（真实教训）:
          R9/R10/R11 已三次抓到人工编辑 config.py 时的误删：
            ① _SINGLE_MODES 常量未随 docstring 同步
            ② TABLE_DATE_COL 被误删 7 个条目
               (cb_issue/cb_rating/cb_share/weekly/monthly/index_weekly/index_monthly)
            ③ 本文件的 CLI 白名单未随新增的 freshness/peek/doctor/ckpt/
               daily-report 更新（2026-09-19 审查发现，已改为动态取全集）
          三次都不是"写错"，而是"改一处时顺手删掉了别的"。

        ⚠ 为什么用"条目数下限"而不是精确匹配:
          精确匹配是脆弱断言（每次新增接口都要改），会被绕过或忽略；
          下限检查只在**异常缩小**时报警 —— 这正是误删的特征。

        ⚠ 判据单一真源（2026-09-19）:
          校验逻辑已抽到 tools/config_check.py，doctor 与本处**共用同一份**
          FLOORS 与判据。若两处各维护一份下限表，就是 R10 揭示的
          "两个本该联动的结构各管一摊" 的复发形态。

        ⚠ 关于 config.py 是否该拆分的结论（2026-09-19 审查）:
          不拆。虽然 1407 行单文件是误删诱因，但拆分会让**关联条目分散**
          （BACKFILL_TARGETS / TABLE_DATE_COL / DB_AUDIT_PKEYS 三处必须同步），
          反而增加漏改风险。更有效的防护是本题断言 + doctor 常驻自检。
        """
        from tools.config_check import check_config
        res = check_config()
        assert res.ok, ("配置完整性检查未通过:\n" + res.render())
        return res.summary


    check("配置完整性", r11)


    # ------------------------------------------------------------
    # R12 缺口分析口径一致性（防 freq 漏读造成大规模误报）
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R12 缺口分析口径一致性\n{SEP}")


    def r12():
        """缺口分析的"应有任务"必须与 backfill 的任务计算同源。

        背景（2026-09-19 实证，本会话发现）:
          `db_audit.audit_gaps` 曾**自行重写** by_date 的任务计算，
          漏掉了 `freq` 参数 → weekly 误报缺口 2,057、monthly 误报 2,475，
          合计 4,532 条，占当时"待补 4,551"的 **99.6%**；真缺口
          （margin/margin_detail/hk_hold 各落后的 1 天）被完全淹没。
          另有两类滚动端点漂移误报：by_range 当年区间端点随"今天"变化、
          by_month 月末日随"今天"变化（断点存 20260916、今天算 20260918）。

          这与 R10 揭示的"两个本该联动的结构各管一摊"同源 —— 第 4 次复发
          （① mode↔layout ② BACKFILL_TARGETS↔DAILY_UPDATE_GROUPS
            ③ CLI 注册表↔回归白名单 ④ backfill_spec↔db_audit 任务计算）。

        断言:
          ① `_period_key` 对四种滚动端点的规范化正确（含跨年 ISO 周）
          ② freq≠day 的表，应有任务数必须按**周期**折算而非按交易日枚举
          ③ 有缺口时，db_audit 的 expect 必须等于 build_tasks（单一真源）
        """
        import datetime as _dt
        from tools.db_audit import _period_key, audit_gaps
        from fetch.backfill_spec import build_tasks, expected_task_count
        from config import BACKFILL_TARGETS
        from common.calendar import open_dates

        # ① 周期键规范化
        assert _period_key("20160101|20161231", "by_range", "day") == "20160101"
        assert (_period_key("000001.SH|20260918", "by_month", "day")
                == "000001.SH|202609")
        assert _period_key("20260918", "by_date", "month") == "202609"
        k1 = _period_key("20251231", "by_date", "week")
        k2 = _period_key("20260102", "by_date", "week")
        assert k1 == k2, f"ISO 跨年周规范化错误: {k1} vs {k2}"
        assert (_period_key("20260918", "by_date", "day") == "20260918"), \
            "按日任务不应被改写"

        # ② freq 表必须按周期折算
        today = _dt.datetime.now().strftime("%Y%m%d")
        n_days = len([d for d in open_dates(start_date="20160101") if d <= today])
        rows = {r["name"]: r for r in audit_gaps(verbose=False)}
        checked = []
        for name, conf in BACKFILL_TARGETS.items():
            freq = conf.get("freq", "day")
            if conf.get("mode") != "by_date" or freq == "day":
                continue
            tasks, kind = build_tasks(name, conf, dry_run=True)
            assert kind == "date" and tasks, f"{name} 任务构建异常"
            assert len(tasks) < n_days * 0.5, (
                f"{name} 应有任务数({len(tasks)})接近交易日数({n_days}) —— "
                f"freq='{freq}' 疑似未被应用（这正是 4,532 条误报的成因）")
            row = rows.get(name)
            if row is not None:
                assert row["expect"] == len(tasks), (
                    f"{name} 口径不一致: db_audit={row['expect']} "
                    f"vs build_tasks={len(tasks)}")
            checked.append(f"{name}(freq={freq},{len(tasks)})")

        # ③ 汇总层：真缺口应远小于误报时代（防再次被淹没）
        #    ⚠ 排除"已声明的历史扩展"（config.HISTORY_BACKFILL_PENDING）:
        #      把 start 往前改是**刻意为之**的真实大缺口，不是口径漂移。
        #      若不排除，断言会持续红灯 → "狼来了" → 真漂移反而被忽视。
        #      未声明的表仍按原阈值严查。
        from config import HISTORY_BACKFILL_PENDING
        pend = set(HISTORY_BACKFILL_PENDING)
        tot_declared = sum(r["missing"] for r in rows.values()
                           if r["name"] in pend)
        unexplained = {r["name"]: r["missing"] for r in rows.values()
                       if r["missing"] and r["name"] not in pend}
        tot = sum(unexplained.values())
        # 其中「日更表」的真缺口 —— 这个才是需要关注的（豁免表差额恒定存在）
        daily_tot = sum(r["missing"] for r in rows.values()
                        if r["missing"] and r.get("category") == "日更")
        assert tot < 500, (
            f"缺口总数 {tot} 异常偏大 —— 疑似出现新的口径漂移"
            f"（误报时代曾达 4,551）。明细: "
            f"{list(unexplained.items())[:8]}"
            + (f" | 另有已声明的历史扩展待补 {tot_declared} 条"
               f"（{sorted(pend)}，见 HISTORY_BACKFILL_PENDING）"
               if tot_declared else ""))
        # ④ single_range 必须只产生 1 个任务（防 db_audit 自行按年切分）
        #    —— 2026-09-20 新增（第 7 次复发的精准断言）:
        #    shibor_lpr 改 single_range 后，db_audit 仍按其**自写的**按年枚举
        #    算成 14 个任务 → 误报"还差 13"（实际只差 1 个请求）。
        #    只看缺口总数抓不到（13 远小于 500 阈值），必须专项断言。
        sr = []
        for name, conf in BACKFILL_TARGETS.items():
            if conf.get("mode") != "by_range" or not conf.get("single_range"):
                continue
            if conf.get("tier") == "X" or conf.get("enabled") is False:
                continue
            tasks, kind = expected_task_count(name, conf)
            assert tasks is not None and len(tasks) == 1, (
                f"{name} 配了 single_range 却算出 "
                f"{None if tasks is None else len(tasks)} 个任务")
            sr.append(name)

        # ⑤ 逐表比对：db_audit 的"应有"必须等于 expected_task_count（单一真源）
        #    —— 这是"两个结构各管一摊"的**通杀**断言: 任何一处自行重写
        #    都会在此暴露（本次即有缺口的 shibor_lpr 被抓出 14 vs 1）。
        mismatched = []
        for name, conf in BACKFILL_TARGETS.items():
            if conf.get("tier") == "X" or conf.get("enabled") is False:
                continue
            if conf.get("mode") not in ("by_date", "by_range", "by_period",
                                        "by_code", "by_month", "once"):
                continue
            row = rows.get(name)
            if row is None:        # 无缺口的表不进 rows，无从比对
                continue
            try:
                exp, _k = expected_task_count(name, conf)
            except Exception:
                continue
            if exp is None:
                continue
            if row["expect"] != len(exp):
                mismatched.append(
                    f"{name}: db_audit={row['expect']} vs 真源={len(exp)}")
        assert not mismatched, (
            "db_audit 与 expected_task_count 口径不一致"
            "（'两个结构各管一摊'复发）: " + "; ".join(mismatched))

        return (f"(周期键规范化OK | 按周期折算: {'; '.join(checked)} | "
                f"single_range={sr or '无'} | 逐表比对OK | "
                # ⚠ 措辞用「未声明差额」而非「真缺口」（2026-09-22）:
                #   tot 含**豁免表的设计性差额**（那些表刻意不日更，差额恒定存在），
                #   并非"真缺口"。db_audit 现已按类别分开，此处同步用词以免混淆。
                f"当前未声明差额 {tot}"
                f"（其中日更表真缺口 {daily_tot}）"
                + (f" + 已声明历史扩展待补 {tot_declared}" if tot_declared else "")
                + ")")


    check("缺口分析口径一致性", r12)


    # ------------------------------------------------------------
    # R13 by_code 截断防护（防"默认窗口"静默漏数据）
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R13 by_code 截断防护\n{SEP}")


    def r13():
        """两道防线，防"by_code 默认窗口静默截断"复发。

        背景（2026-09-19 实证的真实缺陷）:
          by_code 模式只传 ts_code、不传日期参数。对有**默认行数上限**的接口，
          服务端只返最近 N 行 → 早期数据被静默丢弃。
          而原截断检测写的是 `len(df) == max_rows`：
            服务端上限 3000  ≠  config 配的 5000  →  判据不成立 → 检测**失效**
          → index_dailybasic 库内只有 2014-05 起，而源端实有 2004-01 起
            （**漏 10 年**）。同批真截断还有 fund_share / fx_daily / index_weekly。

        ① 引擎判据：不能退回"只认 max_rows"
        ② 数据基线：库内起点不得晚于实测的源端起点（否则=又漏了）
        """
        from fetch.base import (_is_possibly_truncated, SUSPECT_CAPS,
                                TRUNC_MAX_ROUNDS)
        from config import SOURCE_START_BASELINE, TABLE_DATE_COL, MARKET_DATA_DIR

        # ① 引擎判据（关键：3000 < 5000 也必须识别）
        assert _is_possibly_truncated(3000, 5000), (
            "判据失效：3000 行（服务端上限 < 配置 5000）未被识别为可能截断 —— "
            "这正是 index_dailybasic 漏 10 年的成因")
        assert _is_possibly_truncated(5000, 5000), "命中配置上限未被识别"
        assert not _is_possibly_truncated(89, 5000), "正常小结果被误判为截断"
        assert not _is_possibly_truncated(0, 5000), "空结果被误判为截断"
        assert 3000 in SUSPECT_CAPS, "SUSPECT_CAPS 缺少 3000（服务端常见上限）"
        assert TRUNC_MAX_ROUNDS > 0, "缺续拉轮数上限（接口忽略 end_date 时会死循环）"

        # ② 数据基线（库内最早不得晚于源端已知起点）
        import os
        import pandas as pd
        bad, checked = [], 0
        for name, floor in SOURCE_START_BASELINE.items():
            d = os.path.join(MARKET_DATA_DIR, name)
            if not os.path.isdir(d):
                continue                       # 未建库 → 跳过（不误报）
            files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
            if not files:
                continue
            dc = TABLE_DATE_COL.get(name)
            if not dc:
                continue
            # ⚠ 遍历全部文件取"日期列的最小非空值"，不能只读 files[0]：
            #   ① 某些表的 partition 列存在空值（实证 cb_issue 有 1 行 ann_date 为空，
            #      落成了非年份文件 0000.parquet），直接 min() 会得到 nan；
            #   ② 文件名排序在有非年份文件时不可靠。
            mn = None
            try:
                for f in files:
                    s = pd.read_parquet(os.path.join(d, f),
                                        columns=[dc])[dc].dropna()
                    if len(s):
                        v = str(s.min())
                        mn = v if mn is None else min(mn, v)
            except Exception:
                continue
            if mn is None:
                continue
            checked += 1
            if mn > floor:
                bad.append(f"{name}(库内{mn} > 基线{floor})")

        assert not bad, (
            f"以下 by_code 表的库内起点晚于源端已知起点 → 疑似又被截断: {bad}\n"
            f"    → 修复: python main.py backfill --only <表名> --force")
        return (f"(引擎判据OK | 数据基线 {checked} 张全部不晚于源端 | "
                f"续拉轮数上限={TRUNC_MAX_ROUNDS})")


    check("by_code 截断防护", r13)


    # ------------------------------------------------------------
    # R14 联查层契约（alt 库 ↔ 主库）
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R14 联查层契约（alt ↔ 主库）\n{SEP}")


    def r14():
        """联查层的四处静默失效必须不再复发。

        背景（2026-09-20 首次真实验证发现，全部属"不报错但结果不是你要的"）:
          ① 精确匹配默认 how='left' → 匹配率仅 3.3%，却有输出、不报错，
             极易被当成"联查成功"（实证 5,209 行里只有 174 行真匹配）
          ② 容差匹配的语义方向取决于**传参顺序** → 反了不报错，但结果
             完全不同（实证 876 行 vs 174 行）
          ③ value_on 列名写错 → 静默返回 None，与"该日无数据"无法区分
          ④ value_on 取 `df.columns[0]` 当日期列 → 日期列不在首位时
             全量 NaT → "向前回退"分支**恒失效**、恒返回 None
        """
        import warnings as _w

        import pandas as pd

        from alt.reader import join_main, value_on, read_alt, _date_col_of

        # ---------- ① 低匹配率必须告警（内存构造，不依赖磁盘） ----------
        L = pd.DataFrame({"date": [f"2026010{i}" for i in range(1, 9)],
                          "v": list(range(8))})
        R = pd.DataFrame({"trade_date": ["20260101", "20260102"], "w": [1, 2]})
        with _w.catch_warnings(record=True) as rec:
            _w.simplefilter("always")
            out = join_main(L, R, left_date="date", right_date="trade_date")
            assert len(out) == 8, "left join 应保留左侧全部行"
            assert any("匹配率偏低" in str(x.message) for x in rec), (
                "匹配率 2/8=25% 应触发告警 —— 原缺陷会静默产生 6 行全 NaN")
        # 高匹配率不得误报
        with _w.catch_warnings(record=True) as rec_hi:
            _w.simplefilter("always")
            join_main(L, L.rename(columns={"v": "w"}), left_date="date",
                      right_date="date")
            assert not any("匹配率偏低" in str(x.message) for x in rec_hi), \
                "完全匹配不应触发告警（防误报）"

        # ---------- ② 容差方向必须自动识别（两种顺序结果一致） ----------
        mon = pd.DataFrame({"date": ["20260131", "20260228", "20260331",
                                     "20260430", "20260531"],
                            "pe": [10.0, 11.0, 12.0, 13.0, 14.0]})
        day = pd.DataFrame(
            {"trade_date": [f"2026{m:02d}{d:02d}"
                            for m in range(1, 7) for d in (1, 15)],
             "px": list(range(12))})
        with _w.catch_warnings(record=True) as rec2:
            _w.simplefilter("always")
            a = join_main(mon, day, left_date="date", right_date="trade_date",
                          tolerance_days=60)
            assert any("方向已自动校正" in str(x.message) for x in rec2), (
                "低频在前时应触发方向校正告警（原缺陷：静默按错方向算）")
        b = join_main(day, mon, left_date="trade_date", right_date="date",
                      tolerance_days=60)
        assert len(a) == len(b) == len(day), (
            f"两种传参顺序结果应一致且等于高频侧行数: "
            f"{len(a)} vs {len(b)} vs {len(day)}")

        # ---------- ③④ value_on（依赖真实 alt 数据，缺则跳过） ----------
        detail = ""
        try:
            ebs = read_alt("ebs_lg")
        except Exception:
            ebs = None
        if ebs is not None and len(ebs):
            # ③ 列名写错必须抛 KeyError（不得与"无数据"同样返回 None）
            try:
                value_on("ebs_lg", "20260918", "__不存在的列__")
                raise AssertionError(
                    "列名写错时应抛 KeyError —— 原缺陷静默返回 None，"
                    "调用方会误判成'那天没数据'")
            except KeyError:
                pass
            # ④ 日期列必须来自 spec，且该表日期列**恰不在首位**（原 bug 触发条件）
            assert _date_col_of("ebs_lg") == "date", "日期列应由 spec 决定"
            assert list(ebs.columns)[0] != "date", (
                "该表日期列不在首位 —— 正是 columns[0] 取错的触发条件，"
                "本断言保证测试有效")
            d_last = str(ebs["date"].max())
            r_exact = value_on("ebs_lg", d_last, "股债利差")
            assert r_exact is not None, "精确命中应返回值"
            # 向前回退：取一个非交易日，应回退到最近一次取值
            d_next = (pd.to_datetime(d_last, format="%Y%m%d")
                      + pd.Timedelta(days=2)).strftime("%Y%m%d")
            r_fb = value_on("ebs_lg", d_next, "股债利差")
            assert r_fb is not None, (
                f"非交易日 {d_next} 应向前回退 —— 原缺陷恒返回 None"
                f"（日期列取成 columns[0] 后全量 NaT）")
            assert r_fb == r_exact, "回退值应等于最近一次取值"
            detail = f" | 真实表回退OK(最新{d_last})"
        else:
            detail = " | alt 库无数据，③④跳过"

        return (f"(低匹配率告警OK | 容差方向自动识别OK({len(day)}行) | "
                f"列名错抛KeyError{detail})")


    check("联查层契约（alt ↔ 主库）", r14)


    # ------------------------------------------------------------
    # R15 权限漏网监控
    # ------------------------------------------------------------
    print(f"\n{SEP}\n  R15 权限漏网监控\n{SEP}")


    def r15():
        """「权限漏网」必须被识别、不得重试、且清单可交叉验证。

        背景（2026-09-20 实测发现的缺口）:
          账号有 7 项权限漏网（官方门槛高于本档、实测可调）。一旦平台收紧，
          这些表会**静默断裂**——但原实现里"无权限"与"网络故障/接口改名/
          参数写错"走**同一条路径**：
            · 返回值都是 None（调用方无法区分）
            · 都在"其他错误"分支重试（实测 5 次请求 / 18.5 秒，纯浪费）
            · 体检只报"数据落后 N 天"，不报原因
          → 结果：分辨不出"漏网被修复"还是"拉取问题"。
        """
        from fetch.base import (_is_permission_error, _is_rate_limit_error,
                                permission_denied_count, clear_permission_denied,
                                ts_call_with_retry)
        from config import (LEAKY_PERMISSIONS, BACKFILL_TARGETS,
                            DAILY_UPDATE_EXTRA)

        # ---------- ① 判据：只认无权限，不误伤限频/其他 ----------
        PERM = [
            "抱歉，您没有接口(daily)访问权限，请参考 https://...",
            "抱歉，您没有接口(fund_portfolio)访问权限，权限的具体详情访问：...",
        ]
        NOT_PERM = [
            "抱歉，您访问接口(daily)频率超限(200次/分钟)，具体频次详情：...",
            "抱歉，您访问接口(shibor_lpr)频率超限(1次/小时)",
            "抱歉，请指定正确的接口名",
            "HTTPConnectionPool(host='api.tushare.pro'): Read timed out",
            "Expecting value: line 1 column 1 (char 0)",
        ]
        for m in PERM:
            assert _is_permission_error(m), f"未识别为无权限: {m[:40]}"
            assert not _is_rate_limit_error(m), f"无权限被误判为限频: {m[:40]}"
        for m in NOT_PERM:
            assert not _is_permission_error(m), f"误判为无权限: {m[:40]}"

        # ---------- ② 引擎行为：无权限**不重试**（对照实验） ----------
        class _Pro:
            def __init__(self, err):
                self.err, self.calls = err, 0

            def __getattr__(self, _name):
                def _f(**_kw):
                    self.calls += 1
                    raise Exception(self.err)
                return _f

        class _Lim:
            def wait(self, *_a, **_k):
                pass

            def record(self, *_a, **_k):
                pass

            def check_daily(self, *_a, **_k):
                return True

        clear_permission_denied()

        # 组 A：权限错误 —— 期望"只调 1 次"（不重试）
        pa = _Pro("抱歉，您没有接口(daily)访问权限")
        ra = ts_call_with_retry(pa, "daily", _Lim(), {}, verbose=False)
        assert ra is None, "权限错误应返回 None"
        assert pa.calls == 1, (
            f"★ 权限错误被重试了 {pa.calls} 次 —— 应只调 1 次"
            f"（权限是永久性错误，实测重试 5 次白等 18.5 秒）")

        # 组 B：普通错误 —— 期望"重试多次"（反向对照，防过度修复）
        pb = _Pro("HTTPConnectionPool: Read timed out")
        rb = ts_call_with_retry(pb, "daily", _Lim(), {}, verbose=False,
                                max_retries=3)
        assert rb is None, "普通错误最终也应返回 None"
        assert pb.calls > 1, (
            f"★ 普通错误只调了 {pb.calls} 次 —— 网络故障是**临时性**的，"
            f"应当重试；一刀切不重试会丢失自愈能力")

        # ---------- ③ 记录器可用 ----------
        assert permission_denied_count() == 1, (
            f"权限拒绝应被记录 1 次，实得 {permission_denied_count()}")
        clear_permission_denied()
        assert permission_denied_count() == 0, "clear 后应归零"

        # ---------- ④ 清单与表定义交叉验证 ----------
        KNOWN = set(BACKFILL_TARGETS) | set(DAILY_UPDATE_EXTRA)
        assert LEAKY_PERMISSIONS, "漏网清单不应为空"
        bad_name = [k for k in LEAKY_PERMISSIONS if k not in KNOWN]
        assert not bad_name, (
            f"漏网清单含未定义的接口名 {bad_name} —— "
            f"监控会空转（与判重键'键名必须真实存在'同源）")
        NEED = {"claimed", "kind", "use", "alt", "monitor"}
        bad_fld = [k for k, v in LEAKY_PERMISSIONS.items()
                   if not NEED <= set(v.keys())]
        assert not bad_fld, f"漏网清单缺字段 {bad_fld}（需要 {NEED}）"

        n_mon = sum(1 for v in LEAKY_PERMISSIONS.values() if v["monitor"])
        return (f"(判据OK: 无权限{len(PERM)}例识别/限频与网络{len(NOT_PERM)}例不误伤 | "
                f"★权限错误不重试(1次调用) vs 普通错误重试({pb.calls}次) | "
                f"记录器OK | 清单{len(LEAKY_PERMISSIONS)}项 "
                f"表名全合法/字段齐全/其中{n_mon}项纳入告警)")


    check("权限漏网监控", r15)


# ------------------------------------------------------------
# 入口
# ------------------------------------------------------------
if __name__ == "__main__":
    # 保证中文输出不因控制台编码报错
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass
    n_ok, n_fail = run()
    sys.exit(0 if n_fail == 0 else 1)
