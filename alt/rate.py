# -*- coding: utf-8 -*-
"""
alt/rate.py — akshare 专用限流与风控退避
==========================================
与 tushare 的 300 次/分配额制**本质不同**：
  - tushare：明确配额，可规划、可并发打满
  - akshare：上游是公开网站，无标称限额但有**累积式风控**，
             实测探测本身会加重封锁 → 必须低频、串行、失败即长静默

核心设计：
  1. **按域名档位**独立限流（乐咕 30/分、push2his 8/分 ...）
  2. **预约式**令牌：锁内计算时刻、锁外 sleep（线程安全）
  3. **失败二分**：ConnectionError 判定为"被拦"→ 长静默；
                 其他错误 → 短退避重试
  4. **被拦后全局静默**：一旦触发，整个进程暂停请求，避免越探越糟
"""
import threading
import time
from datetime import datetime

from config_alt import (
    ALT_BACKOFF_STEPS,
    ALT_MAX_RETRY,
    ALT_SILENCE_ON_BLOCK,
    rate_for_url,
    host_tier,
)

# 判定为"被风控拦截"的异常特征
_BLOCK_SIGNS = (
    "ConnectionError",
    "RemoteDisconnected",
    "Connection aborted",
    "ConnectionResetError",
    "Max retries exceeded",
    "SSLError",
    # HTTP 层限流（实测乐咕在约 20 次/分时返回 429）
    "429",
    "Too Many Requests",
    "503",
    "504",
    "Gateway Time-out",
    # 被拦后站点返回 HTML 错误页 → JSON 解析失败（空响应特征）
    "Expecting value: line 1 column 1",
)
# 判定为"接口/解析问题"（非风控）的特征
_PARSE_SIGNS = (
    "AttributeError",
    "KeyError",
    "IndexError",
    "ValueError",
    "TypeError",
)


def is_block_error(exc) -> bool:
    """是否属于"被上游拦截"（而非接口本身出错）

    覆盖两类：
      1. 连接层：ConnectionError / RemoteDisconnected / SSLError
      2. 应用层：HTTP 429/503/504，以及被拦后返回 HTML 导致的空 JSON
    """
    s = f"{type(exc).__name__}: {exc}"
    return any(k.lower() in s.lower() for k in _BLOCK_SIGNS)


class AltRateLimiter:
    """akshare 专用限流器（按域名档位 + 被拦静默）"""

    def __init__(self, dry_run=False, verbose=True):
        self.dry_run = dry_run
        self.verbose = verbose
        self._lock = threading.RLock()
        self._next_at = {}          # tier -> 下次可发请求的时刻
        self._blocked_until = 0.0   # 全局静默截止（被拦后设置）
        self.stats = {
            "requests": 0,
            "success": 0,
            "fail": 0,
            "blocked": 0,
            "retries": 0,
            "silence_seconds": 0.0,
            "by_tier": {},
        }

    # ---------- 静默管理 ----------
    @property
    def blocked(self) -> bool:
        return time.time() < self._blocked_until

    def _remaining_silence(self) -> float:
        return max(0.0, self._blocked_until - time.time())

    def enter_silence(self, seconds=None, reason=""):
        """进入静默：整进程暂停发请求（避免加重封锁）"""
        sec = float(seconds if seconds is not None else ALT_SILENCE_ON_BLOCK)
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.time() + sec)
            self.stats["blocked"] += 1
            self.stats["silence_seconds"] += sec
        if self.verbose:
            print(f"    [静默] 触发风控保护，暂停 {sec:.0f}s"
                  f"{'（' + reason + '）' if reason else ''}")

    # ---------- 预约式限流 ----------
    def wait(self, url):
        """按 URL 所属域名档位预约并等待到允许时刻。

        返回实际等待秒数。
        """
        tier = host_tier(url)
        per_min = rate_for_url(url)
        interval = 60.0 / max(per_min, 1)

        # 静默期先等完
        sil = self._remaining_silence()
        if sil > 0:
            time.sleep(sil)

        with self._lock:
            now = time.time()
            slot = max(now, self._next_at.get(tier, 0.0))
            self._next_at[tier] = slot + interval
            self.stats["by_tier"].setdefault(tier, {"requests": 0, "interval": interval})
            self.stats["by_tier"][tier]["requests"] += 1

        delta = slot - time.time()
        if delta > 0:
            time.sleep(delta)
            return delta
        return 0.0

    # ---------- 调用包装 ----------
    def call(self, fn, url, *args, dry_run_note="", **kwargs):
        """执行一次 akshare 调用，带限流 + 重试 + 风控静默。

        参数:
            fn:   无参可调用（建议用 lambda 包好 akshare 调用）
            url:  该调用对应的上游 URL（用于判定域名档位；可为 ""）
        返回:
            (result, ok, error_str)
            ok=False 时 result 为 None，error_str 为原因
        """
        self.stats["requests"] += 1

        for attempt in range(1, ALT_MAX_RETRY + 1):
            wait_s = self.wait(url)
            t0 = time.time()
            try:
                result = fn()
                self.stats["success"] += 1
                return result, True, ""
            except Exception as e:  # noqa: BLE001
                cost = time.time() - t0
                blocked = is_block_error(e)

                if blocked:
                    # 被上游拦截：长静默，不做密集重试
                    step = ALT_BACKOFF_STEPS[min(attempt - 1, len(ALT_BACKOFF_STEPS) - 1)]
                    self.enter_silence(step, reason=f"{type(e).__name__}")
                    if attempt >= ALT_MAX_RETRY:
                        self.stats["fail"] += 1
                        return None, False, f"{type(e).__name__}: {str(e)[:120]}"
                    self.stats["retries"] += 1
                    continue

                # 非风控错误（解析/参数问题）→ 短退避
                self.stats["fail"] += 1
                return None, False, f"{type(e).__name__}: {str(e)[:160]}"

        self.stats["fail"] += 1
        return None, False, "重试耗尽"

    def status_str(self) -> str:
        s = self.stats
        tiers = " ".join(f"{k}:{v['requests']}" for k, v in s["by_tier"].items())
        return (f"请求 {s['requests']} | 成功 {s['success']} | 失败 {s['fail']} | "
                f"被拦 {s['blocked']} | 重试 {s['retries']} | "
                f"静默 {s['silence_seconds']:.0f}s | 档位[{tiers}]")
