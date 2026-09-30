# -*- coding: utf-8 -*-
"""
fetch/backfill.py — 通用补数引擎（执行编排层）
================================================
把「有权限但未入库」的 Tushare 接口批量补进全量库。
目标清单在 config.BACKFILL_TARGETS。

本模块由原 762 行单文件拆分而来，现分三层：
  fetch/backfill_spec.py  任务规格层 —— 该拉哪些数据（纯计算，不发请求）
  fetch/backfill_io.py    IO 层     —— 怎么取、怎么存
  fetch/backfill.py       本文件    —— 执行编排 + CLI

五种取数模式(mode):
  by_code   按标的拉全序列   → 参数 ts_code=<code>        → 布局由 TABLE_DATE_COL 定
  by_date   按日拉全市场     → 参数 trade_date=<交易日>   → 按年分区大表
  by_month  按月末           → <code_param> + trade_date  → 按年分区大表
  by_range  按年区间         → start_date/end_date        → 单文件
  by_period 按报告期精确匹配 → end_date=<报告期>          → 按年分区大表
  once      一次性全量       → 无参数                     → 单文件

用法:
  python main.py backfill                          # 跑 tier A+B（默认）
  python main.py backfill --dry-run                # 只估算请求数, 不发请求
  python main.py backfill --tier A                 # 只跑 A 档
  python main.py backfill --only dividend,margin   # 只跑指定接口
  python main.py backfill --limit 20               # 每接口限量（验证/分批）
  python main.py backfill --parallel-targets 4     # 同时跑几个接口
  python -m fetch.backfill --list                  # 列出目标清单

设计要点:
  - 复用 fetch.base.fetch_codes_parallel（每接口 4 线程, 按接口限频）
  - 落盘统一走 backfill_io.save_df（按年分区 / 按标的 / 单文件 三种布局）
  - by_date 用 YearBatchWriter 批量落盘（实测比逐天写入快 17 倍）
  - 断点按 target 名独立记录；**仅"调用成功"才记**，失败留待续跑
"""
import os
import sys
import time
import argparse
import threading
from datetime import datetime

import pandas as pd

from config import (
    MARKET_DATA_DIR, META_DIR, BACKFILL_TARGETS, BACKFILL_INDEX_CODES,
    BACKFILL_PAGED, BACKFILL_PAGE_CONF, FETCH_MAX_WORKERS, BACKFILL_PARALLEL_TARGETS,
    API_RATE_LIMIT, API_RATE_LIMIT_DEFAULT,
    BACKFILL_FLUSH_CONF, BACKFILL_FLUSH_DEFAULT,
)
from common.paths import ensure_dir
from common.calendar import open_dates
from common.parquet_store import (
    upsert_grouped, merge_append, upsert_by_year, YearBatchWriter,
)
from fetch.base import (
    get_pro, RateLimiter, Checkpoint,
    fetch_codes_parallel, ts_call_with_retry, ts_fetch_by_date,
)

# ── 从拆分后的兄弟模块 re-export，保持既有调用方（main.py / tools/db_audit.py /
#    历史脚本）的 import 路径不变 ──
from fetch.backfill_spec import (          # noqa: F401
    resolve_codes, month_last_trade_dates, month_list, year_ranges,
    report_periods, truncate_to_today, _truncate_to_today,
    build_tasks, estimate_requests, expected_task_count, DAILY_ROWS,
)
from fetch.backfill_io import (            # noqa: F401
    fetch_paged_by_date, save_df, ensure_meta,
    _save_df, _ensure_meta, _pick_sort_col,
    table_storage_layout, table_date_col,
    by_code_needs_batch, ByCodeBatchWriter, BY_CODE_BATCH_THRESHOLD_MB,
)

BACKFILL_DIR_ROOT = MARKET_DATA_DIR          # 各 target 在 market_data 下建同名目录


def _concat_pages(chunks):
    """合并分页结果并**全列去重**（分页拉取的统一收口）。

    为什么必须去重（2026-09-19 独立审查实证）:
      分页接口在翻页边界可能返回重复行（源端行为/offset 语义），而落盘路径
      `merge_append` 的**新建文件分支不判重** —— 首次建库时重复会被直接固化。
      实测：us_basic 24,256 行含 1 处真重复、slb_len_mm 55,214 行含 34 行真重复。

    为什么用**全列**去重而不是按 subset（关键，勿改）:
      只删"每一列都完全相同"的行，绝不会误删业务多行。
      若按 subset 去重则危险 —— 实测 us_basic 配置的 subset=['ts_code']，
      但该键有 55 处不重复的行（如 ABX 键下 2 行内容不同），按 subset 去重
      会静默删掉 54 行**非冗余**数据。
      本口径与 `backfill_io.fetch_paged_by_date` 一致（那里也是 drop_duplicates()）。
    """
    out = pd.concat(chunks, ignore_index=True)
    return out.drop_duplicates().reset_index(drop=True)



def run_target(pro, name, conf, cp, workers, dry_run=False, verbose=False, limit=None,
               date_override=None, ignore_checkpoint=False):
    """执行单个 target。

    date_override:    显式指定 by_date 的待跑日期列表（每日增量用）。给定时
                      不再走 build_tasks（避免"从 start 到今天"的全量日期列表）。
    ignore_checkpoint: 忽略断点、全部重跑。每日增量对"复核窗口"内的日期需要
                      强制重拉（断点说完成也要重拉 —— 那正是为了修复
                      "断点已记但当日数据只有 130 行"这类残缺），故必须提供。
                      落盘走主键去重, 重拉幂等。
    """
    mode = conf["mode"]
    desc = conf.get("desc", "")
    target_dir = os.path.join(BACKFILL_DIR_ROOT, name)
    rate = API_RATE_LIMIT.get(conf["api"], API_RATE_LIMIT_DEFAULT)
    limiter = RateLimiter()

    if date_override is not None:
        tasks, kind = list(date_override), "date"
    else:
        tasks, kind = build_tasks(name, conf, dry_run)

    # 前置基础表 (fx_daily 需要 fx_obasic, hk_daily 需要 hk_basic)
    if kind == "need_meta":
        need = conf.get("codes")
        if _ensure_meta(pro, limiter, need, dry_run):
            tasks, kind = build_tasks(name, conf, dry_run)
        if kind == "need_meta":
            print(f"  [{name}] 跳过: 前置基础表不可用 (codes={need})")
            return None

    # 过滤已完成: 断点已标记 或 (by_code) 目标文件已存在 → 跳过
    if ignore_checkpoint:
        pending = list(tasks)
    elif kind in ("month", "range"):
        pending = [t for t in tasks if not cp.is_done(name, f"{t[0]}|{t[1]}")]
    elif kind == "code":
        pending = []
        if table_storage_layout(name) == "by_year":
            # ⚠ 按年分区下 "{code}.parquet" 恒不存在，不能再用文件存在性判断
            #   （否则"断点丢失时补记"完全失效，每次续跑重拉全部标的）。
            #   改为一次性读出库内已有代码集合，再逐标的比对——
            #   O(文件数) 一次，而非 O(标的数 × 文件数)。
            from common import reader
            try:
                present = set(reader.codes_from_store(
                    name, root=BACKFILL_DIR_ROOT))
            except Exception:
                present = set()
            for t in tasks:
                if cp.is_done(name, str(t)):
                    continue
                if str(t) in present:
                    if not dry_run:
                        cp.mark_done(name, str(t))    # 断点丢失时补记
                    continue
                pending.append(t)
        else:
            for t in tasks:
                if cp.is_done(name, str(t)):
                    continue
                if os.path.exists(os.path.join(target_dir, f"{t}.parquet")):
                    if not dry_run:
                        cp.mark_done(name, str(t))    # 断点丢失时补记
                    continue
                pending.append(t)
    else:
        pending = [t for t in tasks if not cp.is_done(name, str(t))]
    skipped = len(tasks) - len(pending)
    if limit:
        pending = pending[:limit]
    est = estimate_requests(pending, conf)

    print(f"\n{'─' * 66}")
    print(f"  [{name}] {desc} | 模式={mode} | 限频={rate}/min | 并发={workers}")
    print(f"  任务数={len(tasks)} 待跑={len(pending)} 跳过={skipped} "
          f"| 预估请求≈{est} | 预估耗时≈{est/max(rate,1):.1f}min"
          + (f" | 限量={limit}" if limit else ""))
    print(f"{'─' * 66}")

    if dry_run or not pending:
        return {"name": name, "est": est, "done": 0}

    t0 = time.time()
    done_ct = {"n": 0}

    # ── 批量落盘（仅对大文件表启用）──
    # 背景：by_code 模式下每标的调用一次 save_df(layout="by_year")，
    #       而 upsert_by_year 每次都对整个年度文件做读改写，
    #       成本随文件线性增长。实测 share_float（2026 文件 8MB）单次 364ms，
    #       5882 标的累计 35.7 分钟 > 限频下限 34.6 分钟 → 落盘成真瓶颈。
    #       其余 22 个 by_code 表的落盘被限频完全掩盖，故不启用（避免无谓复杂度）。
    _batch = None
    if (mode == "by_code" and not date_override
            and by_code_needs_batch(name, target_dir)):
        _batch = ByCodeBatchWriter(
            target_dir,
            date_col=table_date_col(name),
            subset=conf.get("subset") or "*",
            sort_by=table_date_col(name),
            flush_codes=conf.get("batch_flush_codes", 500),
            flush_rows=conf.get("batch_flush_rows", 500_000),
            on_flushed=lambda keys: [cp.mark_done(name, str(k)) for k in keys],
            on_error=lambda _y, e: print(f"  [ERROR] 批量落盘 {name}: {str(e)[:70]}"),
        )
        print(f"  [批量落盘] {name} 年度文件已超 "
              f"{BY_CODE_BATCH_THRESHOLD_MB}MB，启用累积落盘")

    def on_result(key, df, ok):
        """by_code 落盘回调(主线程串行执行)。

        ⚠ 存储布局由 TABLE_DATE_COL 决定，**不再由 mode 决定**：
          mode="by_code" 只说明"按标的逐个调接口"，不代表要写 {code}.parquet。
          17 个表(如 dividend / fund_adj / shareholder 类)磁盘已迁为按年分区，
          若这里仍写 {code}.parquet，会重新制造混合布局。

        断点纪律:
          ok=True 且非空 → 落盘 + 记断点
          ok=True 但为空 → **也记断点**（该标的确实无数据, 如未覆盖的报告期）
                           否则每次续跑都会重拉这批空结果
          ok=False(调用异常) → 不记断点, 留待下次续跑, 避免静默缺失

        ⚠ 批量模式下的断点时机:
          断点由 _batch 的 on_flushed 回调在**落盘成功后**统一标记，
          故此处不再直接 mark_done —— 保证"先落盘、后记断点"。
        """
        if ok and df is not None and not df.empty:
            ensure_dir(target_dir)
            dc = table_date_col(name)
            if dc and dc in df.columns:
                if _batch is not None:
                    _batch.add(df, str(key))         # 累积，落盘后由回调记断点
                else:
                    # ⚠ 2026-09-22（P0）: 落盘失败（含"部分年份分区未写入"）
                    #   不得记断点，否则该标的数据永不重拉。
                    try:
                        save_df(target_dir, key, df, layout="by_year", date_col=dc)
                    except Exception as e:
                        print(f"  [ERROR] {name} {key} 落盘失败，不记断点: {str(e)[:70]}")
                    else:
                        cp.mark_done(name, str(key))
            else:
                # 无分区列 → 按标的单文件（仅 stock_company 这类静态元数据）
                try:
                    df.to_parquet(os.path.join(target_dir, f"{key}.parquet"), index=False)
                except Exception as e:
                    print(f"  [ERROR] {name} {key} 写文件失败，不记断点: {str(e)[:70]}")
                else:
                    cp.mark_done(name, str(key))
        elif ok:
            # 调用成功但无数据 → 属"确实没有", 记断点避免重复请求
            cp.mark_done(name, str(key))
        done_ct["n"] += 1
        if done_ct["n"] % 200 == 0:
            cp.save()

    def progress(done, tot, stat, elapsed):
        r = done / elapsed * 60 if elapsed > 0 else 0
        eta = (tot - done) / r if r > 0 else 0
        print(f"    {name} {done}/{tot} | 成功={stat['ok']} 空={stat['empty']} "
              f"失败={stat['fail']} | {r:.0f}/min | 剩余≈{eta:.0f}min")

    if conf["mode"] == "once":
        df = ts_call_with_retry(pro, conf["api"], limiter, {}, verbose=verbose)
        # ⚠ 2026-09-22 修复（P0）: 断点必须"先落盘、后记"。
        #   原实现无论 df 是否为 None 都 cp.mark_done —— 与模块 docstring
        #   「仅"调用成功"才记，失败留待续跑」直接矛盾。
        #   后果: 调用失败(限频烧穿 / 无权限 / 网络全失败)时同样落断点，
        #        而 once 模式无其它完成度信号(不落文件、audit 只能看到目录不存在)
        #        → 该表永远不会被重拉。
        if df is None:
            print(f"    {name} 调用失败，不记断点，留待续跑")
            return {"name": name, "est": est, "done": 0}
        n = 0
        if not df.empty:
            ensure_dir(target_dir)
            df.to_parquet(os.path.join(target_dir, f"{conf['api']}.parquet"), index=False)
            n = len(df)
            print(f"    {name} → {n} 行已保存")
        # 调用成功（含"确实返空"）才记断点
        cp.mark_done(name, "once")
        cp.save()
        return {"name": name, "est": est, "done": n}

    if conf["mode"] == "by_period":
        # 按报告期精确匹配拉取(disclosure_date 专用)
        pm = conf.get("period_param", "end_date")
        dc = conf.get("date_col", "end_date")
        cap = conf.get("cap", 0)          # 该接口的单次返回上限, 用于截断告警
        truncated = []
        for i, per in enumerate(pending, 1):
            # ⚠ 不要在此 wait —— ts_call_with_retry 内部已按同速率 wait（第365行），
            #   外层再 wait 会**双重计数**：预约式限频器会连续占用两个槽位，
            #   使实际速率恒为配置值的一半（实证补库 85天/min，配置 170/min）；
            #   低限频接口更会直接阻塞：shibor_lpr(0.02/min, interval=3000s)
            #   第二次 wait 会睡满 50 分钟。
            df = ts_call_with_retry(pro, conf["api"], limiter,
                                    {pm: per}, verbose=verbose)
            nrow = 0 if df is None else len(df)
            if df is not None and not df.empty:
                if cap and nrow >= cap:
                    truncated.append((per, nrow))
                    # 顶到上限 → 尝试分页补齐 (若该接口在分页表中)
                    if conf["api"] in BACKFILL_PAGED:
                        # ⚠ 必须传该接口的真实入参名 pm（2026-09-19）:
                        #   disclosure_date 用 end_date；top10_holders 用 period。
                        #   默认写死 trade_date 会让参数被静默忽略
                        #   → 每页返回同样的数据（看起来"分页了"但全是重复）。
                        df2, _, _ = fetch_paged_by_date(pro, conf["api"], limiter,
                                                        per, verbose=verbose,
                                                        date_param=pm)
                        if df2 is not None and len(df2) > nrow:
                            df = df2
                            truncated[-1] = (per, f"{nrow}→{len(df2)}")
                _save_df(target_dir, conf["api"], df, layout="by_year",
                         subset=conf.get("subset"), date_col=dc)
            if df is not None:      # 仅调用成功才记断点
                cp.mark_done(name, per)
            print(f"    {name} {i}/{len(pending)} | 报告期={per} | {len(df) if df is not None else 0}行")
        cp.save()
        if truncated:
            print(f"    [警告] {len(truncated)} 个报告期触及上限 {cap}: {truncated[:5]}")
        return {"name": name, "est": est, "done": len(pending)}

    if conf["mode"] == "by_param":
        # 按枚举参数逐个拉取 + offset 分页（fut_basic / opt_basic 专用）
        #
        # 为什么需要这个模式:
        #   这两个表既没有可用的日期维度，也不是"按标的逐个调"的形态：
        #     fut_basic 无参数调用返 10000 行（实测按交易所合计 11275）→ 静默截断
        #     opt_basic 无参数/按交易所单次返 12000 行 → 静默截断；
        #               且 trade_date 参数被**静默忽略**
        #               （实测 20260918 与 20200102 返回完全相同的 12000 行）
        #   唯一可靠的切分维度是 exchange，配合 limit/offset 分页。
        pn = conf["param_name"]
        page = conf.get("page_size", 2000)
        max_pages = conf.get("max_pages", 100)
        for i, val in enumerate(pending, 1):
            chunks, offset, pages = [], 0, 0
            first_ok = False
            truncated = False
            while pages < max_pages:
                # 同上：wait 由 ts_call_with_retry 内部保证，勿重复
                df = ts_call_with_retry(pro, conf["api"], limiter,
                                        {pn: val, "limit": page, "offset": offset},
                                        verbose=verbose)
                pages += 1
                if pages == 1:
                    first_ok = df is not None
                if df is None or df.empty:
                    break
                chunks.append(df)
                if len(df) < page:      # 最后一页
                    break
                offset += page
            else:
                truncated = True        # 触及页数上限仍满页
            nrow = sum(len(c) for c in chunks)
            if chunks:
                merged = _concat_pages(chunks)      # 分页去重（见 _concat_pages）
                _save_df(target_dir, conf["api"], merged, layout="single",
                         subset=conf.get("subset"))
            if first_ok:                # 调用成功(含"确实无数据")→ 记断点
                cp.mark_done(name, val)
            flag = "  [警告] 页数触顶, 可能不完整" if truncated else ""
            print(f"    {name} {i}/{len(pending)} | {pn}={val} | "
                  f"{nrow}行 | 页={pages}{flag}")
        cp.save()
        return {"name": name, "est": est, "done": len(pending)}

    if conf["mode"] == "paged":
        # 按 limit/offset 逐页遍历（全量元数据表：us_basic / fund_manager 等）
        #
        # 为什么需要它:
        #   这类表既无日期维度、也无"枚举值"可切分，只有 offset 能翻页。
        #   单次调用会被静默截断（实测 us_basic 返 6000、真实 24256）。
        #
        # 断点三态（与 by_param 一致）:
        #   首行调用成功即记断点（包括"该页为空"——那是正常的末页）
        #   调用失败（None）不记，留待续跑
        page = conf.get("page_size", 5000)
        chunks = []
        done_pages = []                    # 暂存成功页号，落盘成功后才记断点
        for i, pg in enumerate(pending, 1):
            # 同上：wait 由 ts_call_with_retry 内部保证，勿重复
            df = ts_call_with_retry(pro, conf["api"], limiter,
                                    {"limit": page, "offset": pg * page},
                                    verbose=verbose)
            if df is None:                     # 调用失败: 不记断点
                print(f"    {name} 第{pg}页 调用失败，留待续跑")
                continue
            nrow = len(df)
            done_pages.append(str(pg))         # 成功（含空页）→ 暂存，不立即记
            if nrow:
                chunks.append(df)
            print(f"    {name} {i}/{len(pending)} | offset={pg*page} | {nrow} 行")
            if nrow < page:                    # 不足一页 = 已到末尾
                for rest in pending[i:]:       # 剩余页不可能有数据，一并暂存
                    done_pages.append(str(rest))
                break
        # ⚠ 2026-09-22 修复（P0）: 断点必须在**落盘成功之后**才记。
        #   原实现在循环内即 mark_done，而落盘在循环之后 —— 一旦在两者之间
        #   中断（Ctrl+C / 落盘异常 / OOM / 断电），断点文件已记为"全部完成"
        #   而磁盘无数据，下次 is_done 全部命中 → 静默跳过，数据永久缺失且无告警。
        #   同文件 by_period / by_range / by_date / by_code 均为"先落盘后记"，
        #   仅 paged / once 相反，属遗漏。
        if chunks:
            try:
                merged = _concat_pages(chunks)      # 分页去重（见 _concat_pages）
                _save_df(target_dir, conf["api"], merged, layout="single",
                         subset=conf.get("subset"))
                print(f"    [落盘] {name} 共 {len(merged):,} 行")
            except Exception as e:
                print(f"    [ERROR] {name} 落盘失败，断点未记，留待续跑: {str(e)[:80]}")
                cp.save()
                return {"name": name, "est": est, "done": 0}
        for k in done_pages:               # 落盘成功后统一记断点
            cp.mark_done(name, k)
        cp.save()
        return {"name": name, "est": est, "done": len(pending)}

    if conf["mode"] == "by_range":
        sp = conf.get("start_param", "start_date")
        ep = conf.get("end_param", "end_date")
        for i, (s, e) in enumerate(pending, 1):
            # 同上：wait 由 ts_call_with_retry 内部保证，勿重复
            df = ts_call_with_retry(pro, conf["api"], limiter,
                                    {sp: s, ep: e}, verbose=verbose)
            nrow = 0 if df is None else len(df)
            if df is not None and not df.empty:
                _save_df(target_dir, conf["api"], df, layout="single",
                         subset=conf.get("subset"))
            if df is not None:      # 仅调用成功才记断点, 失败留待续跑
                # ⚠ 日更场景（date_override 非空）**不记断点**（2026-09-21）:
                #   by_range 的断点键是 "start|end"，而日更的 end 端点每天漂移
                #   → 会积累大量一次性条目（每年约 365 条/表）。
                #   日更本身是幂等的（每天重算缺口、落盘按主键去重），不依赖断点。
                if date_override is None:
                    cp.mark_done(name, f"{s}|{e}")
            print(f"    {name} {i}/{len(pending)} | {s}~{e} | {nrow}行")
        cp.save()
        return {"name": name, "est": est, "done": len(pending)}

    if conf["mode"] == "by_date":
        # 2026-09-17 优化: "按日并发 + 按年批量落盘"
        #
        # 优化前: 每天串行 → 网络往返; 且每天落盘一次, 对整年文件做
        #         read→concat→dedup→write。实测落盘成本随文件线性增长
        #         (moneyflow 82万行时单次 1.49s), 成为主瓶颈(网络的3.7倍),
        #         整体只有 33~37 天/min。
        # 优化后: worker 线程并发拉取(含该日 offset 分页);
        #         主线程累积到 YearBatchWriter, 累积 N 天后一次写入,
        #         把 N 次 O(文件大小) 的读改写降为 1 次。
        # 断点纪律: 批量落盘成功后才记断点(on_flushed 回调), 保证
        #         "先落盘、后记断点", 崩溃不会造成"断点说完成但没数据"。
        # ⚠ date_param 与 date_col 是两个不同的概念（2026-09-19 区分）:
        #   date_param = **API 入参名**（怎么请求）—— 多数接口是 "trade_date"，
        #                但 fund_div 用 "ann_date"、eco_cal 用 "date"
        #   date_col   = **返回字段名**（怎么分区）—— 多数是 "trade_date"，
        #                但 bc_otcqt 返回的是大写 "TRADE_DATE"
        #   混用会造成"参数被静默忽略、返回全表前N行"或"分区列不存在"。
        dp = conf.get("date_param", "trade_date")
        dc = conf.get("date_col") or "trade_date"
        paged = conf["api"] in BACKFILL_PAGED
        meta = {}              # {日期: (页数, 是否触顶)}
        meta_lock = threading.Lock()
        flush_conf = BACKFILL_FLUSH_CONF.get(conf["api"], BACKFILL_FLUSH_DEFAULT)

        writer = YearBatchWriter(
            target_dir,
            date_col=dc,
            subset=conf.get("subset") or "*",
            sort_by=dc,
            flush_days=flush_conf.get("days", 20),
            flush_rows=flush_conf.get("rows", 500_000),
            on_flushed=lambda keys: [cp.mark_done(name, k) for k in keys],
            on_error=lambda y, e: print(f"  [ERROR] 批量落盘 {y}: {str(e)[:70]}"),
        )

        # API 额外固定参数 —— 2026-09-20 修复:
        #   引擎的 freq（决定"日期粒度"：week/month）与接口的 freq 参数**语义一致**，
        #   故复用同一处配置（单一真源），避免又在两处各写一份而漏改。
        #   实证: stk_weekly_monthly / stk_week_month_adj / fut_weekly_monthly 的
        #         freq 是**必填**；漏传 → 每次调用抛"必填参数, freq" → 走常规退避
        #         重试(1/2/5/10/20s)，表现为「全返空 + 极慢(12天/min，正常170)」，
        #         不修会白烧 547×3 次请求且一条数据都拿不到。
        api_extra = {}
        if conf.get("freq") in ("week", "month"):
            api_extra["freq"] = conf["freq"]

        def fetch_fn(_pro, _lim, dt):
            if paged:
                df, pg, trunc = fetch_paged_by_date(_pro, conf["api"], _lim, dt,
                                                    verbose=verbose, date_param=dp)
                with meta_lock:
                    meta[dt] = (pg, trunc)
                return df
            # 同上：wait 由 ts_call_with_retry 内部保证，勿重复
            params = {dp: dt}
            params.update(api_extra)
            return ts_call_with_retry(_pro, conf["api"], _lim,
                                      params, verbose=verbose)

        def on_result(dt, df, ok):
            if df is None:
                return              # 调用失败: 不落盘、不记断点, 留待续跑
            if df.empty:
                cp.mark_done(name, dt)   # 成功但无数据(如休市日): 记断点
            else:
                writer.add(df, dt)       # 累积; 内部 flush 成功后才记断点
            done_ct["n"] += 1

        def progress(done, tot, stat, elapsed):
            r = done / elapsed * 60 if elapsed > 0 else 0
            eta = (tot - done) / r if r > 0 else 0
            with meta_lock:
                pg_sum = sum(v[0] for v in meta.values())
            extra = f" | 页数={pg_sum}" if paged else ""
            print(f"    {name} {done}/{tot} | 成功={stat['ok']} 空={stat['empty']} "
                  f"失败={stat['fail']} | {r:.0f}天/min | 剩余≈{eta:.0f}min{extra}")

        stat = fetch_codes_parallel(
            pro, conf["api"], pending, limiter,
            fetch_fn=fetch_fn, max_workers=workers,
            on_result=on_result, progress=progress,
            progress_every=50, verbose=verbose,
        )
        writer.flush()          # 收尾: 落盘剩余缓冲
        cp.save()

        with meta_lock:
            trunc_days = [d for d, v in meta.items() if v[1]]
        if trunc_days:
            print(f"    [警告] {len(trunc_days)} 天页数触顶, 可能仍不完整: "
                  f"{trunc_days[:5]}")
        ws = writer.stat()
        print(f"    [落盘] 批量写入 {ws['frames']} 次, 共 {ws['rows']:,} 行 "
              f"(对比逐天写入约 {len(pending)} 次)")
        return {"name": name, "est": est, "done": stat["ok"]}

    # by_code / by_month: 复用并发框架
    if conf["mode"] == "by_month":
        code_param = conf["code_param"]
        date_key = "month" if code_param == "month" else "trade_date"

        def fetch_fn(_pro, _lim, pair):
            code, dval = pair
            params = {}
            if code is not None:
                params[code_param] = code
            if date_key == "month":
                params["month"] = dval[:6]
            else:
                params["trade_date"] = dval
            return ts_call_with_retry(_pro, conf["api"], _lim, params, verbose=verbose)

        def on_result_month(pair, df, ok):
            _save_df(target_dir, conf["api"], df, layout="single",
                     subset=conf.get("subset"))
            if ok:                  # 仅调用成功才记断点
                cp.mark_done(name, f"{pair[0]}|{pair[1]}")
            done_ct["n"] += 1
            if done_ct["n"] % 50 == 0:
                cp.save()

        stat = fetch_codes_parallel(pro, conf["api"], pending, limiter,
                                    fetch_fn=fetch_fn, max_workers=workers,
                                    on_result=on_result_month, progress=progress,
                                    progress_every=50, verbose=verbose)
        cp.save()
        return {"name": name, "est": est, "done": stat["ok"]}

    stat = fetch_codes_parallel(pro, conf["api"], pending, limiter,
                                max_workers=workers, max_rows=conf.get("max_rows", 0),
                                on_result=on_result, progress=progress,
                                progress_every=100, verbose=verbose)
    if _batch is not None:
        _batch.flush()          # 收尾：落盘剩余缓冲（落盘后才记断点）
        bs = _batch.stat()
        print(f"    [批量落盘] {bs['frames']} 次写入, 共 {bs['rows']:,} 行 "
              f"(对比逐标的写入约 {len(pending)} 次)")
    cp.save()
    return {"name": name, "est": est, "done": stat["ok"]}


# ============================================================
# 主流程
# ============================================================
def _banner(msg):
    print("=" * 66)
    print(f"  {msg}")
    print("=" * 66)


def list_targets():
    """列出全部补数目标（接口/档位/模式/布局/完成度/每日更新）。

    与 db-clean --list / db-audit --list / db-migrate --list 保持一致：
    让"这个库有哪些数据、怎么拉的、拉没拉全"一屏可见。
    """
    import json as _json
    from config import DAILY_UPDATE_GROUPS, DAILY_UPDATE_EXEMPT
    from fetch.backfill_io import table_storage_layout

    # 每日更新组归属
    grp = {}
    for g, members in DAILY_UPDATE_GROUPS.items():
        for m in members:
            grp[m] = g

    # 断点完成度
    cp_path = os.path.join(MARKET_DATA_DIR, ".backfill_checkpoint.json")
    cp = {}
    if os.path.exists(cp_path):
        try:
            cp = _json.load(open(cp_path, encoding="utf-8"))
        except Exception:
            cp = {}

    _banner("backfill 补数目标清单")
    print(f"  存储: {MARKET_DATA_DIR}")
    print(f"  目标数: {len(BACKFILL_TARGETS)}")
    print()
    print(f"  {'接口':20s} {'档':3s} {'模式':9s} {'布局':8s} "
          f"{'断点':>8s} {'每日更新':10s} 说明")
    print("  " + "-" * 92)

    for name, conf in sorted(BACKFILL_TARGETS.items(),
                             key=lambda kv: (str(kv[1].get("tier", "Z")), kv[0])):
        tier = conf.get("tier", "-")
        mode = conf.get("mode", "?")
        try:
            layout = table_storage_layout(name)
        except Exception:
            layout = "?"
        done = len(cp.get(name, {}).get("completed", []))
        # 每日更新归属（语义要区分"不适用"与"漏登"）
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            daily = "⛔死结"
        elif name in grp:
            daily = grp[name]
        elif name in DAILY_UPDATE_EXEMPT:
            daily = "豁免"
        elif conf.get("mode") == "by_date" and conf.get("freq", "day") == "day":
            daily = "⚠漏登"          # 日频却无归宿 —— 异常（R10 会拦）
        elif conf.get("mode") == "by_date":
            daily = "周/月线"        # freq != day，机制上不属日更
        else:
            daily = "n/a"            # 非日更形态（by_code/once/paged 等），本就不适用
        desc = conf.get("desc", "")[:20]
        print(f"  {name:20s} {str(tier):3s} {mode:9s} {layout:8s} "
              f"{done:8,d} {daily:10s} {desc}")

    print("  " + "-" * 92)
    print(f"  每日更新组: " +
          " / ".join(f"{g}={len(m)}" for g, m in DAILY_UPDATE_GROUPS.items()))
    print(f"  豁免清单: {len(DAILY_UPDATE_EXEMPT)} 项（已在 config 注明原因）")
    print(f"\n  提示: 断点数为 0 但不代表未拉 —— once/single 类可能已完成（见 db-audit）")
    print(f"        数据新鲜度与缺口请用: python main.py freshness / db-audit --section gap")


def run_backfill(tiers=("A", "B"), only=None, dry_run=False, workers=None,
                 verbose=False, parallel_targets=None, limit=None, force=False):
    if workers is None:
        workers = FETCH_MAX_WORKERS
    if parallel_targets is None:
        parallel_targets = BACKFILL_PARALLEL_TARGETS

    _banner("通用补数 — backfill")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  并发: 每接口 {workers} 线程 | 同时跑 {parallel_targets} 个接口 "
          f"→ 总线程 ≈ {workers * max(parallel_targets, 1)}")
    print(f"  限频: 按接口独立(实测按接口计, 多接口可叠加)")
    print(f"  存储: {os.path.abspath(BACKFILL_DIR_ROOT)}")
    print(f"  模式: {'DRY-RUN (只估算, 不发请求)' if dry_run else '正式拉取'}")
    if force:
        print("  ⚠ --force: 忽略断点与「库内已有标的」，全部重拉"
              "（用于修复「断点说完成但数据不完整」的历史遗留）")

    # 选中目标
    sel = []
    for name, conf in BACKFILL_TARGETS.items():
        if only and name not in only:
            continue
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            if not only:
                continue
        if not only and conf.get("tier") and conf["tier"] not in tiers:
            continue
        sel.append((name, conf))

    if not sel:
        print("\n  没有匹配的目标")
        return

    total_est = 0
    print(f"\n[选中目标] {len(sel)} 个")
    for name, conf in sel:
        print(f"  {name:20s} {conf.get('desc',''):24s} tier={conf.get('tier','-')}")

    if dry_run:
        print(f"\n{'-' * 66}\n  请求数估算\n{'-' * 66}")
        rows = []
        for name, conf in sel:
            try:
                tasks, kind = build_tasks(name, conf, dry_run=True)
                if kind == "need_meta":
                    print(f"  {name:20s} 跳过(需先拉前置基础表)")
                    continue
                est = estimate_requests(tasks, conf)
            except Exception as e:
                print(f"  {name:20s} [估算失败] {type(e).__name__}: {str(e)[:60]}")
                continue
            rate = API_RATE_LIMIT.get(conf["api"], API_RATE_LIMIT_DEFAULT)
            total_est += est
            # ⚠ rate 可能是小数（低限频接口如 shibor_lpr/shibor_quote=0.02/分钟）:
            #   ① 用 :3d 会因 float 抛 ValueError（dry-run 直接崩溃）
            #   ② max(rate, 1) 会把"每 50 分钟一次"误算成"每分钟一次"，
            #      严重低估工期（实证: shibor_lpr 11 个请求被算成 0.1min，
            #      实际需 11 小时）
            eta_min = est / rate if rate > 0 else 0.0
            rows.append((name, len(tasks), est, rate, eta_min))
            print(f"  {name:20s} 任务={len(tasks):6d} 请求≈{est:6d} "
                  f"限频={rate:g}/min 耗时≈{eta_min:6.1f}min")
        serial_min = sum(r[4] for r in rows)
        par = max(parallel_targets, 1)
        print(f"\n  合计: 请求≈{total_est} 次 | 接口数={len(rows)}")
        print(f"  串行等效耗时 ≈ {serial_min:.0f} 分钟 ({serial_min/60:.1f} 小时)")
        print(f"  按 {par} 接口并行 ≈ {serial_min/par:.0f} 分钟 "
              f"({serial_min/par/60:.1f} 小时) — 上限受最慢接口 "
              f"{max((r[4] for r in rows), default=0):.0f} 分钟约束")
        return

    pro = get_pro()
    print("\n[OK] Tushare Pro API 已连接")
    cp = Checkpoint(MARKET_DATA_DIR, filename=".backfill_checkpoint.json")

    results = []
    t_all = time.time()
    try:
        if parallel_targets and parallel_targets > 1:
            # 多接口并行: 每个 target 一个外层线程, target 内部再开 workers 个线程
            from concurrent.futures import ThreadPoolExecutor, as_completed
            print(f"\n[并行模式] {parallel_targets} 个接口同时拉取"
                  + (f" | 每接口限量 {limit}" if limit else "") + "\n")
            with ThreadPoolExecutor(max_workers=parallel_targets) as ex:
                futs = {ex.submit(run_target, pro, n, c, cp, workers, False, verbose, limit,
                                  None, force): n
                        for n, c in sel}
                for fut in as_completed(futs):
                    n = futs[fut]
                    try:
                        r = fut.result()
                        if r:
                            results.append(r)
                    except Exception as e:
                        print(f"  [ERROR] {n}: {str(e)[:120]}")
        else:
            for name, conf in sel:
                try:
                    r = run_target(pro, name, conf, cp, workers,
                                   dry_run=False, verbose=verbose, limit=limit,
                                   ignore_checkpoint=force)
                    if r:
                        results.append(r)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"  [ERROR] {name}: {str(e)[:120]}")
    except KeyboardInterrupt:
        print("\n  [中断] 已保存断点, 下次可续跑")
    finally:
        cp.save()

    _banner("补数完成 — 汇总")
    print(f"  总耗时: {(time.time()-t_all)/60:.1f} 分钟")
    for r in sorted(results, key=lambda x: x["name"]):
        print(f"  {r['name']:20s} 完成={r['done']}")
    print(f"  断点文件: {cp.path}")
    print(f"\n  提示: 重跑本命令会自动跳过已完成部分")


def main():
    p = argparse.ArgumentParser(description="通用补数引擎")
    p.add_argument("--tier", default="A,B", help="分档, 逗号分隔 (默认 A,B)")
    p.add_argument("--only", default=None, help="只跑指定接口, 逗号分隔")
    p.add_argument("--dry-run", action="store_true", help="只估算请求数, 不发请求")
    p.add_argument("--workers", type=int, default=None, help="每接口并发线程数 (默认4)")
    p.add_argument("--parallel-targets", type=int, default=None,
                   help=f"同时跑几个接口 (默认{BACKFILL_PARALLEL_TARGETS})")
    p.add_argument("--verbose", action="store_true", help="打印重试日志")
    p.add_argument("--limit", type=int, default=None,
                   help="每个接口最多跑 N 个任务 (用于微量验证/分批拉取)")
    p.add_argument("--list", action="store_true", help="列出目标清单后退出")
    p.add_argument("--force", action="store_true",
                   help="忽略断点与'库内已有标的'，全部重拉"
                        "（修复'断点说完成但数据不完整'）")
    args = p.parse_args()

    if args.list:
        print(f"{'接口':22s} {'说明':26s} {'模式':10s} {'tier':4s}")
        for n, c in BACKFILL_TARGETS.items():
            en = "" if c.get("enabled", True) else " [关闭]"
            print(f"{n:22s} {c.get('desc',''):26s} {c['mode']:10s} "
                  f"{c.get('tier','-'):4s}{en}")
        return

    tiers = tuple(t.strip() for t in args.tier.split(",") if t.strip())
    only = tuple(x.strip() for x in args.only.split(",")) if args.only else None
    run_backfill(tiers=tiers, only=only, dry_run=args.dry_run,
                 workers=args.workers, verbose=args.verbose,
                 parallel_targets=args.parallel_targets, limit=args.limit,
                 force=args.force)


if __name__ == "__main__":
    main()
