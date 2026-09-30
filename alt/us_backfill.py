# -*- coding: utf-8 -*-
"""
alt/us_backfill.py — 美股建库（mode="list" 专用入口）
======================================================
为什么单独一个脚本：
  1. `mode="list"`（按标的列表逐只拉）需要"先读清单 → 再逐只带参数调用"，
     alt 现有 once / enum 两种模式都表达不了（已在 backfill/update 显式跳过）。
  2. 美股前复权**基准随新拆股漂移** ⇒ 不允许增量追加，**必须整表重建**，
     不适用日更引擎。

⚠ 三条实测纪律（勿改）：
  ① 必须 `adjust="qfq"`。默认返回**未复权**，实测 AAPL 2020-08-31 出现 -74.2%
     断崖、NVDA 2024-06-10 -89.9% → 技术分析会全线失真。
  ② 含点 symbol（如 `BRK.B`，223 只）**必须原样传**；横线 / 去点都会失败。
  ③ 美股收盘 = 北京次日凌晨 ⇒ 数据天然为**美股 T-1**。

用法:
  python -m alt.us_backfill --universe          只重建标的全市场清单
  python -m alt.us_backfill --limit 50          试跑前 50 只（不记断点）
  python -m alt.us_backfill                     全量（约 41 分钟）
  python -m alt.us_backfill --rebuild           忽略断点，整表重建
  python -m alt.us_backfill --workers 4         并发线程数（默认 4）
  python -m alt.us_backfill --status            查看进度
"""
import os
import sys
import io as _io
import time
import socket
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from config_alt import alt_table_dir, assert_writable
from common.paths import ensure_dir
from alt import spec as spec_mod
from alt import io as alt_io
from alt.backfill import AltCheckpoint

UNIVERSE = "us_universe"
DAILY = "us_daily_qfq"
BATCH = 3000          # 每批落盘阈值（只数）

# ⚠ 全局 socket 超时（2026-09-21 实测必需）: akshare 的 requests 调用**没有超时设置**，
#   新浪源对部分标的的请求会**永久挂起**（实测 5 个 TCP 会话挂了 15 分钟、
#   30 个线程全部 Wait、进度停滞）→ 工作线程被耗尽。
#   requests 会继承 socket 默认超时，故全局设置即可。
SOCKET_TIMEOUT = 25
socket.setdefaulttimeout(SOCKET_TIMEOUT)


def _patch_requests_timeout(timeout=25):
    """强制给所有 requests 调用加超时（monkey patch）。

    ⚠ 为什么必须（2026-09-21 实测）:
      akshare 内部的 requests 调用**不设 timeout**，新浪源对部分标的的请求会
      **永久挂起** —— 实测 TCP 连接挂 4.7~15 分钟、工作线程被全部耗尽、进度停滞。
      而 `socket.setdefaulttimeout()` **无效**（被 urllib3 连接池覆盖，实测挂 4.7 分钟）。
      故直接 patch `Session.request` 与 `requests.api.request`（setdefault，
      不覆盖调用方已显式指定的 timeout）。
    """
    import requests
    _sess_req = requests.sessions.Session.request

    def sess_req(self, *a, **kw):
        kw.setdefault("timeout", timeout)
        return _sess_req(self, *a, **kw)
    requests.sessions.Session.request = sess_req

    _api_req = requests.api.request

    def api_req(*a, **kw):
        kw.setdefault("timeout", timeout)
        return _api_req(*a, **kw)
    requests.api.request = api_req


def _log(msg):
    print(msg, flush=True)


# ============================================================
# 一、标的清单（快照型 → 覆盖式重建）
# ============================================================
def _normalize_universe_types(df):
    """统一清单元数据类型 → 全列字符串。

    ⚠ 必须做（实证踩坑 2026-09-21）:
      stock_us_spot 的 `name` 列**混有 str 与 bool**（部分行的 name 实为
      True/False），直接 to_parquet 会抛
      `ArrowTypeError: Expected bytes, got a 'bool' object`。
      该表是快照元数据、不做数值运算，故统一转 str 最稳妥
      （与 alt spec 的 force_str_cols 设计意图一致）。
    """
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if s.dtype == object or str(s.dtype) in ("str", "string", "bool"):
            out[c] = s.where(s.notna(), "").astype(str)
    return out


def _mark_universe_done(verbose=True):
    """给 us_universe 记一条断点（避免审计报「有文件但断点无记录」）。

    ⚠ 为什么需要（2026-09-21 修复）: 该表用 --universe-from-cache 直写 parquet，
      绕过了 AltCheckpoint → 审计【1】报「异常：断点丢失，会重复拉取」。
      该表是**快照型**（每次覆盖重建），故只需一个 __all__ 标记。
    """
    from alt.backfill import AltCheckpoint
    AltCheckpoint().mark_done(UNIVERSE, "__all__")
    if verbose:
        _log(f"[{UNIVERSE}] 断点已标记（__all__）")


def build_universe(verbose=True):
    """拉美股全市场清单并**覆盖式**落盘（快照表，不追加）。

    注意：该接口约需 7~8 分钟（分页 909 页），故已建库时默认复用、
    除非显式 --universe 或 --rebuild。
    """
    import akshare as ak
    sp = spec_mod.get(UNIVERSE)

    if verbose:
        _log(f"[{UNIVERSE}] 拉取 stock_us_spot（约 7~8 分钟）...")
    t = time.time()
    df = ak.stock_us_spot()
    if df is None or len(df) == 0:
        raise RuntimeError(f"{UNIVERSE}: 接口返回空")
    if verbose:
        _log(f"[{UNIVERSE}] 完成: {len(df):,} 行 | {time.time()-t:.1f}s")

    # 快照表：无日期列；统一列类型（防 str/bool 混存导致 pyarrow 报错）
    df = _normalize_universe_types(df)

    target = alt_io.table_path(sp)
    assert_writable(target)                     # 写入守卫：必须落在 alt 授权区
    ensure_dir(os.path.dirname(target))
    tmp = target + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, target)                     # 原子覆盖
    _mark_universe_done(verbose)
    if verbose:
        _log(f"[{UNIVERSE}] 已写入 {target} ({os.path.getsize(target)/1024:.1f} KB)")
    return len(df)


def build_universe_from_cache(cache_path, verbose=True):
    """从本地缓存的清单文件（parquet/pkl）生成 us_universe 表，免去重复拉取。

    用途: stock_us_spot 单次约 7~8 分钟（909 页），调试/重跑时无需重复付出。
    ⚠ 缓存必须是**近期**拉取的快照，否则清单会过期（漏掉新上市标的）。
    """
    sp = spec_mod.get(UNIVERSE)
    if cache_path.endswith(".pkl"):
        df = pd.read_pickle(cache_path)
    else:
        df = pd.read_parquet(cache_path)
    if df is None or len(df) == 0:
        raise RuntimeError(f"缓存为空: {cache_path}")
    df = _normalize_universe_types(df)

    target = alt_io.table_path(sp)
    assert_writable(target)
    ensure_dir(os.path.dirname(target))
    tmp = target + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, target)
    _mark_universe_done(verbose)
    if verbose:
        _log(f"[{UNIVERSE}] 已从缓存写入 {target} ({len(df):,} 行, "
             f"{os.path.getsize(target)/1024:.1f} KB)")
    return len(df)


def load_universe_symbols():
    """读清单表的 symbol 列表（保序去重）"""
    sp = spec_mod.get(UNIVERSE)
    target = alt_io.table_path(sp)
    if not os.path.exists(target):
        raise FileNotFoundError(
            f"清单未建库: {target}\n  请先跑: python -m alt.us_backfill --universe")
    df = pd.read_parquet(target, columns=["symbol"])
    syms = [str(x) for x in df["symbol"].tolist() if str(x).strip()]
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ============================================================
# 二、日线（逐只拉 + 分批落盘）
# ============================================================
def fetch_one(symbol, retries=2, delay=0.0):
    """拉单只美股日线（前复权）。返回 (symbol, DataFrame|None, err)"""
    import akshare as ak
    for attempt in range(retries + 1):
        try:
            d = ak.stock_us_daily(symbol=symbol, adjust="qfq")
            if delay:
                time.sleep(delay)       # 降速防风控（可选）
            if d is None or len(d) == 0:
                return symbol, None, "empty"
            d = d.copy()
            d["symbol"] = symbol            # 注入标的列（接口不返回）
            return symbol, d, None
        except Exception as e:
            if attempt >= retries:
                return symbol, None, f"{type(e).__name__}: {str(e)[:60]}"
            time.sleep(0.5 * (attempt + 1))
    return symbol, None, "unreachable"


def build_daily(symbols, workers=4, rebuild=False, verbose=True, limit=None, delay=0.0):
    """逐只拉取并分批落盘。返回统计 dict。"""
    _patch_requests_timeout(SOCKET_TIMEOUT)     # ★ 必须先启用超时保护
    sp = spec_mod.get(DAILY)
    ckpt = AltCheckpoint()

    # ⚠ limit 必须作用于"待拉列表"（断点过滤之后），否则若清单前 N 只
    #   恰好都已完成，--limit N 会一只都不跑（实测踩坑）。
    todo = [s for s in symbols if rebuild or not ckpt.is_done(DAILY, s)]
    skipped = len(symbols) - len(todo)
    if limit:
        todo = todo[:limit]
    if verbose:
        _log(f"[{DAILY}] 清单 {len(symbols):,} 只 | 断点跳过 {skipped:,} | "
             f"待拉 {len(todo):,}" + (f"（--limit {limit}）" if limit else ""))

    if not todo:
        _log(f"[{DAILY}] 全部已完成（如需重来: --rebuild）")
        return {"ok": 0, "empty": 0, "fail": 0, "rows": 0, "added": 0}

    # ── V8 预热（**必须**，2026-09-21 实证，勿删）──────────────────────
    # akshare 的 stock_us_daily 计算前复权因子时使用 py_mini_racer(V8)，
    # 而 V8 的**并发初始化**会直接致命崩溃，表现为：
    #     [FATAL:partition_address_space.cc(243)]
    #     Check failed: !IsConfigurablePoolInitialized().
    # 实证：未预热直接 4 线程跑全量 → 启动即崩（日志仅 3 行）；
    #       主线程先初始化一次后 → 4 线程 200 只零崩溃、零失败。
    # 机制：预热后 V8 已完成进程级初始化，多线程只是调用、不再初始化。
    if verbose:
        _log(f"[{DAILY}] V8 预热（py_mini_racer 并发初始化会致命崩溃，必须先单线程预热）...")
    try:
        fetch_one("AAPL")
        if verbose:
            _log(f"[{DAILY}] 预热完成，开始 {workers} 线程拉取")
    except Exception as e:
        _log(f"[{DAILY}] ⚠ 预热异常（忽略，继续）: {type(e).__name__}: {e}")

    stat = {"ok": 0, "empty": 0, "fail": 0, "rows": 0, "added": 0}
    errs = []
    buf = []                                # 缓冲区 [(symbol, DataFrame), ...]
    buf_n = 0
    t0 = time.time()

    def flush():
        """把缓冲区按年落盘（走 alt.io.save，含归一化 + 判重）。

        ⚠⚠ 断点必须**落盘成功之后**才记（2026-09-21 实证踩坑）:
          原实现是"拉完立刻 mark_done"，而数据在内存缓冲区、要等 flush 才落盘。
          一旦中途被杀（本环境极易发生：请求挂起/超时），
          就会出现**断点虚记** —— 实测中断后 5,514 个断点里 2,484 个磁盘无数据，
          且这些标的后续会被"断点说已完成"跳过 → **数据永久缺口**。
          正确顺序：**先落盘 → 确认 ok → 再记断点**。
        """
        nonlocal buf, buf_n
        if not buf:
            return
        syms_in_buf = [s for s, _ in buf]
        raw = pd.concat([d for _, d in buf], ignore_index=True)
        norm, warns = alt_io.normalize(raw, sp)
        res = alt_io.save(norm, sp)
        if not res.get("ok"):
            _log(f"  ✗ 落盘失败: {res.get('errors')}")
            errs.append(f"flush: {res.get('errors')}")
            # 落盘失败 → **不记断点**，本批下次重拉
        else:
            stat["added"] += res.get("added", 0)
            for s in syms_in_buf:
                ckpt.mark_done(DAILY, s)     # ✅ 落盘成功才记
        buf = []
        buf_n = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        _t_loop = time.time()
        for i, (sym, d, err) in enumerate(ex.map(lambda s: fetch_one(s, delay=delay), todo), 1):
            if err == "empty":
                stat["empty"] += 1
                ckpt.mark_done(DAILY, sym)      # 返空：源端确实无数据，可立即记
            elif err:
                stat["fail"] += 1
                errs.append(f"{sym}: {err}")    # 失败不记断点，留待续跑
            else:
                stat["ok"] += 1
                stat["rows"] += len(d)
                buf.append((sym, d))            # ⚠ 只入缓冲，**不在此处记断点**
                buf_n += 1

            # 进度：每 500 只打印一次（便于及时发现停滞），落盘另有日志
            if i % 500 == 0:
                _el = time.time() - _t_loop
                _rate = i / max(_el, 1)
                _log(f"    {i:,}/{len(todo):,} | 成功 {stat['ok']:,} 空 {stat['empty']} "
                     f"失败 {stat['fail']} | {_rate:.2f}只/s | "
                     f"剩余 ≈ {(len(todo)-i)/max(_rate,0.01)/60:.0f} 分钟")

            if buf_n >= BATCH:
                flush()
                el = time.time() - t0
                _log(f"  进度 {i:,}/{len(todo):,} | 成功 {stat['ok']:,} "
                     f"空 {stat['empty']} 失败 {stat['fail']} | "
                     f"{i/max(el,1):.1f}只/s | 已落盘 {stat['added']:,} 行")

    flush()

    el = time.time() - t0
    if verbose:
        _log(f"\n[{DAILY}] 完成: 成功 {stat['ok']:,} | 返空 {stat['empty']} | "
             f"失败 {stat['fail']} | 拉取 {stat['rows']:,} 行 | "
             f"落盘新增 {stat['added']:,} 行 | 耗时 {el/60:.1f} 分钟")
        if errs:
            _log(f"  失败/异常样例（前 8 条）:")
            for e in errs[:8]:
                _log(f"      {e}")
    stat["errors"] = errs
    return stat


# ============================================================
# 三、状态
# ============================================================
def prune_checkpoint(verbose=True):
    """清理断点虚记：从断点中移除"已记完成但磁盘无数据"的 symbol。

    背景（2026-09-21 实证）: 旧实现"拉完立刻记断点"，数据却在内存缓冲区，
    中途被杀就会产生虚记 —— 实测中断后 5,514 个断点中 2,484 个磁盘无数据，
    这些标的会被误当作"已完成"而**永久跳过** → 数据缺口。

    ⚠ 只删断点条目，不动磁盘数据；被移除的 symbol 下次运行会重拉（幂等安全）。
    """
    import json as _json
    from datetime import datetime as _dt
    ckpt = AltCheckpoint()
    done = set(ckpt.data.get(DAILY, {}).get("done", []))

    d = alt_table_dir(DAILY)
    syms = set()
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".parquet"):
                try:
                    x = pd.read_parquet(os.path.join(d, f), columns=["symbol"])
                    syms.update(str(s) for s in x["symbol"].unique())
                except Exception:
                    pass

    stale = sorted(done - syms)
    if verbose:
        _log(f"[{DAILY}] 断点已记 {len(done):,} | 磁盘实际 {len(syms):,} | "
             f"虚记 {len(stale):,}")
    if not stale:
        return 0

    keep = sorted(done & syms)
    ckpt.data.setdefault(DAILY, {})["done"] = keep
    ckpt.data[DAILY]["updated"] = _dt.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(ckpt.path), exist_ok=True)
    assert_writable(ckpt.path)
    tmp = ckpt.path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(ckpt.data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ckpt.path)
    if verbose:
        _log(f"[{DAILY}] 已清理虚记 {len(stale):,} 个 → 断点保留 {len(keep):,} 个"
             f"（这些标的将在下次运行时重拉）")
    return len(stale)


def show_status():
    ckpt = AltCheckpoint()
    for name in (UNIVERSE, DAILY):
        try:
            sp = spec_mod.get(name)
        except KeyError:
            _log(f"  {name}: 未登记"); continue
        d = alt_table_dir(name)
        files = []
        if os.path.isdir(d):
            files = [f for f in os.listdir(d) if f.endswith(".parquet")]
        done = len(ckpt.data.get(name, {}).get("done", []))
        rows = 0
        syms = set()
        for f in sorted(files):
            try:
                x = pd.read_parquet(os.path.join(d, f),
                                    columns=["symbol"] if name == DAILY else None)
                rows += len(x)
                if "symbol" in x.columns:
                    syms.update(str(s) for s in x["symbol"].unique())
            except Exception:
                pass
        line = f"  {name:16s} 文件 {len(files):3d} | 断点 {done:6,} | 约 {rows:,} 行"
        if name == DAILY and syms:
            stale = max(0, done - len(syms))
            line += f" | 磁盘标的 {len(syms):,}" + (f" | ⚠虚记 {stale:,}" if stale else "")
        _log(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="美股建库（mode=list 专用）")
    ap.add_argument("--universe", action="store_true", help="只重建标的全市场清单")
    ap.add_argument("--universe-from-cache", default=None,
                    help="从本地缓存(parquet/pkl)生成清单，免重复拉取(省 7~8 分钟)")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 只（试跑用）")
    ap.add_argument("--rebuild", action="store_true", help="忽略断点整表重建")
    ap.add_argument("--workers", type=int, default=4, help="并发线程数")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="每只请求后延迟秒数（降速防风控，默认 0）")
    ap.add_argument("--status", action="store_true", help="查看进度")
    ap.add_argument("--prune-ckpt", action="store_true",
                    help="清理断点虚记（断点已记但磁盘无数据），使它们重拉")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    args = ap.parse_args(argv)

    if args.status:
        _log("=" * 70); _log("  美股建库状态"); _log("=" * 70)
        show_status(); return 0

    if args.prune_ckpt:
        _log("=" * 70); _log("  清理断点虚记"); _log("=" * 70)
        prune_checkpoint(); return 0

    # 写入守卫自检（fail-fast）
    for t in (UNIVERSE, DAILY):
        sp = spec_mod.get(t)
        assert_writable(alt_io.table_path(sp, req_date="20260101"))
    _log(f"[守卫] 写入区自检通过（目标均在 alt 授权区）")

    if args.universe:
        build_universe(); return 0

    if args.universe_from_cache:
        build_universe_from_cache(args.universe_from_cache)
        if args.limit is None and not args.yes:
            return 0

    # 清单：不存在则先建
    try:
        symbols = load_universe_symbols()
    except FileNotFoundError as e:
        _log(str(e))
        _log("\n→ 自动先建清单...")
        build_universe()
        symbols = load_universe_symbols()
    _log(f"[清单] {len(symbols):,} 只标的")

    est = len(symbols) / max(args.workers, 1) * 0.5 / 60
    if not args.limit and not args.yes:
        _log(f"\n⚠ 全量建库预计 {est:.0f} 分钟（{len(symbols):,} 只 / {args.workers} 线程）")
        _log(f"  数据量约 1.16 亿行 / ~1 GB，写入 alt 库: {alt_table_dir(DAILY)}")
        _log(f"  开始？ Ctrl+C 可中断，断点续跑")
        try:
            input("  回车继续 > ")
        except EOFError:
            _log("  （非交互环境，直接继续）")

    build_daily(symbols, workers=args.workers, rebuild=args.rebuild,
                limit=args.limit, delay=args.delay)
    _log("\n[状态]"); show_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
