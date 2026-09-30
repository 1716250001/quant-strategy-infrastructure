# -*- coding: utf-8 -*-
"""
fetch/base.py — 拉取类任务 · 共享基建
======================================
所有拉取模块共用的底层设施:
  - RateLimiter: Tushare频率控制 (按接口独立的预约式令牌桶, 线程安全)
  - Checkpoint: 断点续跑管理
  - ts_call: 带重试的API调用包装 (限频走短退避)
  - fetch_codes_parallel: 线程池并发批量拉取 (每接口 4 线程)
  - get_pro: 全局唯一Tushare连接
  - 工具函数

=== 2026-09-15 性能改造 ===
实测发现旧实现有三处浪费:
  1. 全账号统一 200次/分 —— 但 daily 实测服务端限额是 300次/分
  2. 串行调用受网络延迟限制, 实际只能跑 87~126次/分 (远低于限额)
  3. 限频重试等 30~600 秒 —— 限频是分钟窗口, 等 1~3 秒即可
改造后: 按接口独立限频 + 线程池并发 + 限频短退避
"""

import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from config import (
    TS_TOKEN, TS_RATE_LIMIT_PER_MIN,
    TS_DAILY_LIMIT, TS_MAX_RETRIES, TS_RETRY_DELAYS,
    API_RATE_LIMIT, API_RATE_LIMIT_DEFAULT,
    TS_RATE_BACKOFF, TS_RATE_MAX_RETRIES, FETCH_MAX_WORKERS,
    MARKET_DATA_DIR,
)
from common.paths import ensure_dir
from common.codes import etf_to_ts_code, stock_to_ts_code


# ============================================================
# 权限错误识别与记录（2026-09-20 新增）
# ============================================================
# 背景：Tushare 存在"权限漏网"——官方标称门槛高于本账号档位、
#       但实测可调的接口（全库筛查实测 7 项）。这类接口一旦被平台
#       收紧权限，会**静默断裂**：返回"您没有接口(xxx)访问权限"。
#
# 为什么必须单独识别（实证缺口）：
#   原实现只有两个错误分支——"限频"与"其他"。无权限错误落到"其他"，
#   于是与其他三类**性质完全不同**的故障走同一条路径：
#     · 网络超时      （临时，重试有用）
#     · 接口改名/弃用  （永久，重试无用）
#     · 参数写错      （永久，重试无用）
#     · **无权限**    （永久，重试无用）← 本次要解决
#   后果：① 白白重试 max_retries 次（实测 5 次请求 / 18.5 秒）；
#        ② 最终一律 `return None`，**调用方无法从返回值区分原因**；
#        ③ 体检只看到"数据落后 N 天"，分辨不出是权限收紧还是网络故障。
#
# 处置：本模块把"无权限"识别为**永久性错误**——
#   · 立即返回，**不重试**（重试一万次也不会好）
#   · 记入 _PERMISSION_DENIED，供日更/体检读取并告警
# 这与 config_alt 的"越界尝试计数器"是同一模式：
#   把"推断"补强为**可直接观测的证据**。
_PERMISSION_DENIED = []          # [{api, params, err, at}, ...]

# 判据：Tushare 的实际文案是
#   "抱歉，您没有接口(fund_portfolio)访问权限，权限的具体详情访问：..."
# 注意与限频文案区分——限频是"您**访问接口**(xxx)**频率超限**"，
# 不含"访问权限"，故本判据不会误伤限频。
_PERM_SIGNS = ("没有接口", "访问权限")


def _is_permission_error(err_str):
    """是否为「无权限」错误（永久性，重试无用）"""
    return any(k in err_str for k in _PERM_SIGNS)


def _record_permission_denied(api_name, params, err_str):
    """记录一次权限拒绝（供日更/体检读取）"""
    _PERMISSION_DENIED.append({
        "api": api_name,
        "params": dict(params) if params else {},
        "err": str(err_str)[:200],
        "at": datetime.now().isoformat(timespec="seconds"),
    })


def permission_denied_records():
    """本次进程内遇到的权限拒绝明细"""
    return list(_PERMISSION_DENIED)


def permission_denied_count():
    """本次进程内遇到的权限拒绝次数"""
    return len(_PERMISSION_DENIED)


def clear_permission_denied():
    """清空记录（供测试/多次运行隔离）"""
    _PERMISSION_DENIED.clear()


def permission_denied_apis():
    """去重后的被拒接口名（便于告警文案）"""
    seen, out = set(), []
    for r in _PERMISSION_DENIED:
        if r["api"] not in seen:
            seen.add(r["api"])
            out.append(r["api"])
    return out


# ============================================================
# 全局Tushare连接 (单例)
# ============================================================
_pro = None

def get_pro():
    """获取全局唯一的Tushare Pro API实例"""
    global _pro
    if _pro is None:
        ts.set_token(TS_TOKEN)
        _pro = ts.pro_api()
    return _pro


def get_latest_trade_date(target_date_str=None):
    """通过tushare交易日历获取最近交易日"""
    pro = get_pro()
    try:
        date_str = target_date_str or datetime.now().strftime("%Y%m%d")
        start = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=date_str)
        if cal is not None and not cal.empty:
            open_days = cal[cal["is_open"] == 1].sort_values("cal_date", ascending=False)
            if not open_days.empty:
                return open_days.iloc[0]["cal_date"]
    except Exception as e:
        print(f"  [WARN] 交易日获取失败: {e}")
    return datetime.now().strftime("%Y%m%d")


def safe_fetch(func, name, **kwargs):
    """统一异常包装，返回DataFrame"""
    try:
        return func(**kwargs)
    except Exception as e:
        print(f"  [WARN] {name} 获取失败: {e}")
        return pd.DataFrame()


# etf_to_ts_code / stock_to_ts_code 统一由 common.codes 提供，
# 此处 re-export 以保持历史 import 路径（from fetch.base import ...）可用。
__all__ = [
    "get_pro", "get_latest_trade_date", "ensure_dir", "safe_fetch",
    "etf_to_ts_code", "stock_to_ts_code",
    "RateLimiter", "Checkpoint",
    "ts_call_with_retry", "ts_fetch", "ts_fetch_by_date",
    "fetch_codes_parallel",
]


# ============================================================
# 频率控制器 (按接口独立 · 预约式 · 线程安全)
# ============================================================
class RateLimiter:
    """按接口独立的预约式限频器。

    与旧版(全局统一 200次/分 + 串行 sleep)的区别:
      1. 每个 API 有独立速率: 从 config.API_RATE_LIMIT 查表, 查不到走默认
      2. 预约制: 线程进入时先"预约"本次可用时刻, 再 sleep 到该时刻。
         多线程并发下也能保证总速率不超限 (旧版共享 last_request_time 会失效)
      3. 线程安全: 内部加锁

    兼容旧用法:
        limiter.wait()                # 不传 API 名 → 走默认速率
        limiter.record("daily")
        limiter.check_daily("daily")
        limiter.status_str() / limiter.total_requests / limiter.daily_counts
    """

    def __init__(self, rate_per_min=TS_RATE_LIMIT_PER_MIN, daily_limit=TS_DAILY_LIMIT):
        self.default_rate = rate_per_min
        self.min_interval = 60.0 / rate_per_min
        self.daily_limit = daily_limit
        self.last_request_time = 0.0
        self.daily_counts = {}   # {api_name: count}
        self.total_requests = 0
        self._buckets = {}       # {api_name: {"interval": float, "next_at": float}}
        self._lock = threading.Lock()

    # ---------- 内部 ----------
    def _rate_for(self, api_name):
        """查该接口的速率(次/分)。查不到走默认。"""
        if api_name is None:
            return self.default_rate
        return API_RATE_LIMIT.get(api_name, API_RATE_LIMIT_DEFAULT)

    # ---------- 对外 ----------
    def wait(self, api_name=None):
        """阻塞到该接口的下一次可用时刻(预约制, 线程安全)。

        参数:
            api_name: Tushare 接口名, 如 "daily"。不传则用默认速率。
        """
        key = api_name or "__default__"
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                rate = self._rate_for(api_name)
                bucket = {"interval": 60.0 / rate, "next_at": 0.0}
                self._buckets[key] = bucket

            now = time.time()
            start = now if now >= bucket["next_at"] else bucket["next_at"]
            # 预约下一个槽位(提前占用, 释放锁后 sleep)
            bucket["next_at"] = start + bucket["interval"]

        sleep_for = start - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        self.last_request_time = time.time()

    def check_daily(self, api_name):
        """检查日配额, 返回True=可继续, False=已超限"""
        with self._lock:
            count = self.daily_counts.get(api_name, 0)
        return count < self.daily_limit

    def record(self, api_name):
        """记录一次请求"""
        with self._lock:
            self.daily_counts[api_name] = self.daily_counts.get(api_name, 0) + 1
            self.total_requests += 1

    def status_str(self):
        """返回当前状态字符串"""
        with self._lock:
            total = self.total_requests
            items = sorted(self.daily_counts.items())
        parts = [f"总请求={total}"]
        for api, cnt in items:
            parts.append(f"{api}={cnt}/{self.daily_limit}")
        return " | ".join(parts)

    def rate_summary(self):
        """返回各接口实际生效速率(次/分), 便于核对配置"""
        with self._lock:
            buckets = dict(self._buckets)
        out = {}
        for k, v in buckets.items():
            if v["interval"] > 0:
                out[k] = round(60.0 / v["interval"])
        return out


# ============================================================
# 断点管理
# ============================================================
class Checkpoint:
    """断点续跑管理器: 记录每个API已完成的代码列表

    线程安全: 多 target 并行补数时会被并发访问, 内部加锁。
    """

    def __init__(self, data_root=MARKET_DATA_DIR, filename=".checkpoint.json"):
        self.path = os.path.join(data_root, filename)
        self.data = {}
        self._lock = threading.Lock()
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                print(f"[断点] 已加载: {self.path}")
            except Exception as e:
                print(f"[断点] 文件损坏, 从头开始: {e}")
                self.data = {}

    def is_done(self, api_name, ts_code):
        """判断某个代码在某个API下是否已完成"""
        with self._lock:
            done_set = self.data.get(api_name, {}).get("completed", [])
            return ts_code in done_set

    def mark_done(self, api_name, ts_code):
        """标记某个代码在某个API下已完成"""
        with self._lock:
            if api_name not in self.data:
                self.data[api_name] = {"completed": []}
            if ts_code not in self.data[api_name]["completed"]:
                self.data[api_name]["completed"].append(ts_code)

    def get_completed_count(self, api_name):
        """获取某API已完成数量"""
        with self._lock:
            return len(self.data.get(api_name, {}).get("completed", []))

    def save(self):
        """持久化断点到磁盘(原子写: 先写临时文件再替换, 避免并发写坏)"""
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def reset(self):
        """清除断点"""
        with self._lock:
            self.data = {}
            if os.path.exists(self.path):
                os.remove(self.path)
        print("[断点] 已清除")


# ============================================================
# API调用包装 (含重试+截断检测)
# ============================================================
def _prev_date(date_str):
    """日期字符串减1天"""
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return (dt - timedelta(days=1)).strftime("%Y%m%d")
    except Exception:
        return date_str


def _norm_date(v):
    """把日期值规范化为 YYYYMMDD 字符串。

    为什么需要（防"静默设错 end_date"）:
      不同接口返回的日期列 dtype 不同 —— 有的给字符串 "20140523"，
      有的是 Timestamp("2014-05-23 00:00:00")。
      `_prev_date` 只按 "%Y%m%d" 解析，若传入 "2014-05-23 00:00:00"
      会解析失败并**原样返回**，导致 end_date 被设成一个非法值
      （服务端可能忽略它 → 每次都返回同一段数据 → 续拉无效）。
    """
    s = str(v).strip()
    if len(s) >= 10 and s[4] == "-":
        return s[:10].replace("-", "")      # "2014-05-23 ..." → "20140523"
    return s[:8] if len(s) > 8 else s       # "20140523" 原样


# ── 截断检测（2026-09-19 加固）──────────────────────────────
# 服务端"常见的单次返回上限"特征值 —— 任一命中即视为**可能**被截断。
#
# 为什么不能只用 `len(df) == max_rows`（真实缺陷，2026-09-19 实证）:
#   服务端的**默认上限**可能低于 config 里声明的 max_rows。实测 index_dailybasic:
#     不传 limit 时返 3000 行（服务端硬上限：传 limit=5000/10000 仍只返 3000），
#     而 config 写的是 max_rows=5000  →  `3000 != 5000`  → 截断检测**不触发**
#     → 静默判定"数据完整"，实际漏掉 2005-04-08~2014-05-22 共 9 年数据。
#   即：不能假设 max_rows 恒等于服务端上限。
SUSPECT_CAPS = frozenset({1000, 2000, 3000, 4000, 5000, 6000, 8000, 10000})

# 续拉轮数上限：防止"接口忽略 end_date 参数"时无限循环。
#   参考同类陷阱：opt_basic 的 trade_date 参数被静默忽略（实测传 20260918 与
#   20200102 返回完全相同的数据）。若某接口也忽略 end_date，每次都会返回
#   相同的满页数据 → 无上限就会死循环。
TRUNC_MAX_ROUNDS = 40


def _pick_date_col(df):
    """截断续拉时用于定位"最早日期"的列（决定下一步 end_date）。

    by_code 表返回的日期列不统一：多数是 trade_date，财务类用 end_date，
    公告类用 ann_date。都找不到就放弃续拉（宁可少拉，也不瞎猜）。
    """
    for c in ("trade_date", "end_date", "ann_date"):
        if c in df.columns:
            return c
    return None


def _is_possibly_truncated(n_rows, max_rows):
    """返回行数是否**可能**被服务端截断。

    判据（任一成立即为可能）:
      ① n == max_rows      命中 config 声明的上限
      ② n ∈ SUSPECT_CAPS   命中"服务端常见上限"特征值

    ⚠ `max_rows=0` 的语义（2026-09-19 变更）:
      以前 = "完全不检测截断"；现在 = "未声明上限，但仍按特征值 ② 检测"。
      理由：实测 index_dailybasic 配的是 5000、服务端实际只给 3000，
      "未声明/声明错误"恰恰是最危险的情形 —— 不能让配置疏漏变成静默漏数据。
      实测影响面：6 张 max_rows=0 的 by_code 表（namechange / stock_company /
      cb_issue / cb_share / cb_rating / stk_mins）当前返回值均不命中特征值，
      无行为变化。

    ⚠ 误触发的代价可接受：多一次请求；合并走 drop_duplicates，
      不会产生重复或错误数据（已实测回归：dividend/fund_adj/forecast/
      share_float/stk_managers 等均不触发）。
      且"续拉后行数无新增即停"，能自动区分"真截断"与"恰好等于上限但完整"
      （实证 share_float 518 个标的属后者）。
    """
    if n_rows <= 0:
        return False
    if max_rows > 0 and n_rows == max_rows:
        return True
    return n_rows in SUSPECT_CAPS


def _is_rate_limit_error(err_str):
    """判断是否为限频错误(429/频率超限)"""
    s = err_str.lower()
    return ("429" in s) or ("频率超限" in err_str) or ("每分钟" in err_str and "限" in err_str) \
        or ("rate" in s and "limit" in s) or ("频次" in err_str)


def ts_call_with_retry(pro, api_name, limiter, params, max_retries=TS_MAX_RETRIES,
                       verbose=True):
    """
    带重试的API调用, 返回DataFrame或None

    重试策略(2026-09-15 改造):
      - 限频错误 → 走 TS_RATE_BACKOFF 短退避(1~5秒), 最多 TS_RATE_MAX_RETRIES 次
        理由: 限频是"分钟窗口"概念, 等 1~3 秒即可重试, 旧版等 30~600 秒纯属空转
      - 其他错误 → 走 TS_RETRY_DELAYS(1/2/5/10/20秒), 最多 max_retries 次

    2026-09-20 新增第三分支:
      - **无权限错误 → 立即返回、不重试**（永久性错误，重试无用），
        并记入 permission_denied_records() 供日更/体检告警。
        详见模块顶部"权限错误识别与记录"。

    参数:
        pro: tushare pro_api 实例
        api_name: API名称 (如 "daily")
        limiter: RateLimiter实例
        params: 参数字典
        max_retries: 普通错误的最大重试次数
        verbose: 是否打印重试日志(并发场景建议关掉, 避免刷屏)
    """
    func = getattr(pro, api_name, None)
    if func is None:
        print(f"  [ERROR] API {api_name} 不存在")
        return None

    err_retry = 0        # 普通错误重试计数
    rate_retry = 0       # 限频重试计数
    skip_wait = False    # 限频重试时跳过本地预约等待（见下）

    while True:
        # ⚠ 限频重试时跳过本地 wait（2026-09-20 修复）：
        #   预约式限频器每次 wait 都会推进 next_at 一个完整 interval。
        #   对普通接口(interval<1s)无感，但对极低限频接口是灾难：
        #   实证 shibor_lpr(0.02/min, interval=3000s) 遇服务端超限后进入重试，
        #   每次重试都睡满 50 分钟 → 单次调用阻塞数小时。
        #   服务端已明确拒绝(未占用配额)，重试只需退避 delay，无需再等 interval。
        if not skip_wait:
            limiter.wait(api_name)
        skip_wait = False

        if not limiter.check_daily(api_name):
            print(f"  [LIMIT] {api_name} 日配额已耗尽 ({limiter.daily_counts.get(api_name, 0)}/{limiter.daily_limit})")
            return None

        try:
            df = func(**params)
            limiter.record(api_name)
            if df is None:
                return pd.DataFrame()
            return df
        except Exception as e:
            err_str = str(e)
            limiter.record(api_name)

            # ---- 无权限: 永久性错误, 立即返回不重试（2026-09-20 新增）----
            # ⚠ 必须放在"限频"判断之前：权限是**先于限频**校验的
            #   （该项目已实测确认：报"频率超限"即代表权限已通过）。
            #   实测代价：若不拦，无权限错误会被当普通错误重试 5 次
            #   （延迟 1+2+5+10=18s），而重试一万次也不会好。
            if _is_permission_error(err_str):
                _record_permission_denied(api_name, params, err_str)
                if verbose:
                    hint = ""
                    try:
                        from config import LEAKY_PERMISSIONS
                        if api_name in LEAKY_PERMISSIONS:
                            hint = (f"\n         ★ 该接口登记为「权限漏网」"
                                    f"（官方门槛 {LEAKY_PERMISSIONS[api_name].get('claimed')}）"
                                    f"——很可能是漏网已被平台收紧，"
                                    f"请启用替代路径或升档："
                                    f"{LEAKY_PERMISSIONS[api_name].get('alt', '（未登记替代）')}")
                    except Exception:
                        pass
                    print(f"  [权限] {api_name} 无权限（永久性，不重试）: "
                          f"{err_str[:60]}{hint}")
                return None

            # ---- 限频: 短退避 ----
            if _is_rate_limit_error(err_str):
                if rate_retry >= TS_RATE_MAX_RETRIES:
                    if verbose:
                        print(f"  [FAIL] {api_name} 限频重试{rate_retry}次仍失败: {err_str[:70]}")
                    return None
                delay = TS_RATE_BACKOFF[min(rate_retry, len(TS_RATE_BACKOFF) - 1)]
                if verbose:
                    print(f"  [限频] {api_name} 第{rate_retry+1}次重试, 等待{delay}s...")
                rate_retry += 1
                skip_wait = True        # 服务端已拒绝，勿再睡满一个 interval
                time.sleep(delay)
                continue

            # ---- 其他错误: 常规退避 ----
            if err_retry < max_retries - 1:
                delay = TS_RETRY_DELAYS[min(err_retry, len(TS_RETRY_DELAYS) - 1)]
                if verbose:
                    print(f"  [RETRY] {api_name} 错误: {err_str[:70]}, 等待{delay}s...")
                err_retry += 1
                time.sleep(delay)
                continue

            if verbose:
                print(f"  [FAIL] {api_name} 重试{err_retry+1}次仍失败: {err_str[:70]}")
            return None


def ts_fetch(pro, api_name, limiter, ts_code, max_rows=5000, extra_params=None,
             verbose=True):
    """
    调用 Tushare API (按ts_code), 包含:
    - 频率限制(按接口独立速率)
    - 日配额检查
    - 重试(限频短退避 / 普通错误常规退避)
    - 截断检测(返回行数==max_rows时按日期分段重拉)

    返回: (DataFrame, success:bool)
    """
    params = {"ts_code": ts_code}
    if extra_params:
        params.update(extra_params)

    df = ts_call_with_retry(pro, api_name, limiter, params, verbose=verbose)
    if df is None:
        return pd.DataFrame(), False

    # ── 截断检测 + 往前分段续拉 ──
    # 判据见 _is_possibly_truncated（不再只认 max_rows，见该函数 docstring）
    # 续拉方式：用 end_date 逐步往前推，直到"返回不足上限"或"源端无更早数据"。
    #
    # ⚠ 例外：登记在 ACCEPT_TRUNCATED_APIS 的接口**不做续拉**（2026-09-22 新增）。
    #   这些接口"命中上限"是预期行为（它们总是返回**最新 N 行**），
    #   且只需最新数据；续拉会灾难性放大（实证 index_global：
    #   22 指数 × 40 轮 × 7.5 秒限频 ≈ 110 分钟，且 880 请求远超 100次/天 配额）。
    #   配置在 config.ACCEPT_TRUNCATED_APIS，理由见该处注释。
    try:
        from config import ACCEPT_TRUNCATED_APIS
        _accept_trunc = api_name in ACCEPT_TRUNCATED_APIS
    except Exception:
        _accept_trunc = False
    if _accept_trunc and _is_possibly_truncated(len(df), max_rows):
        if verbose:
            print(f"  [接受截断] {api_name} 返回 {len(df)} 行（命中上限属预期，"
                  f"不续拉；理由见 config.ACCEPT_TRUNCATED_APIS）")
        return df, True

    if _is_possibly_truncated(len(df), max_rows):
        col = _pick_date_col(df)
        if col is None:
            return df, True            # 无日期列 → 无法分段，保持原样

        rounds = 0
        while rounds < TRUNC_MAX_ROUNDS:
            earliest = _norm_date(df[col].min())
            p_early = dict(params)
            p_early["end_date"] = _prev_date(earliest)
            df_early = ts_call_with_retry(pro, api_name, limiter, p_early,
                                          verbose=verbose)
            rounds += 1
            if df_early is None or df_early.empty:
                break                  # 源端确实没有更早的数据了

            before = len(df)
            df = pd.concat([df_early, df], ignore_index=True).drop_duplicates()
            if verbose:
                print(f"  [截断续拉] {ts_code} {api_name}: 第{rounds}轮 "
                      f"end_date={p_early['end_date']} → {len(df_early)}行, "
                      f"累计 {before}→{len(df)}行")
            if len(df) == before:
                # 无新增行 → 该接口可能**忽略 end_date**（同类陷阱见 opt_basic），
                # 再拉只会重复，必须跳出，否则死循环。
                break
            if not _is_possibly_truncated(len(df_early), max_rows):
                break                  # 本段不足上限 → 已覆盖到源端起点

            col = _pick_date_col(df_early) or col

    return df, True


# ============================================================
# 并发批量拉取 (2026-09-15 新增)
# ============================================================
def fetch_codes_parallel(pro, api_name, codes, limiter,
                         fetch_fn=None, max_workers=FETCH_MAX_WORKERS,
                         max_rows=5000, on_result=None,
                         progress=None, progress_every=100,
                         stop_event=None, verbose=False):
    """线程池并发拉取一组代码 (每接口默认 4 线程)。

    设计要点:
      - 网络请求在【worker 线程】并发执行, 但 on_result 回调在【主线程】串行执行。
        这样落盘(parquet)与断点标记(checkpoint)天然无竞争, 不需要额外加锁。
      - 限频由 RateLimiter 的预约制保证 (线程安全), 不会因并发而超限。

    参数:
        pro:        tushare pro_api 实例
        api_name:   Tushare 接口名 (用于限频与统计)
        codes:      待拉取的 ts_code 列表
        limiter:    RateLimiter 实例 (全线程共享同一个)
        fetch_fn:   可选自定义拉取函数 fetch_fn(pro, limiter, ts_code) -> DataFrame。
                    None 则使用 ts_fetch(pro, api_name, limiter, ts_code, max_rows)
        max_workers: 线程数, 默认 config.FETCH_MAX_WORKERS (=4)。传 1 则退化为串行。
        on_result:  回调 on_result(ts_code, df, ok), 在主线程按完成顺序调用
        progress:   回调 progress(done, total, stat, elapsed_sec), 每 progress_every 条
        stop_event: threading.Event, 置位后不再提交新任务(用于中断)
        verbose:    是否打印单次重试日志

    返回:
        {"total","ok","empty","fail","elapsed","rate_per_min","aborted"}
    """
    codes = list(codes)
    total = len(codes)
    stat = {"total": total, "ok": 0, "empty": 0, "fail": 0, "aborted": False}
    if total == 0:
        stat.update({"elapsed": 0.0, "rate_per_min": 0.0})
        return stat

    t0 = time.time()

    def _work(code):
        if stop_event is not None and stop_event.is_set():
            return code, None, False
        try:
            if fetch_fn is not None:
                df = fetch_fn(pro, limiter, code)
                # ⚠ ok 必须按 df 是否为 None 判定，**不可硬编码 True**（2026-09-21 修复）:
                #   历史 bug: 原写 `return code, df, True`，而 _tally 的判据是
                #     `if ok and df is not None and not df.empty → ok`
                #     `elif ok → empty`          ← df=None 因 ok=True 被计入「空」
                #     `else → fail`
                #   ⇒ **调用失败（df=None）被统计成「空」而非「失败」**：
                #     ① 日志显示「失败=0」，真实失败被完全掩盖；
                #     ② 报告据「空」得出"源端无该日数据"的**错误归因**
                #        （实证: 原补库报告称 eco_cal 240 天"源端无事件日"，
                #         现拉实测 20100201 有 100 行、20100202 有 11 行，
                #         实为 240 次调用失败）；
                #     ③ 该 240 天因 on_result 的 `if df is None: return` 不记断点，
                #        长期停留在"待补"却不被察觉。
                #   与 on_result 的既有语义（df=None 不落盘不记断点）保持一致。
                return code, df, df is not None
            df, ok = ts_fetch(pro, api_name, limiter, code,
                              max_rows=max_rows, verbose=verbose)
            return code, df, ok
        except Exception as e:
            if verbose:
                print(f"  [ERROR] {api_name} {code}: {str(e)[:70]}")
            return code, None, False

    if max_workers <= 1:
        # 串行路径 (保留用于对照/调试)
        for i, code in enumerate(codes, 1):
            c, df, ok = _work(code)
            _tally(stat, df, ok)
            if on_result is not None:
                on_result(c, df, ok)
            if progress is not None and (i % progress_every == 0 or i == total):
                progress(i, total, stat, time.time() - t0)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_work, c) for c in codes]
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    c, df, ok = fut.result()
                except Exception as e:
                    c, df, ok = "<?>", None, False
                    if verbose:
                        print(f"  [ERROR] {api_name} future: {str(e)[:70]}")
                _tally(stat, df, ok)
                if on_result is not None:
                    on_result(c, df, ok)
                if progress is not None and (i % progress_every == 0 or i == total):
                    progress(i, total, stat, time.time() - t0)

    stat["elapsed"] = time.time() - t0
    done = stat["ok"] + stat["empty"] + stat["fail"]
    stat["rate_per_min"] = (done / stat["elapsed"] * 60) if stat["elapsed"] > 0 else 0.0
    if stop_event is not None and stop_event.is_set():
        stat["aborted"] = True
    return stat


def _tally(stat, df, ok):
    """累加单条结果到统计(内部使用)"""
    if ok and df is not None and not df.empty:
        stat["ok"] += 1
    elif ok:
        stat["empty"] += 1
    else:
        stat["fail"] += 1


def ts_fetch_by_date(pro, api_name, limiter, trade_date, extra_params=None, max_retries=TS_MAX_RETRIES):
    """
    按交易日批量拉取数据 (不指定ts_code, 用trade_date)
    适用于尾盘增量更新场景: 一次拉全市场当日数据

    参数:
        trade_date: YYYYMMDD格式
        extra_params: 额外参数

    返回: DataFrame或None
    """
    params = {"trade_date": trade_date}
    if extra_params:
        params.update(extra_params)
    return ts_call_with_retry(pro, api_name, limiter, params, max_retries)
