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
import os
import json
from datetime import datetime

import pandas as pd

import glob
from config import (
    MARKET_DATA_DIR, META_DIR, DB_AUDIT_PKEYS, DB_AUDIT_DATE_APIS,
    DB_AUDIT_CKPT_ALIASES, DB_AUDIT_CKPT_EXTERNAL, DB_AUDIT_UPSTREAM_DUP,
    BACKFILL_TARGETS, BACKFILL_PARALLEL_TARGETS, TABLE_DATE_COL,
    DAILY_UPDATE_GROUPS, DAILY_UPDATE_EXEMPT,
)
from common.calendar import open_dates
from common.reader import read_code

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


def _parquet_column_names(path):
    """只读 parquet **元数据**拿列名（不读数据本体，约 1ms）"""
    try:
        import pyarrow.parquet as pq
        return list(pq.ParquetFile(path).schema_arrow.names)
    except Exception:
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
    """
    d = os.path.join(MARKET_DATA_DIR, name)
    if not os.path.isdir(d):
        return None
    files = sorted(glob.glob(os.path.join(d, "*.parquet")))
    if not files:
        return None
    path = files[-1]
    try:
        date_col = TABLE_DATE_COL.get(name)
        if not date_col:
            names = _parquet_column_names(path)
            date_col = next((c for c in _DATE_COL_CANDIDATES if c in names), None)
        if not date_col:
            return None
        s = pd.read_parquet(path, columns=[date_col])[date_col]
        if not len(s):
            return None
        return str(s.max())
    except Exception:
        return None


# ============================================================
# A. 断点 × 落盘 交叉校验
# ============================================================
CHECKPOINT_FILES = [".checkpoint.json", ".cb_checkpoint.json", ".backfill_checkpoint.json"]


def audit_checkpoint(verbose=True):
    """检查所有断点文件与实际落盘的交叉一致性。

    判定分级(避免误报):
      异常(error): 断点>0 但目标目录不存在 / 无任何文件  ← 真的缺数据
      提示(info):  有文件但断点=0, 且该目录由其他流程填充 ← 正常
      提示(info):  断点键名经别名映射后才找到目录          ← 命名差异
      正常(OK):    其余
    """
    if verbose:
        print(SEP)
        print("  A. 断点 × 落盘 交叉校验")
        print(SEP)
        print("  异常 = 断点说完成但无文件; 提示 = 命名/来源差异(非故障)")

    errors, infos = [], []
    for cp_name in CHECKPOINT_FILES:
        cp_path = os.path.join(MARKET_DATA_DIR, cp_name)
        if not os.path.exists(cp_path):
            if verbose:
                print(f"\n  [{cp_name}] 不存在, 跳过")
            continue
        try:
            cp = json.load(open(cp_path, encoding="utf-8"))
        except Exception as e:
            errors.append((cp_name, f"断点文件损坏: {str(e)[:50]}"))
            print(f"\n  [{cp_name}] ⛔ 读取失败: {str(e)[:60]}")
            continue

        print(f"\n  [{cp_name}] {os.path.getsize(cp_path)/1024:.1f} KB | {len(cp)} 个键")
        print(f"  {'键名':22s} {'断点记录':>9s} {'实际文件':>9s}  状态")
        print("  " + "-" * 60)

        for name in sorted(cp):
            done = len(cp[name].get("completed", [])) if isinstance(cp[name], dict) else 0

            # 别名映射
            target = DB_AUDIT_CKPT_ALIASES.get(name, name)
            aliased = target != name
            d = os.path.join(MARKET_DATA_DIR, target)

            if not os.path.isdir(d):
                if done > 0:
                    st = "⛔ 断点有记录但目录不存在"
                    errors.append((f"{cp_name}:{name}", "断点有记录但目录不存在"))
                else:
                    st = "(目录不存在, 断点也为0) 跳过"
                nf = -1
            else:
                nf = len([f for f in os.listdir(d) if f.endswith(".parquet")])
                if done > 0 and nf == 0:
                    st = "⛔ 断点有记录但无文件"
                    errors.append((f"{cp_name}:{name}", "断点有记录但无文件"))
                elif nf > 0 and done == 0:
                    if target in DB_AUDIT_CKPT_EXTERNAL:
                        st = "提示: 由其他流程填入(非故障)"
                        infos.append((f"{cp_name}:{name}", "外部流程填充, 断点不记录"))
                    else:
                        st = "提示: 有文件但断点为0"
                        infos.append((f"{cp_name}:{name}", "有文件但断点为0"))
                else:
                    st = "OK"
            show = f"{name} → {target}" if aliased else name
            print(f"  {show:22s} {done:9d} {nf:9d}  {st}")

    if verbose:
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
    return errors, infos


# ============================================================
# B. 重复二分判定
# ============================================================
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
            except Exception:
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
        elif full_dup == 0:
            verd = "✓ 无真重复"
        elif pk_dup == 0:
            verd = "✓ 主键干净"
        elif name in DB_AUDIT_UPSTREAM_DUP:
            verd = f"ℹ 上游即有重复(抽样{full_dup:,})"
        else:
            verd = f"⚠ 有真重复(抽样{full_dup:,})"

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
        except Exception:
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
        except Exception:
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

    # 延迟导入, 复用 backfill 的任务构建逻辑(保证口径一致)
    try:
        from fetch.backfill import build_tasks
    except Exception:
        build_tasks = None

    if verbose:
        print(f"  基准日: {today}")
        print(f"\n  {'接口':18s} {'模式':9s} {'已完成':>8s} {'应有':>8s} "
              f"{'完成率':>8s} {'还差':>7s}  {'类别':4s}")
        print("  " + "-" * 76)

    out = []
    for name, conf in BACKFILL_TARGETS.items():
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            continue
        if conf["api"] in ("cn_cpi", "cn_ppi", "cn_m", "cn_pmi", "cn_gdp",
                           "sf_month", "hk_basic"):
            pass  # once 类照常处理
        mode = conf["mode"]
        done = set(cp.get(name, {}).get("completed", []))
        if not done and mode not in ("once",):
            continue

        expect = None
        if mode == "by_date":
            # ⚠ 必须复用 build_tasks（单一真源）—— 它处理了 freq 参数：
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
            if build_tasks is not None:
                try:
                    tasks, kind = build_tasks(name, conf, dry_run=True)
                    if kind == "date" and tasks:
                        expect = [str(t) for t in tasks]
                except Exception:
                    expect = None
            if expect is None:      # 兜底：build_tasks 不可用时退回按日枚举
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
            tasks, kind = expected_task_count(name, conf, today=today)
            if tasks is None:
                expect = None
            else:
                # ⚠ expected_task_count 统一返回**断点键**字符串格式:
                #   by_range → "20160101|20161231"；其余 → 日期/代码字符串。
                #   （注意与 build_tasks 的 by_range 不同 —— 后者返回 (s,e) 元组。
                #     此处按断点键比对，故用前者。）
                expect = [str(t) for t in tasks]
        elif mode in ("by_code", "by_month", "once"):
            if build_tasks is not None:
                try:
                    tasks, kind = build_tasks(name, conf, dry_run=True)
                    if kind == "need_meta":
                        expect = None
                    elif kind == "month":
                        expect = [f"{t[0]}|{t[1]}" for t in tasks]
                    elif kind == "code":
                        expect = [str(t) for t in tasks]
                    elif kind == "once":
                        expect = ["once"]
                except Exception:
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
        if mode == "by_date" and not miss:
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
                print(f"\n  ✓ 真缺口（日更表）: **0** —— 所有参与日更的接口均已到位")

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
                print(f"  续跑命令: python main.py backfill --tier A,B")
            stale_count = sum(1 for x in out if x.get("data_stale"))
            if stale_count:
                print(f"  ⚠ 其中 {stale_count} 个接口断点虚记（调用成功但返空被记为完成），")
                print(f"    实际数据落后于断点记录。建议: python main.py gap-update --group all --lookback 5")
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
        for dp, dn, fn in os.walk(e.path):
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
            t, u, fr = shutil.disk_usage(os.path.splitdrive(MARKET_DATA_DIR)[0] + "\\")
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
def run_audit(sections=("checkpoint", "dup", "gap", "scale"), only=None,
              verbose=True, exact=False):
    """执行体检。exact=True 时无主键目录做全量整行判重(慢, 约18分钟)。"""
    print("=" * 96)
    print("  全量数据库体检")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  存储: {MARKET_DATA_DIR}")
    print(f"  项目: {', '.join(sections)}")
    if "dup" in sections:
        print(f"  重复判重范围: {'全量(慢)' if exact else '抽样(快)'}")
    print("=" * 96)

    result = {}
    if "checkpoint" in sections:
        result["checkpoint"] = audit_checkpoint(verbose)
    if "dup" in sections:
        result["dup"] = audit_duplicates(only=only, verbose=verbose, exact=exact)
    if "gap" in sections:
        result["gap"] = audit_gaps(verbose)
    if "scale" in sections:
        result["scale"] = audit_scale(verbose=verbose)

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
            print(f"  断点一致性: ✓ 正常" + (f" ({len(infos)} 条提示)" if infos else ""))
    if "dup" in result:
        b = [r for r in result["dup"] if "⚠" in r["verdict"]]
        n_issue += len(b)
        print(f"  真重复:     {'✓ 未发现' if not b else f'⚠ {len(b)} 个目录'}")
    if "gap" in result:
        g = result["gap"]
        # ⚠ 只有**真缺口（日更表）**才计入「待关注」（2026-09-22 修复）：
        #   原实现用 len(g)，把"豁免表的设计性差额"也算成待关注项，
        #   于是体检结论长期显示「⚠ 12 项待关注」而真实缺口为 0 —— 狼来了。
        daily_g = [x for x in g if x.get("category") == GAP_CAT_DAILY]
        n_issue += len(daily_g)
        if not g:
            print(f"  缺口:       ✓ 无")
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
