# -*- coding: utf-8 -*-
"""
config_alt.py — akshare 备用数据源（alt 库）独立配置
======================================================
设计原则（严格遵守分立方案）：
  1. **不修改 config.py 的任何内容**，也不被 config.py 引用（单向依赖：本文件 → config.py）
  2. alt 库与 tushare 主库 **物理分离**，仅读 tushare 库、绝不写
  3. 所有路径、断点、限流参数均独立，互不干扰
  4. 表规格集中在 alt/spec.py，本文件只放路径与运行参数

库布局：
    D:\\全量数据\\
    ├── market_data\\   ← tushare 主库（58 目录，只读）
    └── alt_data\\      ← akshare 备用库（本文件定义，独立断点）

用法：
    from config_alt import ALT_DATA_DIR, ALT_CHECKPOINT_FILE, ...
"""
import os

# ── 依赖主 config 只为取工程路径，绝不反向修改 ──────────────
from config import PROJECT_DIR, TEMP_DIR, LOG_DIR, MARKET_DATA_DIR

# ============================================================
# 一、库根与路径
# ============================================================
# akshare 独立库根（与 market_data 平级）
ALT_DATA_DIR = r"D:\全量数据\alt_data"

# 主库根（只读引用，用于联查与校验；严禁写入）
TS_DATA_DIR = MARKET_DATA_DIR

# alt 库内部元数据目录（存放同步状态等，非行情数据）
ALT_META_DIR = os.path.join(ALT_DATA_DIR, "_meta")

# 断点文件（与主库 .backfill_checkpoint.json / .checkpoint.json 完全隔离）
ALT_CHECKPOINT_FILE = os.path.join(ALT_DATA_DIR, ".alt_checkpoint.json")

# 运行日志
ALT_LOG_DIR = os.path.join(ALT_DATA_DIR, "_logs")

# 临时/干跑产物（不入库）
ALT_TMP_DIR = os.path.join(TEMP_DIR, "alt_run")


def alt_table_dir(table):
    """alt 库中某表的目录（不保证存在）"""
    return os.path.join(ALT_DATA_DIR, table)


def ensure_alt_dirs():
    """确保 alt 库基础目录存在"""
    for d in (ALT_DATA_DIR, ALT_META_DIR, ALT_LOG_DIR, ALT_TMP_DIR):
        os.makedirs(d, exist_ok=True)


# ============================================================
# 二、安全边界（硬保护）
# ============================================================
# 任何写入前都会校验目标路径必须落在这些根之下
ALLOWED_WRITE_ROOTS = (ALT_DATA_DIR, ALT_TMP_DIR)

# 明令禁止写入的路径（主库根 + 其备份目录）
FORBIDDEN_WRITE_ROOTS = (
    TS_DATA_DIR,
    r"D:\全量数据\_migrate_backup",
    r"D:\全量数据\_backup_corrupt_20260917",
)


class AltPathViolation(RuntimeError):
    """写入路径越界（试图写主库或非授权目录）时抛出"""


# ── 越界尝试计数（N2，2026-09-19 二次审查）─────────────────────────
# 为什么需要它：
#   `audit.isolation_guard()` 的夹逼校验会对主库变更做归因，但归因是**推断**
#   （"变更不在 metadata/ 下 → 必然是主库自己写的"），不是证据。若 alt 真的
#   越界写了主库某表，夹逼会判成 external 并刷新基线，痕迹被抹掉。
#
#   而"alt 是否**试图**越界"是可以直接观测的：所有 alt 写路径都经过
#   assert_writable()，越界必然在此抛错。故在此记一个计数器，
#   夹逼期间若 >0，说明确实发生过越界尝试（即使被拦下，也应立即告警）。
#   这把"推断"补强为"证据 + 推断"。
#
# 读取：`violation_count()`；夹逼前后取差即可判定本次操作是否尝试越界。
_VIOLATION_LOG = []


def violation_count() -> int:
    """累计的越界尝试次数（含被拒绝的）"""
    return len(_VIOLATION_LOG)


def violation_records():
    """越界尝试明细（用于夹逼报告）"""
    return list(_VIOLATION_LOG)


def _record_violation(kind, path, forbidden):
    _VIOLATION_LOG.append({
        "at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "kind": kind,              # forbidden=命中禁区；outside=不在授权区
        "path": str(path),
        "forbidden": str(forbidden) if forbidden else "",
    })


def assert_writable(path):
    """写入前的硬保护：目标必须位于 alt 授权区，且不得触碰主库。

    这是分立方案的**最后一道防线** —— 任何落盘函数都应先调用它。
    越界时抛错，并记入 _VIOLATION_LOG（供隔离夹逼取证）。
    """
    p = os.path.abspath(path)
    for bad in FORBIDDEN_WRITE_ROOTS:
        b = os.path.abspath(bad)
        if p == b or p.startswith(b + os.sep):
            _record_violation("forbidden", p, b)
            raise AltPathViolation(
                f"拒绝写入：目标位于受保护的主库区域\n  目标: {p}\n  禁区: {b}"
            )
    if not any(p == os.path.abspath(r) or p.startswith(os.path.abspath(r) + os.sep)
               for r in ALLOWED_WRITE_ROOTS):
        _record_violation("outside", p, "")
        raise AltPathViolation(
            f"拒绝写入：目标不在 alt 授权区\n  目标: {p}\n"
            f"  授权: {ALLOWED_WRITE_ROOTS}"
        )
    return p


# ============================================================
# 三、akshare 专用限流参数
# ============================================================
# 与 tushare 的 300 次/分完全不同：akshare 上游是公开网站，
# 实测存在**累积式风控**（探测会加重封锁），必须低频且带静默。
#
# ⚠ 实测校准记录（2026-09-18）：
#   乐咕乐咕原设 30 次/分，连续 15 次请求后开始返回 429 Too Many Requests，
#   并出现 504 Gateway Time-out 与空响应（JSONDecodeError）。
#   → 实测限值明显低于 30，保守下调至 10 次/分（6 秒间隔）。
#   经验：公开数据站的隐性限流通常远低于其页面宣称；
#         首次接入一律从低调起，确认稳定后再试提速。
ALT_RATE_LIMITS = {
    # 稳定域名 —— 温和限速
    "legulegu":   10,   # 乐咕乐股（实测 30 会 429 → 下调）
    "10jqka":     12,   # 同花顺（板块清单/历史）
    "sina":       20,   # 新浪
    "sse":        20,   # 上交所
    "szse":       20,   # 深交所
    "cninfo":     20,   # 巨潮
    "push2ex":    15,   # 东财 push2ex（实测稳定）
    "datacenter": 15,   # 东财 datacenter-web（实测稳定）
    "jin10":      15,   # 金十数据（海外宏观；实测稳定，速率与 datacenter 齐平）
    "other":      15,
    # 不稳定域名 —— 严厉限速
    # optbbs：QVIX 波指的真实上游（实测 akshare 走 1.optbbs.com/d/csv/... 直读 CSV）。
    #         原代码按函数名误判为 push2his，归类错了；此处按真实域名单列，
    #         速率沿用原保守值 6/分（不改行为，只让归类正确，便于后续调参）。
    "optbbs":     6,
    "push2his":   6,    # 东财行情端点（实测被拦截过）
    "push2":      6,
}
ALT_RATE_DEFAULT = 12   # 每分钟请求数（未知域名）

# 触发风控后的静默（秒）。实测封禁会衰减，静默后可恢复。
ALT_BACKOFF_STEPS = [30, 60, 120, 300]     # 逐级静默
ALT_MAX_RETRY = 3                          # 每请求最大重试
ALT_SILENCE_ON_BLOCK = 180                 # 判定为"被拦"后的默认静默秒数

# 单次运行的最大请求数（防止一把打爆，0=不限）
ALT_MAX_REQUESTS_PER_RUN = 0

# ============================================================
# 四、域名 → 限流档位 的判定规则
# ============================================================
_HOST_RULES = [
    ("legulegu.com", "legulegu"),
    ("10jqka.com.cn", "10jqka"),
    ("sina.com.cn", "sina"),
    ("sse.com.cn", "sse"),
    ("szse.cn", "szse"),
    ("cninfo.com.cn", "cninfo"),
    ("push2ex.eastmoney.com", "push2ex"),
    # ⚠ 顺序敏感：datacenter-web 必须排在 datacenter 之前（子串匹配）
    ("datacenter-web.eastmoney.com", "datacenter"),
    ("datacenter.eastmoney.com", "datacenter"),
    # 金十（海外宏观真实上游：cdn.jin10.com / datacenter-api.jin10.com）
    ("jin10.com", "jin10"),
    # QVIX 波指真实上游（1.optbbs.com，直读 CSV；原按函数名误判为东财）
    ("optbbs.com", "optbbs"),
    ("push2his.eastmoney.com", "push2his"),
    ("push2.eastmoney.com", "push2"),
]


def host_tier(url):
    """从 URL 反推限流档位名"""
    u = (url or "").lower()
    for frag, tier in _HOST_RULES:
        if frag in u:
            return tier
    return "other"


def rate_for_url(url):
    """某 URL 的每分钟请求上限"""
    return ALT_RATE_LIMITS.get(host_tier(url), ALT_RATE_DEFAULT)


# ============================================================
# 五、日期格式规范（联查一致性的关键）
# ============================================================
# 主库实测：trade_date 为 'YYYYMMDD' 字符串（str dtype）
# akshare 实测：返回 datetime.date 对象
# → 归一化方向：**新库向主库对齐**，主库零改动
ALT_DATE_FORMAT = "%Y%m%d"
ALT_DATE_COL_DEFAULT = "trade_date"


def normalize_date_series(s):
    """把任意日期型 Series 归一化为 'YYYYMMDD' 字符串。

    覆盖 akshare 实测的各种形态：
      datetime.date / datetime64 / 'YYYY-MM-DD' / 'YYYYMMDD' / int
      **中文年月**（2026-09-21 新增）: '2026年08月份' / '2026年8月'

    ⚠ 中文年月为什么要单独处理：
      akshare 的 `macro_china_cpi`（国家统计局口径）返回的日期列是
      '2026年08月份' 这种格式，`pd.to_datetime` **完全无法解析**
      （实测 0/224 全部为 NaT）→ 日期列会整列变空 → 落盘后数据不可用。
      该格式转为 "YYYY-MM-01"（宏观月度数据取当月 1 日，与多数宏观表惯例一致）。
    """
    import pandas as pd

    t = s.astype(str).str.strip()

    # ---- 中文年月格式：'2026年08月份' / '2026年8月' → 'YYYY-MM-01' ----
    # 只对**匹配该格式的行**做替换，其余行原样保留（不干扰其他格式）
    cn = t.str.extract(r"^(\d{4})\s*年\s*(\d{1,2})\s*月", expand=True)
    if cn.shape[1] >= 2:
        mask = cn[0].notna() & cn[1].notna()
        if mask.any():
            t = t.where(~mask, cn[0] + "-" + cn[1].str.zfill(2) + "-01")

    out = pd.to_datetime(t, errors="coerce")
    return out.dt.strftime(ALT_DATE_FORMAT)


# 自检：确保从未把主库写进去
assert os.path.abspath(ALT_DATA_DIR) != os.path.abspath(TS_DATA_DIR), "alt 库不得与主库同路径"
