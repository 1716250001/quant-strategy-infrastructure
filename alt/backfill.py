# -*- coding: utf-8 -*-
"""
alt/backfill.py — alt 库历史回补编排
=====================================
职责：按 spec 调 akshare、归一化、落盘、记断点。支持 --dry-run 全程预演。

断点（独立于主库）：
    D:\\全量数据\\alt_data\\.alt_checkpoint.json
    {"<table>": {"done": ["<enum值或日期>", ...], "updated": "ISO时间"}}

断点三态纪律（沿用主库约定）：
    - 调用失败          → 不记断点，留待续跑
    - 成功但空数据      → 记断点（避免每次重拉）
    - 成功且有数据      → 落盘后记断点

CLI：
    python -m alt.backfill --list                  # 列出规格
    python -m alt.backfill --dry-run               # 预演（不写库、不发请求）
    python -m alt.backfill --tier P0 --dry-run     # 指定优先级预演
    python -m alt.backfill --tier P0               # 实跑 P0
    python -m alt.backfill --only ebs_lg           # 单表
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime

import pandas as pd

from config_alt import (
    ALT_CHECKPOINT_FILE,
    ALT_DATA_DIR,
    ALT_META_DIR,
    assert_writable,
    ensure_alt_dirs,
)
from alt.io import normalize, save, alt_layout
from alt.rate import AltRateLimiter
from alt.spec import ALL_SPECS, TIERS


def _specs():
    """延迟导入 spec 模块（便于 --list 与测试）"""
    from alt import spec as _sp
    return _sp


# ============================================================
# 一、断点
# ============================================================
class AltCheckpoint:
    """alt 库独立断点（绝不与主库 .backfill_checkpoint.json 混用）"""

    def __init__(self, path=ALT_CHECKPOINT_FILE, dry_run=False):
        self.path = path
        self.dry_run = dry_run
        self.data = {}
        if os.path.exists(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.data = {}

    def is_done(self, table, key="__all__"):
        return key in self.data.get(table, {}).get("done", [])

    def mark_done(self, table, key="__all__"):
        if self.dry_run:
            return
        rec = self.data.setdefault(table, {"done": []})
        if key not in rec["done"]:
            rec["done"].append(key)
        rec["updated"] = datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        assert_writable(self.path)          # M1：落盘前过写入守卫
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)          # 原子写

    def progress(self, tables):
        """返回 (已完成表数, 总表数)"""
        done = sum(1 for t in tables if self.is_done(t))
        return done, len(tables)


# ============================================================
# 二、URL 推断（用于限流档位判定）
# ============================================================
def guess_url(func_name):
    """从函数名推断上游域名，用于限流档位。保守估计，宁慢勿快。

    ⚠ 已收敛到 alt/urls.py（R5，2026-09-19）：
      本函数原与 verify_spec.guess_url 是两份重复实现，注释自称"保持一致"，
      但规则实际不同（宏观前缀、_lg 匹配、qvix 判定三处均不同），
      导致同一接口在两模块被判入不同档位。现统一转发到唯一真源。
      本包装保留是为了不破坏既有调用方与外部引用。
    """
    from alt.urls import guess_url as _guess
    return _guess(func_name)


# ============================================================
# 三、单表执行
# ============================================================
# 源端"已超出保留窗口"类错误的特征词。
# 实测：stock_zt_pool_zbgc_em / stock_zt_pool_dtgc_em 在 akshare 源码层
#       硬校验"最近 30 个交易日"，超出直接 raise ValueError。
# ⚠ 为什么要单独识别：这类"失败"与网络/风控故障**性质完全不同**——
#   它表示"该日期永久不可补"，重试一万次也无用。若按普通失败处理
#   （不记断点），每天都会重试一次、每次都失败，既浪费请求，
#   又让"失败明细"长期挂红，制造新的"狼来了"效应。
_WINDOW_ERR_SIGNS = ("只能获取最近", "超出范围", "超出可用", "仅支持最近")


def is_window_error(err) -> bool:
    """该错误是否表示"已超出源端保留窗口"（永久不可补，重试无意义）"""
    s = str(err)
    return any(k in s for k in _WINDOW_ERR_SIGNS)


def run_spec(sp, limiter: AltRateLimiter, ckpt: AltCheckpoint,
             dry_run=False, force=False, verbose=True, target_date=None):
    """执行一张表（含 enum 展开）。返回结果 dict。

    target_date: 按日型表的目标日期（YYYYMMDD）。对 inject_date 的表必填。
    """
    import akshare as ak

    res = {"table": sp.name, "tier": sp.tier, "mode": sp.mode,
           "requests": 0, "rows": 0, "added": 0, "ok": False,
           "skipped": False, "window_out": False, "errors": [], "warnings": []}

    # —— mode="list"（按标的列表逐只拉）不属于本引擎 ——
    # 2026-09-21: 该模式需要"先读清单、再逐只带参数调用"，本引擎的 once/enum
    # 两种形态都表达不了；若漏过此处，会被当 once 无参调用而报错
    # （如实测 stock_us_daily 无 symbol 参数直接失败）。
    # 专用入口: python -m alt.us_backfill
    if sp.mode == "list":
        res["skipped"] = True
        res["warnings"].append(
            'mode="list" 表由 alt/us_backfill.py 处理，本引擎跳过'
            '（命令: python -m alt.us_backfill）')
        return res

    fn = getattr(ak, sp.func, None)
    if fn is None:
        res["errors"].append(f"akshare 无此函数: {sp.func}")
        return res

    url = guess_url(sp.func)

    # —— enum 型：逐枚举值取数并合并 ——
    if sp.mode == "enum":
        frames = []
        done_keys = []                     # 暂存，落盘成功后才记断点
        for val in sp.enum_values:
            key = f"enum:{val}"
            if ckpt.is_done(sp.name, key) and not force:
                continue
            if dry_run:
                res["requests"] += 1
                res["warnings"].append(f"[dry-run] 将调用 {sp.func}({sp.enum_param}={val!r})")
                continue
            raw, ok, err = limiter.call(lambda v=val: fn(**{sp.enum_param: v}), url)
            res["requests"] += 1
            if not ok:
                res["errors"].append(f"{val}: {err}")
                continue
            norm, warns = normalize(raw, sp, enum_value=val)
            res["warnings"].extend(warns)
            if len(norm):
                frames.append(norm)
            done_keys.append(key)           # 成功（含空数据）→ 暂存，不立即记
        if dry_run:
            res["ok"] = True
            return res
        if frames:
            merged = pd.concat(frames, ignore_index=True)
            st = save(merged, sp)
            res.update(rows=st["rows"], added=st["added"],
                       warnings=res["warnings"] + st["warnings"],
                       errors=res["errors"] + st.get("errors", []))
            # ⚠ R4（2026-09-19 修复）：成功与否必须**同时**看落盘结果与枚举失败。
            #   原实现只取 st["ok"]（落盘结果），于是【部分枚举值失败】时仍报成功——
            #   这比"全部失败"更常见（坏一个枚举值就中招），后果是：
            #     失败被掩盖、汇总"成功 N 张"虚高、该表不进失败清单，
            #     且 update 会据此写入 last_update 而延迟重试。
            res["ok"] = bool(st["ok"]) and not res["errors"]
        else:
            # 全部枚举值既无数据也无错误 vs 全部调用失败，必须区分对待：
            n_val = len(sp.enum_values)
            if res["errors"]:
                # 调用失败（网络/风控/接口变更）→ 判失败，不记断点，留待续跑
                res["ok"] = False
                res["errors"].append(
                    f"全部 {n_val} 个枚举值均未成功取数，本表本轮无数据落盘")
            else:
                # 全部成功但都返回空 → 判成功（避免每次重拉空表）
                res["ok"] = True
        # ⚠ 2026-09-22 修复（P0）: 断点必须在**落盘成功之后**才记。
        #   原实现在循环内即 ckpt.mark_done，而落盘在循环之后 —— 一旦在两者
        #   之间中断（落盘异常 / Ctrl+C / OOM），断点已写而磁盘无数据，
        #   后续 run_spec 因 is_done 直接 continue，frames 为空 → 落入上面的
        #   else 分支判 ok=True，表现为"成功但无数据"，该表永不再拉。
        #   同项目 alt/us_backfill.py 已是"先落盘 → 确认 ok → 再记断点"，此处对齐。
        if res["ok"]:
            for k in done_keys:
                ckpt.mark_done(sp.name, k)
        return res

    # —— once 型 ——
    # 断点粒度：按日型表按【日期】记（快照每日需重拉），其余按表记
    key = f"date:{target_date}" if sp.inject_date else "__all__"
    if ckpt.is_done(sp.name, key) and not force:
        res["skipped"] = True
        return res

    if sp.inject_date and not target_date and not dry_run:
        res["errors"].append("该表为按日型，需指定 --date（或传 target_date）")
        return res

    # ⚠⚠ 纯快照表写入守卫（R1，2026-09-19 修复，属严重静默错误）⚠⚠
    #   这类接口没有任何日期维度，无论传什么日期都只返回**同一份当前快照**。
    #   若调用方指定了一个"历史日期"，就会把当前数据错标成历史——
    #   不报错、行数正常、日期"看着对"，**只有数值是错的**。
    #   故必须两道硬拦：
    #     ① 时刻合法：源端数据必须已完整（盘中采集只能是残缺快照）
    #     ② 日期合法：注入日期必须等于"此刻真正可采集的那个交易日"
    #
    # ⚠ N8（2026-09-19 三轮审查）：dry-run 也要检查，只是**不阻断**。
    #   原实现 `if sp.is_snapshot and not dry_run` 让 dry-run 完全跳过守卫，
    #   于是 `--date <历史日> --dry-run` 显示"OK"，实跑却被拒绝 ——
    #   "预演结果 ≠ 实跑结果"，手工试参数时极具误导性（已实测复现）。
    #   现改为：dry-run 也走同一套判断，命中则记入 warnings 并注明
    #   "实跑将拒绝"，既保持预演语义（仍展示计划），又准确预警。
    if sp.is_snapshot:
        safe, why, warn = is_snapshot_safe_now()
        if warn:
            res["warnings"].append(warn)
        if not safe:
            msg = (f"纯快照表拒绝采集：{why}。"
                   f"该表历史不可回补，但也不能用残缺数据充数")
            if dry_run:
                res["warnings"].append(f"[dry-run] 实跑时会被拒绝：{why}")
            else:
                res["errors"].append(msg)
                return res
        live = latest_trade_date()
        if str(target_date) != str(live):
            msg = (f"纯快照表拒绝写入：注入日期 {target_date} ≠ 当前可采集日期 {live}。"
                   f"该接口无日期维度，只能采集「此刻」的快照；"
                   f"指定历史日期会把当前数据错标为历史（静默污染）")
            if dry_run:
                res["warnings"].append(
                    f"[dry-run] 实跑时会被拒绝：注入日期 {target_date} "
                    f"≠ 当前可采集日期 {live}（纯快照表只能采当日）")
            else:
                res["errors"].append(msg)
                return res

    if dry_run:
        kw = dict(sp.params)
        if sp.date_param:
            kw[sp.date_param] = "<date>"
        res["requests"] = 1
        res["ok"] = True
        res["warnings"].append(f"[dry-run] 将调用 {sp.func}({kw or ''})")
        return res

    # 组装实参：接口认日期参数的才传
    kw = dict(sp.params)
    if sp.date_param and target_date:
        kw[sp.date_param] = str(target_date)

    raw, ok, err = limiter.call(lambda: fn(**kw), url)
    res["requests"] += 1
    if not ok:
        if is_window_error(err):
            # 已超出源端保留窗口 → 该日期**永久不可补**，重试无意义。
            # 记断点表示"已尝试过、不可补"；并置 window_out 让调用方中止
            # 后续更老日期（更老的必然同样越界）。
            res["window_out"] = True
            res["ok"] = True
            res["warnings"].append(
                f"已超出源端保留窗口，该日期永久不可补：{str(err)[:90]}")
            ckpt.mark_done(sp.name, key)
            return res
        res["errors"].append(err)
        return res

    norm, warns = normalize(raw, sp, req_date=target_date)
    res["warnings"].extend(warns)
    if len(norm) == 0:
        res["ok"] = True
        ckpt.mark_done(sp.name, key)
        return res

    st = save(norm, sp, req_date=target_date)
    res.update(rows=st["rows"], added=st["added"], ok=st["ok"],
               warnings=res["warnings"] + st["warnings"],
               errors=res["errors"] + st.get("errors", []))
    if st["ok"]:
        ckpt.mark_done(sp.name, key)
    return res


# ============================================================
# 四、编排
# ============================================================
def latest_trade_date(market_close_hour=15, verbose=False):
    """最近【已收盘】交易日（只读主库交易日历，不写入）。

    ⚠ 为什么必须加收盘保护：
        快照类接口（涨停池等）**返回数据不含日期**，日期由我们注入。
        若在盘前/盘中运行而注入"今天"，会把【昨日】数据错标为今日 ——
        这是一类**静默错误**（数据看着正常，日期是错的）。

    规则：
        - 当前时刻 >= 15:00 → 今天若为交易日则取今天
        - 当前时刻 <  15:00 → 今天数据尚未产生，取今天之前最近的交易日
    """
    import pandas as pd
    from config_alt import TS_DATA_DIR

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    after_close = now.hour >= market_close_hour

    p = os.path.join(TS_DATA_DIR, "metadata", "trade_cal.parquet")
    if os.path.exists(p):
        try:
            cal = pd.read_parquet(p)
            cal = cal[(cal["exchange"] == "SSE") & (cal["is_open"] == 1)]
            cal["cal_date"] = cal["cal_date"].astype(str)
            if after_close:
                cand = cal[cal["cal_date"] <= today]
            else:
                cand = cal[cal["cal_date"] < today]
            if len(cand):
                d = str(cand["cal_date"].max())
                if verbose:
                    print(f"  [日期] {now:%H:%M:%S} "
                          f"{'盘后' if after_close else '盘前/盘中'} → 目标交易日 {d}")
                return d
        except Exception:
            pass

    # 兜底：用当天（无日历时的降级，宁可保守）
    return today


def _trade_cal_frame():
    """读主库交易日历（只读，绝不写入）；不可用返回 None"""
    import pandas as pd
    from config_alt import TS_DATA_DIR

    p = os.path.join(TS_DATA_DIR, "metadata", "trade_cal.parquet")
    if not os.path.exists(p):
        return None
    try:
        cal = pd.read_parquet(p)
        cal = cal[(cal["exchange"] == "SSE") & (cal["is_open"] == 1)]
        cal["cal_date"] = cal["cal_date"].astype(str)
        return cal
    except Exception:  # noqa: BLE001
        return None


def is_trade_day(d):
    """d(YYYYMMDD) 是否交易日；无交易日历时按"工作日"保守估计"""
    cal = _trade_cal_frame()
    if cal is None:
        import pandas as pd
        try:
            return pd.Timestamp(str(d)).weekday() < 5
        except Exception:  # noqa: BLE001
            return True
    return bool((cal["cal_date"] == str(d)).any())


def is_snapshot_safe_now(open_hour=9, close_hour=15, buffer_minutes=30):
    """此刻是否可采集「纯快照」表。返回 (safe, reason, warn)。

    ⚠ 为什么必须判（R1 衍生，2026-09-19）：
      分时波指这类接口在源端**只保留当日分时**。三种时刻的数据含义完全不同：

        · 盘后（>= 15:00）→ 源端是**当日完整**快照 → 安全，注入当日
        · 盘前（< 09:00） → 源端仍是**上一交易日**的完整快照
                            → 安全，注入上一交易日
        · **盘中（09:00~15:00）→ 源端正在写入当日"未完成"分时**
          → 无论注入哪一天都是错的（要么数据残缺，要么把当日残片标成上一日）

      这是比"复核窗口污染"更底层的一层保护：
      **快照必须在源端数据完整时采集**。

    ⚠ 收盘缓冲（N3，2026-09-19 二次审查）——**只告警，不拒绝**：
      刚过 15:00 时源端可能尚未把当日分时写完（实测 optbbs 是直读 CSV）。
      若在 15:00~15:30 采集，有拿到**残缺数据**的风险。

      为什么这里**不做硬拒绝**（与审查建议不同，理由如下）：
        两种方案的失败模式不对称——
          · 硬拒绝：该日快照**永久丢失**（源端不保留历史，且当天若无二次
            运行就再也没机会补）；损失不可逆。
          · 采集但告警：可能拿到残缺数据，但**当天重跑一次即可 force 覆盖
            修正**；损失可逆。
        本项目的核心纪律是"避免**静默**错误"——残缺可以用告警消除，
        不该用"不可逆地丢一天"来换。
      故：15:00~15:30 仍采集，但输出明确的告警提示稍后重跑；
          建议把定时任务排在 **15:30 之后**（如 16:00 / 盘后流水线末尾）。
    """
    now = datetime.now()
    h, m = now.hour, now.minute

    # ⚠ N7（2026-09-19 三轮审查）：**非交易日判断必须放在最前**。
    #   原实现把 `not is_trade_day` 放在盘中分支之后，于是 15:00 后的
    #   任何时刻都会从"盘后"分支直接返回，永远走不到交易日判断 ——
    #   结果周六/周日 15:00~15:30 运行盘后任务时，会误报
    #   "源端分时可能尚未写完"。非交易日根本没有分时在写，
    #   源端停在上一交易日的完整快照上。
    if not is_trade_day(now.strftime("%Y%m%d")):
        return True, "", ""

    # 盘前：源端仍是上一交易日的完整快照
    if h < open_hour:
        return True, "", ""

    # 盘后
    if h >= close_hour:
        # 收盘缓冲内 → 采集，但提示可能不完整（N3：只告警不拒绝）
        if (h * 60 + m) < (close_hour * 60 + buffer_minutes):
            return True, "", (f"当前 {now:%H:%M} 刚过收盘（不足 {buffer_minutes} "
                              f"分钟），源端分时可能尚未写完 → 建议稍后重跑一次"
                              f"以覆盖修正（当天重跑会自动 force 刷新当日）")
        return True, "", ""

    # 盘中：源端正在写入当日"未完成"分时，无论注入哪天都是错的
    return False, (f"当前 {now:%H:%M} 处于交易时段，源端分时快照尚未走完，"
                   f"采集会得到残缺数据（应等收盘后运行）"), ""


def _backfill_dates(sp, tdate):
    """某表本轮应覆盖的日期列表（**新 → 旧**）。

    ⚠ R3（2026-09-19 修复）：原实现只取 latest_trade_date() **一天**，
      spec 里登记的 max_backfill_days（源端可回补窗口）从未被使用。
      实测后果：zt_pool 族只入库 3 天，而窗口允许 20 天 ——
      约 2 周"本可回补"的涨停池历史被白白放弃，且源端过期后**不可再生**。

    排序为什么必须**新→旧**：
      超出源端窗口的日期会失败（zt_pool_zbgc/_dtgc 在 akshare 源码层硬校验
      30 天，超出直接 raise）。从新往旧拉，才能"先把有效数据拿到手、
      碰到窗口边界自然停止"；若从旧往新，第一条就撞窗口报错，
      后面的有效日期一个都拿不到。
    """
    if not sp.inject_date or not sp.max_backfill_days:
        return [tdate]

    # ⚠ N4（2026-09-19 二次审查）：纯快照表**显式**短路，不参与多日回补。
    #   为什么不依赖"max_backfill_days 恰好为 0"这个巧合：
    #     当前 10 张快照表的 max_backfill_days 确实是 0，于是上面的判断
    #     已经让它们返回 [tdate]。但这是**配置巧合**而非**类型约束**——
    #     若将来有人给某张快照表填了 max_backfill_days>0（本意可能是
    #     "想多拿几天"），就会展开成多日期；虽然 run_spec 的守卫会逐日拒绝
    #     （报错而非污染，不会静默），但那属于"靠下游兜底"，
    #     不如在源头把约束写死：**快照表在定义上就没有历史可回补**。
    if sp.is_snapshot:
        return [tdate]

    try:
        from common.calendar import recent_trade_dates
        ds = recent_trade_dates(sp.max_backfill_days, end_date=tdate)
    except Exception:  # noqa: BLE001
        ds = []
    out = sorted({str(d) for d in ds}, reverse=True)
    return out or [tdate]


def _merge_daily_results(sp, pairs):
    """把同一张表多个日期的执行结果聚合成一条记录（供汇总/打印）"""
    agg = {"table": sp.name, "tier": sp.tier, "mode": sp.mode,
           "requests": 0, "rows": 0, "added": 0, "ok": True, "skipped": False,
           "errors": [], "warnings": [], "days": [], "failed_at": None,
           "window_skip": 0}
    for d, r in pairs:
        agg["requests"] += r.get("requests", 0)
        agg["rows"] += r.get("rows", 0)
        agg["added"] += r.get("added", 0)
        agg["errors"].extend(r.get("errors", []))
        agg["warnings"].extend(r.get("warnings", []))
        if r.get("skipped"):
            continue
        if r.get("window_out"):
            # 窗口外：不可补，不计入"已覆盖天数"，也不算失败
            agg["window_skip"] += 1
            continue
        if not r.get("ok"):
            agg["ok"] = False
            if agg["failed_at"] is None:
                agg["failed_at"] = d
            break
        agg["days"].append(d)
    if not agg["days"] and not agg["failed_at"] and not agg["window_skip"]:
        agg["skipped"] = True     # 全部日期都已 done
    return agg


def run(tier=None, only=None, dry_run=False, force=False, limit=None,
        target_date=None, verbose=True):
    """执行回补。dry_run=True 时全程预演（不发请求、不写库）。"""
    sp_mod = _specs()

    if only:
        specs = [sp_mod.get(only)]
    elif tier:
        specs = sp_mod.tier_specs(tier)
    else:
        specs = list(ALL_SPECS)

    if limit:
        specs = specs[:limit]

    if not dry_run:
        ensure_alt_dirs()

    # 按日型表的目标日期
    need_date = any(s.inject_date for s in specs)
    tdate = target_date
    if need_date and not tdate:
        tdate = latest_trade_date()

    limiter = AltRateLimiter(dry_run=dry_run, verbose=verbose)
    ckpt = AltCheckpoint(dry_run=dry_run)

    print("=" * 78)
    print(f"alt 库历史回补{'【DRY-RUN 预演】' if dry_run else '【实跑】'}")
    print(f"  库根    : {ALT_DATA_DIR}")
    print(f"  表数    : {len(specs)}")
    if need_date:
        print(f"  目标日期: {tdate}（按日型表使用）")
    print(f"  断点    : {ALT_CHECKPOINT_FILE}")
    print(f"  时间    : {datetime.now():%Y-%m-%d %H:%M:%S}")

    # ⚠ N5（2026-09-19 三轮审查）：--force 会忽略断点重跑**全部**日期，
    #   成本远高于日常增量（实测 zt_pool 族 6 表 × 20~30 天 ≈ 120~180 请求，
    #   push2ex 15/分档位下约 8~12 分钟）。此处给出预估，避免误用。
    if force:
        _est = 0
        for sp in specs:
            if not sp.inject_date or sp.is_snapshot:
                _est += 1                      # 无日期维度 / 快照表：只 1 次
            else:
                _est += len(_backfill_dates(sp, tdate))
        print(f"  ⚠ --force：忽略断点强制重跑，预计约 {_est} 次请求"
              f"（按各表窗口展开估算；实际会因'越界即停'减少）")
        print(f"    费率参考：push2ex 15/分、legulegu 10/分 → 数分钟到十余分钟不等")
    print("=" * 78)

    results = []
    t_start = time.time()

    # ⚠ R2（2026-09-19）：主库隔离改用**前后夹逼**校验（越界判据）。
    #   静态基线比对会因主库盘后流水线每日合法写库而长期误报，故在本次
    #   回补前后各记一次主库全量指纹，差异可直接归因于本次操作。
    from contextlib import nullcontext
    from alt.audit import isolation_guard

    guard = (nullcontext() if dry_run
             else isolation_guard(verbose=verbose, label="alt.backfill"))

    with guard:
        for i, sp in enumerate(specs, 1):
            dates = _backfill_dates(sp, tdate)
            print(f"\n[{i}/{len(specs)}] {sp.name}  (tier={sp.tier}, layout={sp.layout}, "
                  f"mode={sp.mode}) — {sp.note}")
            if len(dates) > 1:
                print(f"    - 按 max_backfill_days={sp.max_backfill_days} 逐日回补 "
                      f"{len(dates)} 天：{dates[-1]} ~ {dates[0]}（新→旧，"
                      f"越界即停）")
            try:
                pairs = []
                for j, d in enumerate(dates):
                    rr = run_spec(sp, limiter, ckpt, dry_run=dry_run, force=force,
                                  verbose=verbose, target_date=d)
                    pairs.append((d, rr))
                    if rr.get("window_out"):
                        # 撞到源端保留窗口边界：更老的日期必然同样越界 →
                        # 一次性全部标记为"不可补"，避免以后每天逐日试探
                        for older in dates[j + 1:]:
                            ckpt.mark_done(sp.name, f"date:{older}")
                        break
                    # 单日失败即停：避免在风控下持续轰击
                    if rr.get("errors"):
                        break
                r = _merge_daily_results(sp, pairs)
            except Exception as e:  # noqa: BLE001
                r = {"table": sp.name, "tier": sp.tier, "mode": sp.mode, "requests": 0,
                     "rows": 0, "added": 0, "ok": False, "skipped": False,
                     "errors": [f"{type(e).__name__}: {e}"],
                     "warnings": [traceback.format_exc()[-300:]]}
            results.append(r)

            if r.get("skipped"):
                print("    - 跳过（断点显示已完成）")
            else:
                extra = ""
                if len(r.get("days") or []) > 1:
                    extra = (f" | 覆盖 {len(r['days'])} 天 "
                             f"({r['days'][-1]}~{r['days'][0]})")
                print(f"    - 请求 {r['requests']} | 落盘 {r['rows']} 行"
                      f"（新增 {r['added']}） | {'OK' if r['ok'] else 'FAIL'}{extra}")
                if r.get("failed_at"):
                    print(f"    - 回补中止于 {r['failed_at']}"
                          f"（该日失败，其后日期未尝试）")
                if r.get("window_skip"):
                    print(f"    - 窗口外不可补 {r['window_skip']} 天"
                          f"（已记断点，后续不再重试）")
            for w in r.get("warnings", [])[:3]:
                print(f"      [warn] {w[:130]}")
            for e in r.get("errors", [])[:3]:
                print(f"      [ERR ] {e[:130]}")

    dur = time.time() - t_start
    ok_n = sum(1 for r in results if r["ok"] and not r.get("skipped"))
    skip_n = sum(1 for r in results if r.get("skipped"))
    fail_n = len(results) - ok_n - skip_n
    tot_rows = sum(r["rows"] for r in results)
    tot_req = sum(r["requests"] for r in results)

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"  表: 成功 {ok_n} | 跳过 {skip_n} | 失败 {fail_n}")
    print(f"  请求数: {tot_req} | 落盘行数: {tot_rows:,} | 耗时: {dur:.1f}s")
    print(f"  {limiter.status_str()}")
    if dry_run:
        print("\n  ※ DRY-RUN：未发起任何网络请求，未写入任何文件。")

    # 失败清单（排除"跳过"——跳过的表 res["ok"] 为 False 但并非失败）
    fails = [r for r in results if not r["ok"] and not r.get("skipped")]
    if fails:
        print("\n  失败明细:")
        for r in fails:
            errs = r.get("errors") or ["(无错误信息)"]
            print(f"    {r['table']:<22} {str(errs[0])[:90]}")
    return results


# ============================================================
# 五、CLI
# ============================================================
def main(argv=None):
    p = argparse.ArgumentParser(description="akshare 备用库历史回补")
    p.add_argument("--tier", choices=TIERS, help="按优先级执行（自动派生自 spec，勿硬编码）")
    p.add_argument("--only", help="只执行某张表")
    p.add_argument("--dry-run", action="store_true", help="只预演，不发请求不写库")
    p.add_argument("--force", action="store_true", help="忽略断点强制重跑")
    p.add_argument("--limit", type=int, help="最多执行前 N 张表")
    p.add_argument("--date", help="按日型表的目标日期 YYYYMMDD（默认取最近交易日）")
    p.add_argument("--list", action="store_true", help="列出规格概览")
    args = p.parse_args(argv)

    sp_mod = _specs()

    if args.list:
        print("=" * 90)
        print("alt 库表规格")
        print("=" * 90)
        for s in sp_mod.summary():
            print(f"\n[{s['tier']}] {s['tables']} 张表 / 首次约 {s['requests']} 次请求")
            for n in s["names"]:
                spec = sp_mod.get(n)
                print(f"    {n:<22} {spec.func:<38} {spec.layout:<8} "
                      f"{spec.mode:<5} {spec.note}")
        return 0

    run(tier=args.tier, only=args.only, dry_run=args.dry_run,
        force=args.force, limit=args.limit, target_date=args.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
