# -*- coding: utf-8 -*-
"""
tools/db_audit.py — 全量数据库体检
====================================
交付后/定期核查数据库健康度，四个维度:

  A. 断点 × 落盘 交叉校验
     找出"断点说完成但文件不存在"或"有文件但断点为 0"这两类异常。
     ⚠ 只报这两类 —— 文件数 ≠ 断点数 在按年分区/按标的拆分下都是正常的。

  B. 重复二分判定
     把"重复"拆成两种, 结论完全不同:
       - 复合主键重复 = 单日多行的业务事实(如 index_weight 一天 50 只成分股)
       - 整行完全相同  = 真重复, 才需要考虑清理
     只按单列主键判重会得出大量假阳性。

  C. 缺口分析(按今天截断)
     ⚠ 交易日历含未来交易日, 不截断会算出"缺口到 2027 年"的假象。

  D. 规模统计

用法:
  python main.py db-audit                      # 全部检查
  python main.py db-audit --section dup        # 只查重复
  python main.py db-audit --only daily,moneyflow
  python main.py db-audit --list               # 列出检查的主键配置

主键配置在 config.DB_AUDIT_PKEYS。
"""
import glob
import json
import os
import sys
from datetime import datetime

import pandas as pd
import pyarrow.parquet as pq

from common.calendar import open_dates
from config import (
    ALT_DATA_DIR,
    BACKFILL_PARALLEL_TARGETS,
    BACKFILL_TARGETS,
    DAILY_UPDATE_GROUPS,
    DB_AUDIT_CKPT_ALIASES,
    DB_AUDIT_CKPT_EXTERNAL,
    DB_AUDIT_PKEYS,
    DB_AUDIT_UPSTREAM_DUP,
    KEY_NARROW_ACK,
    MARKET_DATA_DIR,
    TABLE_DATE_COL,
)
from config import checkpoint_registry as _cfg
from config.daily import DAILY_UPDATE_EXTRA, PERIODIC_UPDATE_TARGETS

SEP = "=" * 96

# 缺口分类（2026-09-22 新增）—— 见 audit_gaps docstring「为什么要分类」。
#   DAILY  : 在 DAILY_UPDATE_GROUPS 里 → 每天都会跑 → 差额是**真缺口**，应归零
#   EXEMPT : freq=day 但刻意不日更（DAILY_UPDATE_EXEMPT）→ 差额是**设计性**
#   CYCLE  : freq != day（周线/月线）→ 周期未结束属正常
GAP_CAT_DAILY = "日更"
GAP_CAT_EXEMPT = "豁免"
GAP_CAT_CYCLE = "周期"


def _gap_category(name, conf):
    """判定某接口的缺口类别（用于把「真缺口」与「设计性差额」分开）。"""
    if conf.get("freq", "day") != "day":
        return GAP_CAT_CYCLE
    in_group = any(name in members for members in DAILY_UPDATE_GROUPS.values())
    return GAP_CAT_DAILY if in_group else GAP_CAT_EXEMPT


# 日期列候选（按优先级）；仅用于 config.TABLE_DATE_COL 未覆盖时的兜底探测
_DATE_COL_CANDIDATES = ("trade_date", "cal_date", "ann_date", "date", "end_date")

# 取"库内实际最新日期"时，从文件名（年份）最大的 N 个分区里取 max。
# 见 _get_data_latest_date 的 2026-11-03 注释（§294 F4d）。
_LATEST_PARTITION_COUNT = 3


def _parquet_column_names(path):
    """只读 parquet **元数据**拿列名（不读数据本体，约 1ms）"""
    try:
        import pyarrow.parquet as pq
        return list(pq.ParquetFile(path).schema_arrow.names)
    except Exception as exc:
        print(f"[db.audit] 读 parquet 元数据失败 {path}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return []


def _get_data_latest_date(name):
    """读取 by_date 表的库内实际最新日期（数据口径）。

    断点口径会把"调用成功但返空"也记为完成，导致虚记。
    本函数读 parquet 文件取实际最大日期，用于交叉校验断点真实性。

    性能（2026-09-19 优化）:
      原实现 `columns=None` 读了**全部列**，只为取一个最大日期。
      改为"先用 pyarrow 读元数据确定日期列名 → 只读该列"，实测：
        moneyflow      79ms → 18ms  (省 77%)
        margin_detail  38ms → 15ms  (省 61%)
        stk_limit      32ms → 20ms  (省 37%)
      日期列优先取 config.TABLE_DATE_COL 的权威定义（如 bc_otcqt 是 TRADE_DATE），
      未登记时才按候选列表探测。

    2026-11-03（§294 F4d）:
      原实现只读 `files[-1]`。forecast 目录里 `2027.parquet`（旧 end_date 分区
      遗留，仅 1 行 ann_date=20240808）排在最后 → 读到 2024-08-08，于是
      (2024-08-08, today) 之间的 538 个交易日被 R12 误判为"断点虚记"。
      现改为取文件名（年份）最大的 N 个分区（N=_LATEST_PARTITION_COUNT）再取 max：
      成本只多两列日期，但不再被单个异常分区带偏。
      ⚠ 判据强度不变 —— 仍是"数据最新 vs 应有最新"，只是取样面从 1 个分区扩到 N 个。
      （根治仍是 forecast 分区按 ann_date 重排，另立项。）
    """
    d = os.path.join(MARKET_DATA_DIR, name)
    if not os.path.isdir(d):
        return None
    files = sorted(glob.glob(os.path.join(d, "*.parquet")))
    if not files:
        return None
    try:
        date_col = TABLE_DATE_COL.get(name)
        if not date_col:
            names = _parquet_column_names(files[-1])
            date_col = next((c for c in _DATE_COL_CANDIDATES if c in names), None)
        if not date_col:
            return None
        latest, seen = None, False
        for path in files[-_LATEST_PARTITION_COUNT:]:
            s = pd.read_parquet(path, columns=[date_col])[date_col]
            if not len(s):
                continue
            seen = True
            v = str(s.max())
            if latest is None or v > latest:
                latest = v
        return latest if seen else None
    except Exception as exc:
        print(f"[db.audit] 读库内最新日期失败: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


# ============================================================
# A. 断点 × 落盘 交叉校验
# ============================================================
def _checkpoint_files(exclude_authorities=("NOT_A_CHECKPOINT",)):
    """按 registry 列出全部断点载体（去重，按库+文件名稳定排序）。

    P3-2（2026-11-11）：原为硬编码 3 个文件名 —— 实测数据根下有 21 个载体，
    只认 3 个等于「audit 全绿」并不覆盖其余 18 个。现改由单一真源驱动：
    同一文件被多个域引用时只登一次（如 .backfill_checkpoint.json 有 9 个域）。

    默认排除 NOT_A_CHECKPOINT（audit_report/isolation_log 等 5 个报告类 JSON，
    不回答"某键是否完成"）⇒ 剩余 21 个即 P3-0 的"21 载体"。
    """
    seen = {}
    for d in _cfg.all_domains():
        if d.authority in exclude_authorities:
            continue
        # ⛔ W-2/E 单：file="" 表示「零载体」（表存在但从未启用、无断点文件），
        #    不是「载体名为空字符串」—— 计入会让 audit 去读库根目录本身。
        if not d.file:
            continue
        key = (d.library, d.file)
        # 同一文件多个域时以 PRIMARY 为代表（如实测 .backfill_checkpoint.json
        # 有 9 个域、.checkpoint.json 有 code 与 done_dates 两个域）
        if key not in seen or (seen[key].authority != "PRIMARY" and d.authority == "PRIMARY"):
            seen[key] = d
    return [seen[k] for k in sorted(seen)]


def _carrier_root(dom):
    """载体所在库根：market_data / alt_data（跨库混算会造出假异常）。"""
    return MARKET_DATA_DIR if dom.library == "market_data" else ALT_DATA_DIR


def _count_parquet(d):
    """目录下 parquet 文件数；目录不存在返回 -1。"""
    if not os.path.isdir(d):
        return -1
    return len([f for f in os.listdir(d) if f.endswith(".parquet")])


def _alt_spec_note(name):
    """取 alt 表 spec 的 note（惰性导入，避免 market-only 体检付 alt 导入成本）。"""
    try:
        from alt.spec import BY_NAME
    except Exception as exc:
        # SKIP（§451 基线误判）：返回 "" 是故意的（调用方只判 "已废弃" in note），
        # 空串＝无废弃标记；此处只补可观测性，不改返回值。
        print(f"[db.audit] 导入 alt.spec 失败（note 按空处理）: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return ""
    sp = BY_NAME.get(name)
    return getattr(sp, "note", "") or ""


def _check_carrier(dom):
    """核对**单个**断点载体，返回 (level, depth, msg)。

    K2 修正（18 号 §409.2）：上一版打印 21 个载体、实际只核 3 个（三道 continue
    把 alt 14 / LEGACY 3 / 异构字段 1 全跳过）—— 那是"假装覆盖"。现在**每个载体
    都必须给出结论**，深度分两级：

      deep  ：账本记录数 × 落盘文件数逐表对账（completed/done 为列表的账本）
      struct：JSON 可解析 + 记录结构在位（异构字段、进度快照类文件）

    ⛔ 不许再加"跳过"分支 —— 跳过就等于 K2 倒退。
    """
    path = os.path.join(_carrier_root(dom), dom.file)
    if not os.path.exists(path):
        lvl = "error" if dom.authority in ("PRIMARY", "SUPPLEMENTARY") else "info"
        return lvl, "struct", "文件不存在"
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return "error", "struct", f"JSON 损坏: {str(e)[:40]}"
    if not isinstance(data, dict) or not data:
        return "info", "struct", "空文件 / 非 dict"

    field = dom.completed_field
    recs = {k: v for k, v in data.items() if isinstance(v, dict)}
    hit = [k for k, v in recs.items() if field and isinstance(v.get(field), list)]
    if not hit:
        # 异构字段（如 .periodic_state 的 last_run 是标量）或纯进度快照：只做结构核对
        if not field:
            lack = "无完成字段定义"
        elif any(field in v for v in recs.values()):
            lack = f"{field} 非列表"
        else:
            lack = f"无 {field} 字段"
        return "ok", "struct", f"{len(recs)} 条记录（{lack}，仅结构核对）"

    errs, infos = [], []
    root = _carrier_root(dom)
    for k in sorted(hit):
        done_n = len(recs[k][field])
        target = DB_AUDIT_CKPT_ALIASES.get(k, k)
        nf = _count_parquet(os.path.join(root, target))
        if done_n > 0 and nf <= 0:
            # alt 侧复用既有语义（freshness.py:313 同源）：spec.note 标「已废弃」
            # 且未建表 ⇒ 已归档，不是缺数据（us_daily_qfq，§159）。
            if dom.library == "alt_data" and "已废弃" in _alt_spec_note(k):
                infos.append(f"{k}: 断点 {done_n} 条，表已废弃归档（spec.note）")
            else:
                errs.append(f"{k}: 断点 {done_n} 条但落盘 {max(nf, 0)} 个文件")
        elif nf > 0 and done_n == 0 and target not in DB_AUDIT_CKPT_EXTERNAL:
            infos.append(f"{k}: 有 {nf} 个文件但断点 0")
    # 账本里没有完成记录、但磁盘上有文件的表：多数是"由其他流程填充"（外部流程），
    # 改前会报出来，改后不得因为"没进 hit"就静默吞掉（§409 同型：假装覆盖）。
    for k in sorted(recs):
        if k in hit:
            continue
        target = DB_AUDIT_CKPT_ALIASES.get(k, k)
        nf = _count_parquet(os.path.join(root, target))
        if nf > 0:
            infos.append(f"{k}: 有 {nf} 个文件但账本无 {field} 记录")
    if errs:
        return "error", "deep", "；".join(errs[:3])
    if infos:
        return "info", "deep", "；".join(infos[:3])
    return "ok", "deep", f"{len(hit)} 张表逐表对账通过"


def audit_checkpoint(verbose=True):
    """检查所有断点文件与实际落盘的交叉一致性（registry 驱动，21 载体全覆盖）。

    P3-2b 修正（18 号 §409.2）：上一版打印 21 个载体却只真核 3 个，K2 判不通过。
    现每个载体都给结论，deep/struct 两级在汇总行明示，不再"假覆盖"。

    判定分级(避免误报):
      异常(error): 断点>0 但目标目录不存在 / 无任何文件  ← 真的缺数据
      提示(info):  有文件但断点=0, 且该目录由其他流程填充 ← 正常
      正常(OK):    其余
    """
    if verbose:
        print(SEP)
        print("  A. 断点 × 落盘 交叉校验")
        print(SEP)
        print("  deep = 账本键×落盘逐表对账; struct = JSON/结构核对; 异常 = 断点说完成但无文件")

    errors, infos, deep_n = [], [], 0
    inventory = _checkpoint_files()
    if verbose:
        print(f"\n  遍历断点载体 {len(inventory)} 个（registry 驱动，已去重；"
              f"21 载体 = 全部 − NOT_A_CHECKPOINT）:")
    for dom in inventory:
        lvl, depth, msg = _check_carrier(dom)
        deep_n += depth == "deep"
        if verbose:
            tag = {"ok": "OK  ", "info": "提示", "error": "⛔  "}[lvl]
            print(f"    {tag} [{depth:6s}] {dom.library:11s} {dom.file:46s} {msg}")
        if lvl == "error":
            errors.append((f"{dom.library}/{dom.file}", msg))
        elif lvl == "info":
            infos.append((f"{dom.library}/{dom.file}", msg))

    if verbose:
        print(f"\n  覆盖: 载体 {len(inventory)} 个全部核过（deep {deep_n} / "
              f"struct {len(inventory) - deep_n}）")
        print(f"\n  ⛔ 真异常: {len(errors)}")
        for n, s in errors:
            print(f"    [{n}] {s}")
        if not errors:
            print("    ✓ 无")
        print(f"  ℹ 提示(非故障): {len(infos)}")
        for n, s in infos:
            print(f"    [{n}] {s}")
        if not infos:
            print("    ✓ 无")
        # G1 双向差集（P3-2b）：与上方"断点×落盘"互补 —— 上方看目录/文件，
        # 这里看**账本键**本身（extra 藏旧右端/日更日期键/下架代码）。
        audit_checkpoint_diff(verbose=True)
        # C-2（§421）：断点键 × **磁盘键** 的双向差集（哪个键到底属于谁）
        audit_checkpoint_disk(verbose=True)
    return errors, infos


# ============================================================
# A3. C-2 断点键 × 磁盘键 双向差集（§421，2026-11-12）
# ============================================================
def audit_checkpoint_disk(verbose=True):
    """逐表给出「断点键 vs 磁盘键」的**双向差集**与归属结论。

    与 A2（audit_checkpoint_diff）的分工：
      A2 = 断点账本 × 应有任务（extra/miss，回答"该拉没拉"）
      本函数 = 断点账本 × **磁盘键**（回答"断点记的这个键磁盘到底有没有"）
    ⚠ 与 C-3 的分工：本函数只看「键在不在」，C-3 看「注册表里的码库内到底有没有数据」。

    口径（与 `tools/ckpt_tool --show` 同源，避免两套解释）：
      by_date → `_disk_dates`（排除最近 1 个已收盘交易日，T 日源端可能未发布）
      by_code → `_disk_codes`
      其余模式无磁盘一一对应物 → 不出现在本表（由 A2 覆盖）
    """
    from tools.ckpt_tool import _disk_codes, _disk_dates, _recent_trade_dates

    cp_path = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
    if not os.path.exists(cp_path):
        return []
    cp = json.load(open(cp_path, encoding="utf-8"))
    recent = _recent_trade_dates(1)

    rows = []
    for name in sorted(set(BACKFILL_TARGETS) | set(DAILY_UPDATE_EXTRA)):
        conf = BACKFILL_TARGETS.get(name) or DAILY_UPDATE_EXTRA.get(name) or {}
        mode = conf.get("mode")
        if mode not in ("by_date", "by_code"):
            continue
        done = {str(x) for x in cp.get(name, {}).get("completed", [])}
        if not done:
            continue
        if mode == "by_date":
            dc = conf.get("date_col") or TABLE_DATE_COL.get(name) or "trade_date"
            disk = _disk_dates(name, dc)
        else:
            disk = _disk_codes(name)
        if disk is None:
            continue
        disk = {str(x) for x in disk}
        if mode == "by_date":
            # 断点有磁盘无：排除 T 日（源端未发布属正常，见 ckpt_tool 同口径）
            only_ckpt = {x for x in done - disk if x not in recent}
        else:
            only_ckpt = done - disk
        only_disk = disk - done
        if not only_ckpt and not only_disk:
            verdict = "一致"
        elif only_ckpt and only_disk:
            verdict = "两边都有缺口"
        elif only_ckpt:
            verdict = "⚠ 断点有·磁盘无（疑虚记/待核验）"
        else:
            verdict = "断点滞后（磁盘有·断点无，需 ckpt --fix）"
        rows.append({"name": name, "mode": mode, "ckpt": len(done), "disk": len(disk),
                     "only_ckpt": len(only_ckpt), "only_disk": len(only_disk),
                     "verdict": verdict,
                     "samples": sorted(only_ckpt)[:3] or sorted(only_disk)[:3]})

    if verbose:
        print()
        print(SEP)
        print("  A3. C-2 断点键 × 磁盘键 双向差集（键在不在；T 日已排除）")
        print(SEP)
        if not rows:
            print("  ✓ 无差异（全部表：断点键与磁盘键完全一致）")
            return rows
        print(f"  {'表名':18s} {'模式':8s} {'断点':>7s} {'磁盘':>7s} "
              f"{'断点有磁盘无':>12s} {'磁盘有断点无':>12s}  结论")
        print("  " + "-" * 96)
        tot_c = tot_d = tot_a = tot_b = 0
        for r in rows:
            print(f"  {r['name']:18s} {r['mode']:8s} {r['ckpt']:7,d} {r['disk']:7,d} "
                  f"{r['only_ckpt']:12,d} {r['only_disk']:12,d}  {r['verdict']}")
            if r["samples"]:
                print(f"    样例: {r['samples']}")
            tot_c += r["ckpt"]
            tot_d += r["disk"]
            tot_a += r["only_ckpt"]
            tot_b += r["only_disk"]
        print("  " + "-" * 96)
        print(f"  合计: {len(rows)} 张有差 | 断点有磁盘无 {tot_a:,} | "
              f"磁盘有断点无 {tot_b:,}（断点 {tot_c:,} / 磁盘 {tot_d:,}）")
    return rows


# ============================================================
# A2. G1 双向差集（P3-2b，2026-11-11）
# ============================================================
def _task_date(task, mode):
    """取任务键里的"日期部分"，用于把 miss 拆成未来日/真实日。

    非日期维度（by_code 的代码、once 的标记）返回 "" ⇒ 一律计入"真实"，
    绝不能让它们被误判成"未来日"而掩盖真缺口。
    """
    s = str(task)
    if mode in ("by_code", "once"):
        return ""
    if mode in ("by_range", "by_month"):
        return s.rpartition("|")[2]
    return s


def audit_checkpoint_diff(verbose=True):
    """G1：断点账本 × 应有任务的**双向差集**（含未来日拆分）。

    与 audit_gaps 的三点差别（这就是它存在的理由）：
      1. **双向**：既算 miss（应有·断点无），也算 extra（断点有·应有无）——
         extra 正是"旧右端日/日更日期键/已下架代码"的藏身处；
      2. **不做数据新鲜度替换**：audit_gaps 会用 parquet 最新日期推断断点虚记
         并重写 miss，本函数只看账本本身，避免两套口径互相覆盖；
      3. **miss 拆未来/真实**：快照日之后的交易日属正常未到账（A 股日历
         已排到 2027），必须单列，否则"146 条缺口"会盖住"40 条真缺口"。

    与 audit_gaps 保持一致的地方：同一套 X/enabled/audit_skip 过滤、
    同一个 `_period_key` 折周期端点口径（否则周/月滚动端点会天天假红）。
    """
    cp_path = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
    if not os.path.exists(cp_path):
        return []
    cp = json.load(open(cp_path, encoding="utf-8"))
    today = datetime.now().strftime("%Y%m%d")
    from fetch.backfill_spec import expected_task_count

    rows = []
    tot = {"expect": 0, "done": 0, "extra": 0, "miss": 0, "future": 0, "real": 0}
    for name, conf in BACKFILL_TARGETS.items():
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            continue
        if conf.get("audit_skip"):
            continue
        mode = conf["mode"]
        freq = conf.get("freq", "day")
        done = [str(x) for x in cp.get(name, {}).get("completed", [])]
        if not done and mode != "once":
            continue
        expect, _kind = expected_task_count(name, conf)
        if expect is None:
            continue
        expect = [str(x) for x in expect]
        exp_keys = {_period_key(e, mode, freq) for e in expect}
        done_keys = {_period_key(d, mode, freq) for d in done}
        # extra 与 miss 同走 `_period_key`（派单 M1）：
        #   by_range 只比起始日 ⇒ 右端日随运行时点漂移不算脏键（否则 12 张正常表
        #   各报 1~2 条，合计 17 条，会把"旧右端日"误导成待清理脏键）。
        #   ⚠ 代价：single_range 表"断点右端超前磁盘"看不见（派单总表 U4 已登记）。
        extra = [d for d in done if _period_key(d, mode, freq) not in exp_keys]
        miss = [e for e in expect if _period_key(e, mode, freq) not in done_keys]
        future = [m for m in miss if _task_date(m, mode) > today]
        real = [m for m in miss if _task_date(m, mode) <= today]
        if not extra and not miss:
            continue
        if real:
            verdict = "★ 真缺口"
        elif extra:
            verdict = "⚠ extra"
        else:
            verdict = "未来日(正常)"
        rows.append({"name": name, "mode": mode, "expect": len(expect),
                     "done": len(done), "extra": len(extra), "miss": len(miss),
                     "miss_future": len(future), "miss_real": len(real),
                     "verdict": verdict, "extra_keys": extra[:3],
                     "miss_keys": (real or future)[:3]})
        tot["expect"] += len(expect)
        tot["done"] += len(done)
        tot["extra"] += len(extra)
        tot["miss"] += len(miss)
        tot["future"] += len(future)
        tot["real"] += len(real)

    if verbose:
        print()
        print(SEP)
        print("  A2. G1 双向差集（周期键口径；extra=断点有·应有无，miss=应有·断点无）")
        print(SEP)
        print(f"  基准日(真实今天): {today}")
        print(f"\n  {'表名':18s} {'模式':9s} {'期望':>6s} {'实际':>6s} {'extra':>6s} "
              f"{'miss':>6s} {'未来':>5s} {'真实':>5s}  判定")
        print("  " + "-" * 84)
        for r in rows:
            print(f"  {r['name']:18s} {r['mode']:9s} {r['expect']:6d} {r['done']:6d} "
                  f"{r['extra']:6d} {r['miss']:6d} {r['miss_future']:5d} "
                  f"{r['miss_real']:5d}  {r['verdict']}")
            # 样例键：M3 要求能直接看到"哪只代码/哪一天"（如 001381.SZ）
            if r["extra_keys"] or r["miss_keys"]:
                print(f"    样例: extra={r['extra_keys'] or '-'} "
                      f"miss={r['miss_keys'] or '-'}")
        print("  " + "-" * 84)
        print(f"  合计: 期望 {tot['expect']} | 实际 {tot['done']} | extra {tot['extra']} "
              f"| miss {tot['miss']}（未来 {tot['future']} + 真实 {tot['real']}）"
              f" | {len(rows)} 张表有差")
    return rows


# ============================================================
# B. 重复二分判定
# ============================================================
def _write_path_subsets(name):
    """列出该表在**写入端**实际使用的判重键（供 6a「键是否不足」判定）。

    CC-FIX-6 v3（2026-11-09，§368）：只看审计主键会大面积误伤 —— 真实折叠风险
    取决于写入端 subset：
      · subset 缺失 / "*"  ⇒ 全列判重，同键不同值不会折叠
      · subset 为窄键      ⇒ 同键不同值会被丢弃
    四个来源都解析；DAILY_UPDATE_GROUPS 只声明「谁参与日更」，键仍取自
    BACKFILL_TARGETS / DAILY_UPDATE_EXTRA，故组内成员不额外产生 subset。
    """
    out = []
    for label, conf in (("backfill", BACKFILL_TARGETS.get(name)),
                        ("periodic", PERIODIC_UPDATE_TARGETS.get(name)),
                        ("daily", DAILY_UPDATE_EXTRA.get(name))):
        if conf is not None:
            out.append((label, conf.get("subset") or "*"))
    if not out and any(name in v for v in DAILY_UPDATE_GROUPS.values()):
        out.append(("daily_group", "*"))
    return out


def audit_duplicates(only=None, verbose=True, show_samples=3, exact=False):
    """检查各目录的重复情况，区分"主键重复"与"整行真重复"。

    性能取舍:
      有主键配置 → 只读主键列判重(快), 整行判重仅抽样 show_samples 个文件
      无主键配置 → 需全列判重; 默认抽样采样, exact=True 才全量。
                   实测无主键目录中含 2百万行的大表, 全量判重会让
                   整体体检从 5 分钟涨到 18 分钟, 故默认抽样并明确标注。
    """
    if verbose:
        print()
        print(SEP)
        print("  B. 重复二分判定（复合主键重复 vs 整行完全相同）")
        print(SEP)
        print("  说明: 主键重复常见且多为业务事实; 只有「整行完全相同」才是真冗余。")
        print()
        print(f"  {'目录':18s} {'行数':>12s} {'主键重复':>10s} {'整行相同':>10s}  判定")
        print("  " + "-" * 72)

    results = []
    for name, pk in DB_AUDIT_PKEYS.items():
        if only and name not in only:
            continue
        d = os.path.join(MARKET_DATA_DIR, name)
        if not os.path.isdir(d):
            continue
        files = [f for f in os.listdir(d) if f.endswith(".parquet")]
        if not files:
            continue

        frames = []
        tot = 0
        for f in files:
            try:
                cols = [c for c in (pk or []) if c]
                # 只读主键列(若指定), 否则读全列(用于整行判重)
                x = pd.read_parquet(os.path.join(d, f), columns=cols or None)
                if len(x):
                    frames.append(x)
                    tot += len(x)
            except Exception as exc:
                print(f"[db.audit] 读主键列失败，回退全列 {f}: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                # 主键列可能不存在, 回退读全列
                try:
                    x = pd.read_parquet(os.path.join(d, f))
                    if len(x):
                        frames.append(x if not pk else x[[c for c in pk if c in x.columns]])
                        tot += len(x)
                except Exception:
                    pass

        if not frames or tot == 0:
            continue
        allf = pd.concat(frames, ignore_index=True)

        pk_dup = None
        if pk:
            miss = [c for c in pk if c not in allf.columns]
            pk_dup = -1 if miss else int(allf.duplicated(subset=pk).sum())

        # 整行判重:
        #   有主键配置 → 主键判重已足够, 整行判重仅抽样(避免读全列大表)
        #   无主键配置 → 需全列判重; 默认均匀抽样, exact=True 才全量
        full_dup = None
        dup_scope = ""
        if pk is None:
            if exact or len(files) <= 10:
                samp = files
                dup_scope = "全量"
            else:
                # 均匀采样(避免只取前几个文件导致年份偏斜)
                n_s = 10
                step = max(1, len(files) // n_s)
                samp = files[::step][:n_s]
                dup_scope = f"抽样{len(samp)}/{len(files)}文件"
            try:
                fs = []
                for f in samp:
                    x = pd.read_parquet(os.path.join(d, f))
                    if len(x):
                        fs.append(x)
                if fs:
                    full_dup = int(pd.concat(fs, ignore_index=True).duplicated().sum())
            except Exception:
                pass
        else:
            # CC-FIX-8 v2（§372）：ACK 表的「整行相同=0」必须**全列全量**证明，
            #   抽样 3 个文件不足以支撑永久 ACK（index_global 131 个小文件，代价可忽略）。
            if name in KEY_NARROW_ACK:
                samp = files
            else:
                samp = files[:show_samples] if show_samples else files[:5]
            try:
                fs = []
                for f in samp:
                    x = pd.read_parquet(os.path.join(d, f))
                    if len(x):
                        fs.append(x)
                if fs:
                    full_dup = int(pd.concat(fs, ignore_index=True).duplicated().sum())
            except Exception:
                pass

        if pk is None:
            # 全列判重(抽样或全量)
            if full_dup == 0:
                verd = "✓ 无真重复"
            elif full_dup:
                tag = f"{full_dup:,} 行" if exact else f"{full_dup} 行({dup_scope})"
                verd = (f"ℹ 上游即有重复 {tag}"
                        if name in DB_AUDIT_UPSTREAM_DUP
                        else f"⚠ 有真重复 {tag}")
            else:
                verd = "未采样"
        elif full_dup is None:
            verd = "整行未采样"
        elif pk_dup == 0:
            verd = "✓ 主键干净" if full_dup > 0 else "✓ 无真重复"
        elif full_dup > 0:
            verd = (f"ℹ 上游即有重复(抽样{full_dup:,})"
                    if name in DB_AUDIT_UPSTREAM_DUP
                    else f"⚠ 有真重复(抽样{full_dup:,})")
        elif name in KEY_NARROW_ACK and pk_dup > 0:
            # CC-FIX-8 v2（§369/§372）：已裁定保留窄键的表 —— 显式 ACK + 增长护栏
            _ack = KEY_NARROW_ACK[name]
            _base = int(_ack.get("baseline_dup", 0))
            _ratio = float(_ack.get("alarm_ratio", 1.5))
            _lim = _base * _ratio
            if pk_dup <= _lim:
                verd = f"✓ 已裁定：上游修订（同键多行 {pk_dup:,} 行，基线 {_base}）"
            else:
                verd = (f"⚠ 已裁定表同键多行异常增长：{pk_dup:,} 行 > "
                        f"基线 {_base}×{_ratio:g}={_lim:g}")
        else:
            # CC-FIX-6 v3（2026-11-09，§368 Q3）：主键有重复、抽样整行全不同时，
            #   是否「键不足」取决于**写入端 subset**（不是审计主键）：
            #     · 全部 "*" ⇒ 全列判重，同键不同值不会折叠 → 业务多行
            #     · 存在窄键 ⇒ 写入会被折叠 → 报警并点名是哪条路径
            _ws = _write_path_subsets(name)
            _narrow = [(lbl, s) for lbl, s in _ws if s != "*"]
            if not _narrow:
                verd = "✓ 业务多行（写路径已全列判重）"
            else:
                _desc = "; ".join(f"{lbl}={s}" for lbl, s in _narrow)
                verd = (f"⚠ 键可能不足：同键不同值 {pk_dup:,} 行"
                        f"（写路径 subset={_desc}）")

        results.append({"name": name, "rows": tot, "pk_dup": pk_dup,
                        "full_dup": full_dup, "verdict": verd})
        if verbose:
            if pk is None:
                pks = "未配置"
            elif pk_dup == -1:
                pks = "缺列"
            else:
                pks = f"{pk_dup:,}"
            fds = f"{full_dup:,}" if full_dup is not None else "-"
            print(f"  {name:18s} {tot:12,d} {pks:>10s} {fds:>10s}  {verd}")

    if verbose:
        bad = [r for r in results if "⚠" in r["verdict"]]
        print(f"\n  ⚠ 需关注: {len(bad)}")
        for r in bad:
            print(f"    {r['name']}: {r['verdict']} (行数 {r['rows']:,})")
        if not bad:
            print("  ✓ 未发现真重复")
    return results


# ============================================================
# B2. 截断残留检测（CC-FIX-6，2026-11-09，§366/§367）
# ============================================================
def audit_truncation(verbose=True, table_dirs=None):
    """逐码行数恰为 cap（源端单次硬顶，未配则回落 page_size）整数倍 ⇒ 疑似翻页停在整页（源端极少长这样）。

    CC-FIX-4 一次性修过 share_float 的 6,000 行截断，但 5R 前又长出残留（§366）
    —— 一次性点名脚本挡不住复发，故做成常态护栏。

    table_dirs: {table: 目录} 覆盖默认 MARKET_DATA_DIR/<table>，
        供"对历史冷备只读复现"用（Step 6b 判据②）。
    判据由 BACKFILL_TARGETS[*]["cap"] 优先、未配回落 ["page_size"]；新增配了任一字段的
    表自动纳入，本函数不含任何表名/页大小字面量。
    本段是**诊断项**：不计入 run_audit 的 n_issue，也不影响任何退出码。
    """
    if verbose:
        print()
        print(SEP)
        print("  B2. 截断残留检测（单码行数为 cap／page_size 整数倍，见各行标注）")
        print(SEP)
        print("  说明: 只有存在「单次硬顶」的接口才需要 offset 翻页；"
              "停在整页 = 疑似少翻一页。")

    # CC-FIX-6 v3（2026-11-09，§368）：判据优先用 cap（源端单次硬顶＝截断签名），
    #   未配 cap 的表回落 page_size。
    targets = {n: c for n, c in BACKFILL_TARGETS.items()
               if c.get("page_size") or c.get("cap")}
    out = []
    failed = 0
    for name, conf in sorted(targets.items()):
        page = int(conf.get("cap") or conf["page_size"])
        unit = "cap" if conf.get("cap") else "page_size"
        d = (table_dirs or {}).get(name) or os.path.join(MARKET_DATA_DIR, name)
        if not os.path.isdir(d):
            continue
        files = sorted(glob.glob(os.path.join(d, "*.parquet")))
        if not files:
            continue

        # 先只读 schema 判列；任何读取异常都要 fail-loud（§368 第 4 条），不许静默跳过
        good = []
        for f in files:
            try:
                good.append((f, pq.read_schema(f).names))
            except Exception as e:
                failed += 1
                if verbose:
                    print(f"  [READ-FAIL] {name}/{os.path.basename(f)}: "
                          f"{type(e).__name__}: {e}")
        if not good:
            continue
        no_col = [f for f, names in good if "ts_code" not in names]
        if len(no_col) == len(good):
            if verbose:
                print(f"  {name:22s} 无 ts_code 列，跳过（非逐码分页表）")
            continue
        if no_col:
            failed += len(no_col)
            if verbose:
                print(f"  [READ-FAIL] {name}: {len(no_col)} 个分片缺 ts_code 列: "
                      f"{[os.path.basename(f) for f in no_col][:5]}")

        frames = []
        for f, _names in good:
            if f in no_col:
                continue
            try:
                frames.append(pd.read_parquet(f, columns=["ts_code"]))
            except Exception as e:
                failed += 1
                if verbose:
                    print(f"  [READ-FAIL] {name}/{os.path.basename(f)}: "
                          f"{type(e).__name__}: {e}")
        if not frames:
            continue
        cnt = pd.concat(frames, ignore_index=True)["ts_code"].astype(str).value_counts()
        sus = cnt[(cnt % page == 0) & (cnt >= page)].sort_values(ascending=False)
        out.append({"table": name, "cap": page, "cap_source": unit,
                    "codes": len(cnt), "rows": int(cnt.sum()),
                    "suspects": {str(k): int(v) for k, v in sus.items()}})
        if verbose:
            print(f"  {name:22s} {unit}={page:<5d} {len(cnt):,} 码 / "
                  f"{int(cnt.sum()):,} 行 → 疑似 {len(sus)} 只")
            for code, n in list(sus.items())[:10]:
                print(f"      {code}  db_rows={n:,}  db_rows%{page}={n % page}")
            if len(sus) > 10:
                print(f"      ... 其余 {len(sus) - 10} 只见 JSON/明细")
            print("      " + ("✓ 无截断残留" if len(sus) == 0 else
                              f"⚠ 疑似截断 {len(sus)} 只"
                              f"（必须用 fetch_paged_by_code 重探）"))
    if verbose:
        print(f"  [统计] 读取失败 {failed} 个文件")
        if not out:
            print("  未发现配置了 page_size/cap 的逐码分页表")
    return out


# ============================================================
# C. 缺口分析（按今天截断）
# ============================================================
def _period_key(task, mode, freq):
    """把任务规范化为**周期键**，用于消除"滚动端点漂移"造成的误报。

    为什么需要（2026-09-19 实证）:
      周/月/年度的"最后一个周期"尚未结束，其端点会**随当前日期每天漂移**：
        · monthly   月末日  20260917(前天) → 20260918(今天)
        · index_weight 同理（月末序列 × 代码）
        · shibor / us_tycr / new_share（by_range 当年区间端点 = 今天）
      而断点里存的是**运行当天**算出的端点 → 与今天算出的必然不等
      → 每天稳定误报若干条。这是"狼来了"：真缺口（margin 落后的 1 天）
      被假缺口淹没。

    规范化规则（把"同一周期的不同端点表示"映射为同一个键）:
      by_range                → 起始日（"20160101|20161231" → "20160101"）
      by_month                → code|YYYYMM（"000001.SH|20260918" → "000001.SH|202609"）
      by_date + freq=month    → YYYYMM
      by_date + freq=week     → YYYY"W"ww（ISO 周，正确处理跨年周）
      其余（freq=day 等）      → 原样（按日任务本就该精确比对）
    """
    s = str(task)
    if mode == "by_range":
        return s.split("|")[0]
    if mode == "by_month":
        code, _sep, d = s.partition("|")
        return f"{code}|{d[:6]}"
    if mode == "by_date" and freq == "month":
        return s[:6]
    if mode == "by_date" and freq == "week":
        try:
            import datetime as _dt
            dd = _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            y, w, _ = dd.isocalendar()
            return f"{y}W{w:02d}"
        except Exception as exc:
            print(f"[db.audit] 周期键折算失败（回退原串 {s}）: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return s
    return s


def _period_ongoing(task, freq, today):
    """该任务所属周期**是否仍在进行中**（未结束 → 不能据此判"数据落后"）。

    用途: `data_stale`（断点虚记）判定的排除项。
      实证：monthly 的 expect 末尾是"9 月月末该交易日"，而 9 月尚未结束，
      月末数据本就不存在 → 若不排除，每天都会误报一次"断点虚记"。
    """
    if not task or freq == "day":
        return False
    s = str(task)
    if freq == "month":
        return s[:6] == str(today)[:6]
    if freq == "week":
        try:
            import datetime as _dt
            a = _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isocalendar()[:2]
            b = _dt.date(int(today[:4]), int(today[4:6]),
                         int(today[6:8])).isocalendar()[:2]
            return a == b
        except Exception as exc:
            print(f"[db.audit] 周周期判定失败 {task!r}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return False
    return False


def audit_gaps(verbose=True):
    """基于 backfill 断点与各模式的"应有任务集"，算出所有未完成接口的缺口。

    覆盖 5 种取数模式:
      by_date    → 应有 = 起止区间内交易日
      by_range   → 应有 = 按年切分的区间任务
      by_period  → 应有 = 季报报告期
      by_month   → 应有 = 月末序列 (×代码数)
      by_code    → 应有 = 代码清单长度 (读本地元数据)
    ⚠ 只算到"今天" —— 交易日历含未来交易日, 不截断会算出缺口到 2027 年的假象。

    ── 为什么要**分类**（2026-09-22 新增）────────────────────────────
    原实现把所有接口的差额**混在一个「合计待补」里**，导致数字严重误导：

      实证（2026-09-22 体检）：合计报「缺口 49 / 12 个接口待补」，
      而逐张核查后发现 **49 全部来自「刻意不日更」的表**
      （10 张 DAILY_UPDATE_EXEMPT + weekly 周线 + shibor_lpr 月频），
      **没有任何一张日更表在内** —— 即真实缺口为 0，但体检结论写着「⚠ 12 项待关注」。

    根因：db_audit 遍历的是 BACKFILL_TARGETS（**属性定义** = 怎么拉），
    而"每天跑不跑"由 DAILY_UPDATE_GROUPS 决定 —— 两者是**独立维护的两个结构**。
    对"不在组里"的表，其「应有」随日历逐日增长、但数据永不更新，
    于是差额**每天 +1、永远存在**，属**设计性差额而非缺口**。

    ⇒ 现按三类分开呈现，让"真缺口"一眼可见：
        · 日更（在 DAILY_UPDATE_GROUPS）→ 差额是真缺口，应归零
        · 豁免（freq=day 但刻意不日更） → 设计性差额，会持续存在
        · 周期（freq=week/month）       → 周期未结束，属正常
    """
    if verbose:
        print()
        print(SEP)
        print("  C. 缺口分析（按今天截断, 覆盖全部取数模式）")
        print(SEP)

    cp_path = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
    if not os.path.exists(cp_path):
        print("  无 backfill 断点, 跳过")
        return []
    cp = json.load(open(cp_path, encoding="utf-8"))
    today = datetime.now().strftime("%Y%m%d")

    # 任务计算一律走 fetch.backfill_spec.expected_task_count（快照日 20261113）

    if verbose:
        print(f"  基准日: {today}")
        print(f"\n  {'接口':18s} {'模式':9s} {'已完成':>8s} {'应有':>8s} "
              f"{'完成率':>8s} {'还差':>7s}  {'类别':4s}")
        print("  " + "-" * 76)

    out = []
    for name, conf in BACKFILL_TARGETS.items():
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            continue
        if conf.get("audit_skip"):
            continue  # 独立补数入口: 缺口由日更/增量机制兜底, db_audit 不重复算
        if conf["api"] in ("cn_cpi", "cn_ppi", "cn_m", "cn_pmi", "cn_gdp",
                           "sf_month", "hk_basic"):
            pass  # once 类照常处理
        mode = conf["mode"]
        done = set(cp.get(name, {}).get("completed", []))
        if not done and mode not in ("once",):
            continue

        expect = None
        if mode == "by_date":
            # ⚠ 必须复用 expected_task_count（单一真源）—— 它处理了 freq 参数：
            #   freq=week/month 的接口（weekly/monthly）只需在**周末/月末那个
            #   交易日**拉一次，而非每个交易日。
            #
            # 2026-09-19 实证（本处的真实缺陷）：
            #   原实现自行写 `[d for d in open_dates(start) if d <= today]`，
            #   **漏了 freq** → weekly 误报缺口 2,057、monthly 误报 2,475，
            #   合计 4,532 条，占全部"待补 4,551"的 **99.6%** —— 真缺口
            #   （margin/margin_detail/hk_hold 各 1 条断点虚记）被彻底淹没。
            #   这与 R10 揭示的"两个结构各管一摊"同源：任务计算在
            #   backfill_spec（正确）与 db_audit（错漏）各写了一遍。
            # 2026-11-11 P3-1 收口：改调 expected_task_count（快照日 20261113），
            # 与 R12 的"真源"逐位一致；build_tasks 仍按真实今天跑，属执行侧。
            from fetch.backfill_spec import expected_task_count
            try:
                tasks, kind = expected_task_count(name, conf)
                if kind == "date" and tasks:
                    expect = [str(t) for t in tasks]
            except Exception as exc:
                print(f"[db.audit] expected_task_count 失败 {name}（退按日枚举）: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                expect = None
            if expect is None:      # 兜底：expected_task_count 不可用时退回按日枚举
                start = conf.get("start", "20160101")
                expect = [d for d in open_dates(start_date=start) if d <= today]
        elif mode in ("by_range", "by_period"):
            # ⚠ 2026-09-20 重构（"两个结构各管一摊"缺陷族第 7 次复发的根治）:
            #   此处原为**自行重写**的按年切分 / 季度枚举，未走单一真源
            #   → 新增的 `single_range` 配置被无视：shibor_lpr 已改为
            #   "一次拉全"（1 个任务），却仍被按年算成 14 个，**误报"还差 13"**。
            #   现统一改调 expected_task_count —— 它与 build_tasks 同源，
            #   且已处理 single_range、截断到今天、各 kind 的格式转换。
            #   **不要再在 db_audit 内新增任何自行实现的任务计算。**
            from fetch.backfill_spec import expected_task_count
            tasks, kind = expected_task_count(name, conf)
            if tasks is None:
                expect = None
            else:
                # ⚠ expected_task_count 统一返回**断点键**字符串格式:
                #   by_range → "20160101|20161231"；其余 → 日期/代码字符串。
                #   （注意与 build_tasks 的 by_range 不同 —— 后者返回 (s,e) 元组。
                #     此处按断点键比对，故用前者。）
                expect = [str(t) for t in tasks]
        elif mode in ("by_code", "by_month", "once"):
            # 2026-11-11 同上：by_month 的 month_last_trade_dates 在 build_tasks
            # 内不传 end（取 datetime.now()）⇒ 体检侧会按天漂移，故统一走真源。
            from fetch.backfill_spec import expected_task_count
            try:
                tasks, kind = expected_task_count(name, conf)
                if kind == "need_meta" or tasks is None:
                    expect = None
                else:
                    expect = [str(t) for t in tasks]
            except Exception as exc:
                print(f"[db.audit] expected_task_count 失败 {name}（应有数未知）: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                expect = None

        if expect is None:
            if verbose:
                print(f"  {name:18s} {mode:9s} {len(done):8d} {'?':>8s} "
                      f"{'?':>8s} {'?':>7s}  (应有数未知)")
            continue

        # ── 缺口计算（按**周期键**比对，消除滚动端点漂移）──
        #   见 _period_key docstring：周/月/年度的最后一个周期端点会逐日漂移，
        #   直接比对字符串会每天误报。改用周期键后，同一周期的不同端点视为同一任务。
        freq = conf.get("freq", "day")
        done_keys = {_period_key(d, mode, freq) for d in done}
        miss_keys = {_period_key(e, mode, freq) for e in expect} - done_keys
        miss = sorted({e for e in expect
                       if _period_key(e, mode, freq) in miss_keys})
        miss = [str(x) for x in miss]

        # ── 数据口径校验（仅 by_date）──
        # 断点会把"调用成功但返空"也记为完成，导致缺口被隐瞒。
        # 读 parquet 实际最大日期，如果落后于应有日期，说明断点虚记。
        data_stale = []
        if mode == "by_date" and not miss and not conf.get("audit_no_stale"):
            data_latest = _get_data_latest_date(name)
            expect_latest = expect[-1] if expect else None
            # ⚠ 排除"所属周期仍在进行中"的末尾任务（周/月线专用）：
            #   如 monthly 的末尾是"9 月月末该交易日"，而 9 月尚未结束，
            #   月末数据本就不存在 → 若不排除，每天都会误报一次"断点虚记"。
            skip_ongoing = _period_ongoing(expect_latest, freq, today)
            if data_latest is not None and not skip_ongoing:
                if expect_latest and data_latest < expect_latest:
                    # ⚠ 排除基准日当天：T 日数据源常尚未发布（两融/港股通当晚才更新），
                    #   断点记"完成"属合理，不应每天假报虚记。
                    #   只把"确已发布却未入库"的交易日算作虚记。
                    miss = sorted(d for d in expect if data_latest < d < today)
                    data_stale = miss

        if not miss and not data_stale:
            continue

        # ⚠ 完成率应基于**周期键匹配数**，而非断点条数（2026-09-20 修复）:
        #   断点可能记的是"旧任务键"（如 shibor_lpr 的 20160101|20161231），
        #   而当前应有任务是新键（20131001|20260920）→ len(done)=len(expect)=1
        #   会算出 100%，却又同时显示"还差 1"，自相矛盾。
        matched = max(len(expect) - len(miss), 0)
        ratio = matched / len(expect) * 100 if expect else 100
        out.append({"name": name, "mode": mode, "done": len(done),
                    "expect": len(expect), "missing": len(miss),
                    "category": _gap_category(name, conf),
                    "first": miss[0] if miss else "",
                    "last": miss[-1] if miss else "",
                    "data_stale": bool(data_stale),
                    "data_latest": data_latest if data_stale else None})
        if verbose:
            stale_tag = " ⚠断点虚记" if data_stale else ""
            cat = _gap_category(name, conf)
            # 真缺口（日更表）高亮，设计性差额与周期表用普通样式
            cat_tag = "★" + cat if cat == GAP_CAT_DAILY else " " + cat
            print(f"  {name:18s} {mode:9s} {len(done):8d} {len(expect):8d} "
                  f"{ratio:7.1f}% {len(miss):7d}  {cat_tag:4s}{stale_tag}")
            if data_stale and data_latest:
                print(f"    断点口径: 已完成, 数据口径: 最新={data_latest}")

    if verbose:
        if out:
            tot = sum(x["missing"] for x in out)
            by_cat = {}
            for x in out:
                c = x.get("category", "?")
                e = by_cat.setdefault(c, {"n": 0, "miss": 0, "bydate": 0})
                e["n"] += 1
                e["miss"] += x["missing"]
                if x["mode"] == "by_date":
                    e["bydate"] += x["missing"]
            daily_miss = by_cat.get(GAP_CAT_DAILY, {}).get("miss", 0)

            # ⚠ 顺序很重要：**先给真缺口结论**，再列设计性差额。
            #   原实现只打一个"合计"，让 47 个设计性差额掩盖了"真缺口=0"这一事实。
            if daily_miss:
                print(f"\n  ★ 真缺口（日更表）: {daily_miss:,} 个任务 —— **需补**")
            else:
                print("\n  ✓ 真缺口（日更表）: **0** —— 所有参与日更的接口均已到位")

            for cat, label in ((GAP_CAT_EXEMPT, "豁免表（刻意不日更）"),
                               (GAP_CAT_CYCLE, "周期表（周/月线，周期未结束）")):
                v = by_cat.get(cat)
                if v:
                    print(f"  ⓘ {label}: {v['n']} 个接口 / {v['miss']} 个差额"
                          f" —— **设计性差额，非缺口**（这些表不参与日更，差额会持续存在）")

            print(f"\n  合计差额: {tot:,} 个任务（其中真缺口 {daily_miss:,}）")
            if daily_miss:
                bd = by_cat.get(GAP_CAT_DAILY, {}).get("bydate", 0)
                if bd:
                    par = max(BACKFILL_PARALLEL_TARGETS, 1)
                    print(f"    其中 by_date 类 {bd:,} 个交易日请求"
                          f" (按 41 天/分钟 4 接口并行 ≈ {bd/41/par:.0f} 分钟)")
                print("  续跑命令: python main.py backfill --tier A,B")
            stale_count = sum(1 for x in out if x.get("data_stale"))
            if stale_count:
                print(f"  ⚠ 其中 {stale_count} 个接口断点虚记（调用成功但返空被记为完成），")
                print("    实际数据落后于断点记录。建议: python main.py gap-update --group all --lookback 5")
        else:
            print("  ✓ 无缺口（含日更表与豁免表）")
    return out


# ============================================================
# D. 规模统计
# ============================================================
def audit_scale(top_n=12, verbose=True):
    """统计数据库规模与磁盘占用。"""
    if verbose:
        print()
        print(SEP)
        print("  D. 规模统计")
        print(SEP)

    by_dir = {}
    tot_f = tot_b = 0
    root_files = 0
    for e in os.scandir(MARKET_DATA_DIR):
        if e.is_file() and e.name.endswith(".parquet"):
            root_files += 1
            continue
        if not e.is_dir():
            continue
        n = s = 0
        for dp, _dn, fn in os.walk(e.path):
            for x in fn:
                if x.endswith(".parquet"):
                    n += 1
                    try:
                        s += os.path.getsize(os.path.join(dp, x))
                    except Exception:
                        pass
        if n:
            by_dir[e.name] = (n, s)
            tot_f += n
            tot_b += s

    if verbose:
        print(f"  目录数: {len(by_dir)} | parquet 文件: {tot_f:,}" +
              (f" (+根目录 {root_files})" if root_files else ""))
        print(f"  总大小: {tot_b/1024**3:.2f} GB")
        try:
            import shutil
            _t, _u, fr = shutil.disk_usage(os.path.splitdrive(MARKET_DATA_DIR)[0] + "\\")
            print(f"  磁盘剩余: {fr/1024**3:.1f} GB")
        except Exception:
            pass
        print(f"\n  Top {top_n} 目录:")
        for k, (n, s) in sorted(by_dir.items(), key=lambda x: -x[1][1])[:top_n]:
            print(f"    {k:20s} {n:7,d} 文件  {s/1024**2:9.1f} MB")

    return {"dirs": len(by_dir), "files": tot_f, "bytes": tot_b, "by_dir": by_dir}


# ============================================================
# 主流程
# ============================================================
def _read_code_set(name):
    """读某表 parquet 现存的代码集合（只看代码列，不读全表）。"""
    d = os.path.join(MARKET_DATA_DIR, name)
    if not os.path.isdir(d):
        return None
    cols = ("ts_code", "symbol", "code")
    out = set()
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        print(f"[db.audit] 导入 pyarrow 失败: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None
    for f in os.listdir(d):
        if not f.endswith(".parquet"):
            continue
        try:
            names = pq.read_schema(os.path.join(d, f)).names
            col = next((c for c in cols if c in names), None)
            if not col:
                return None
            t = pq.read_table(os.path.join(d, f), columns=[col])
            out |= {str(x) for x in t.column(col).to_pylist() if x is not None}
        except Exception as exc:
            print(f"[db.audit] 读代码集失败 {f}（整表放弃）: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return None
    return out


def audit_data_check(only=None, verbose=True):
    """by_code 表**观测**：断点已完成、但库内零行的代码数。

    ⚠ 仅观测：不参与 n_issue / 退出码 / regress 判据（§314 C1）。
    合法返空很多（握手型接口、新上市标的），故先看量级，不立判据。
    """
    from config import BACKFILL_TARGETS
    from config import MARKET_DATA_DIR as _MD
    cp_path = os.path.join(_MD, ".backfill_checkpoint.json")
    if not os.path.exists(cp_path):
        return []
    cp = json.load(open(cp_path, encoding="utf-8"))

    rows = []
    for name, conf in BACKFILL_TARGETS.items():
        if conf.get("mode") != "by_code" or conf.get("audit_skip"):
            continue
        if only and name not in only:
            continue
        done = set(str(x) for x in (cp.get(name, {}).get("completed") or []))
        if not done:
            continue
        data = _read_code_set(name)
        if data is None:
            continue
        nodata = done - data
        rows.append({"table": name, "done": len(done), "with_data": len(done & data),
                     "done_no_data": len(nodata),
                     "sample": sorted(nodata)[:5]})
    rows.sort(key=lambda r: -r["done_no_data"])
    if verbose:
        print()
        print(SEP)
        print("  D. by_code 观测（断点已完成 / 库内零行；不进判据）")
        print(SEP)
        print(f"  {'接口':20s} {'断点完成':>8s} {'有数据':>8s} {'零行':>8s}  样例")
        print("  " + "-" * 76)
        for r in rows:
            print(f"  {r['table']:20s} {r['done']:8d} {r['with_data']:8d} "
                  f"{r['done_no_data']:8d}  {','.join(r['sample'])}")
        print(f"  合计: {len(rows)} 表 / 零行码 {sum(r['done_no_data'] for r in rows):,}")
    return rows


def run_audit(sections=("checkpoint", "dup", "truncation", "gap", "scale"),
              only=None, verbose=True, exact=False, as_json=False,
              data_check=False):
    """执行体检。exact=True 时无主键目录做全量整行判重(慢, 约18分钟)。

    as_json: stdout 输出结果 JSON（CLI --json；2026-09-30 P3 统一）
    """
    # GBK 控制台下 ⚠/★ 等符号会让 print 抛 UnicodeEncodeError（2026-10-11 实测：
    # 走管道/CI 捕获时 `db audit --section gap` **100% 崩**，退出码 1、体检中段
    # 丢失）——errors="replace" 让符号降级为 `?`，判定链与数字完全保真。
    # 同源处置见 tools/doctor.py::_harden_stdout。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    if not as_json:
        print("=" * 96)
        print("  全量数据库体检")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  存储: {MARKET_DATA_DIR}")
        print(f"  项目: {', '.join(sections)}")
        if "dup" in sections:
            print(f"  重复判重范围: {'全量(慢)' if exact else '抽样(快)'}")
        print("=" * 96)

    v = verbose and not as_json
    result = {}
    if "checkpoint" in sections:
        result["checkpoint"] = audit_checkpoint(v)
    if "dup" in sections:
        result["dup"] = audit_duplicates(only=only, verbose=v, exact=exact)
    if "truncation" in sections:
        result["truncation"] = audit_truncation(verbose=v)
    if "gap" in sections:
        result["gap"] = audit_gaps(v)
    if "scale" in sections:
        result["scale"] = audit_scale(verbose=v)
    if data_check:
        result["data_check"] = audit_data_check(only=only, verbose=v)

    if as_json:
        from common.jsonio import print_json
        out = dict(result)
        if "checkpoint" in out:
            errs, infos = out["checkpoint"]
            out["checkpoint"] = {"errors": errs, "infos": infos}
        # 与下方文本结论同一判据: 断点真异常 + 真重复 + 日更真缺口
        n_issue = 0
        if "checkpoint" in result:
            n_issue += len(result["checkpoint"][0])
        if "dup" in result:
            n_issue += len([r for r in result["dup"] if "⚠" in r["verdict"]])
        if "gap" in result:
            n_issue += len([x for x in result["gap"]
                            if x.get("category") == GAP_CAT_DAILY])
        out["n_issue"] = n_issue
        print_json(out, indent=1)
        return result

    print()
    print("=" * 96)
    print("  体检结论")
    print("=" * 96)
    n_issue = 0
    if "checkpoint" in result:
        errs, infos = result["checkpoint"]
        n_issue += len(errs)
        if errs:
            print(f"  断点一致性: ⚠ {len(errs)} 处真异常")
        else:
            print("  断点一致性: ✓ 正常" + (f" ({len(infos)} 条提示)" if infos else ""))
    if "dup" in result:
        b = [r for r in result["dup"] if "⚠" in r["verdict"]]
        n_issue += len(b)
        print(f"  真重复:     {'✓ 未发现' if not b else f'⚠ {len(b)} 个目录'}")
    if "truncation" in result:
        n_sus = sum(len(r["suspects"]) for r in result["truncation"])
        print(f"  截断残留:   {'✓ 无' if n_sus == 0 else f'⚠ 疑似 {n_sus} 只'}"
              f"（诊断项，不计入总体）")
    if "gap" in result:
        g = result["gap"]
        # ⚠ 只有**真缺口（日更表）**才计入「待关注」（2026-09-22 修复）：
        #   原实现用 len(g)，把"豁免表的设计性差额"也算成待关注项，
        #   于是体检结论长期显示「⚠ 12 项待关注」而真实缺口为 0 —— 狼来了。
        daily_g = [x for x in g if x.get("category") == GAP_CAT_DAILY]
        n_issue += len(daily_g)
        if not g:
            print("  缺口:       ✓ 无")
        elif not daily_g:
            miss_tot = sum(x["missing"] for x in g)
            print(f"  缺口:       ✓ 真缺口 0"
                  f"（另有 {len(g)} 个接口 / {miss_tot:,} 个**设计性差额**，见上方分类）")
        else:
            miss_tot = sum(x["missing"] for x in daily_g)
            print(f"  缺口:       ⚠ {len(daily_g)} 个**日更**接口待补, 合计 {miss_tot:,} 请求")
    if "scale" in result:
        s = result["scale"]
        print(f"  规模:       {s['files']:,} 文件 / {s['bytes']/1024**3:.2f} GB / {s['dirs']} 目录")
    print(f"\n  总体: {'✓ 健康' if n_issue == 0 else f'⚠ {n_issue} 项待关注'}")
    return result
