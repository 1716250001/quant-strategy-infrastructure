# -*- coding: utf-8 -*-
"""
config_wind.py — Wind 第三库独立配置（WIND-LIB W-0，2026-10-22）
===============================================================
设计原则（严格遵守§69/§70）：
  1. **分立原则**：单向依赖（本文件 → config.py + config_alt.py），绝不反向修改；
  2. 第三库与主库 / alt 库 **物理分离**，FORBIDDEN 连 alt 也禁写（三库各自只写自己）；
  3. 管控指标换成**预算**（积分消耗 / 调用次数），区别于主库的完整性 / alt 的新鲜度；
  4. 绝对不进 DAILY_UPDATE_GROUPS，不进任何 automation；
  5. 凭据走环境变量 WIND_API_KEY，禁硬编码。

库布局：
    D:\\全量数据\\
    ├── market_data\\   ← tushare 主库（只读）
    ├── alt_data\\      ← akshare 备用库（只读）
    └── wind_data\\     ← Wind 第三库（本文件定义）
        ├── raw\\       原始响应，immutable 只增不改
        ├── norm\\series\\  序列表（允许增量合并）
        ├── norm\\snap\\   快照表（不可覆盖）
        ├── _catalog\\  目录索引 + 清单 CSV
        ├── _meta\\     写权白名单 + 积分预算台账
        └── _logs\\     运行日志

用法：
    from config_wind import WIND_DATA_DIR, assert_writable, WIND_API_KEY, ...
"""
import os
import datetime as _dt

# ── 依赖主 config 只为取工程路径，绝不反向修改 ──────────────
from config import PROJECT_DIR, TEMP_DIR, LOG_DIR, MARKET_DATA_DIR

# alt_data 根（需显式加到 FORBIDDEN）
ALT_DATA_DIR = r"D:\全量数据\alt_data"

# ============================================================
# 一、库根与路径
# ============================================================
WIND_DATA_DIR = r"D:\全量数据\wind_data"

# ── 原始层（immutable，先存后解析）────────────────────────────
WIND_RAW_DIR = os.path.join(WIND_DATA_DIR, "raw")

# ── 规范层 ─────────────────────────────────────────────────
WIND_NORM_DIR = os.path.join(WIND_DATA_DIR, "norm")
WIND_SERIES_DIR = os.path.join(WIND_NORM_DIR, "series")
WIND_SNAP_DIR = os.path.join(WIND_NORM_DIR, "snap")

# ── 目录索引 ────────────────────────────────────────────────
WIND_CATALOG_DIR = os.path.join(WIND_DATA_DIR, "_catalog")
WIND_CATALOG_FILE = os.path.join(WIND_CATALOG_DIR, "wind_catalog.parquet")

# ── 元数据（写权 + 预算台账）─────────────────────────────────
WIND_META_DIR = os.path.join(WIND_DATA_DIR, "_meta")
WIND_BUDGET_FILE = os.path.join(WIND_META_DIR, "budget_ledger.jsonl")
WIND_SNAP_META_DIR = os.path.join(WIND_SNAP_DIR, "_meta")   # 快照覆盖冲突记录

# ── 日志 ─────────────────────────────────────────────────
WIND_LOG_DIR = os.path.join(WIND_DATA_DIR, "_logs")

# ── 临时（临时产物不入库）─────────────────────────────────
WIND_TMP_DIR = os.path.join(TEMP_DIR, "wind_run")


def ensure_wind_dirs():
    """确保 wind_data 目录树存在（8 项）"""
    for d in (
        WIND_DATA_DIR, WIND_RAW_DIR, WIND_NORM_DIR, WIND_SERIES_DIR,
        WIND_SNAP_DIR, WIND_CATALOG_DIR, WIND_META_DIR, WIND_LOG_DIR,
    ):
        os.makedirs(d, exist_ok=True)


# ============================================================
# 二、安全边界（比 alt 更严：三库各自只写自己）
# ============================================================
ALLOWED_WRITE_ROOTS = (WIND_DATA_DIR, WIND_TMP_DIR)

FORBIDDEN_WRITE_ROOTS = (
    MARKET_DATA_DIR,              # 主库根（alt 也禁写 Wind）
    ALT_DATA_DIR,                 # alt 库根（新增：三库隔离）
    r"D:\量化策略\备份",          # 全局备份区（绝对保护）
    r"D:\全量数据\_migrate_backup",
    r"D:\全量数据\_backup_corrupt_20260917",
)


class WindPathViolation(RuntimeError):
    """Wind 写入路径越界（试图写主库 / alt 库 / 备份区）时抛出"""


# ── 越界尝试计数（沿用 alt 的 N2 设计）─────────────────────────
_VIOLATION_LOG = []


def violation_count() -> int:
    return len(_VIOLATION_LOG)


def violation_records():
    return list(_VIOLATION_LOG)


def _record_violation(kind, path, forbidden):
    _VIOLATION_LOG.append({
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "path": str(path),
        "forbidden": str(forbidden) if forbidden else "",
    })


def assert_writable(path):
    """Wind 写入硬保护（照抄 alt.assert_writable 的逻辑 + 更严的 FORBIDDEN）。

    调用链：wind_gate.py 每次落盘前必须过这里；catalog.py 写盘过这里；
    wind_snap 覆盖拒绝逻辑由 W-3 单独实现。
    """
    p = os.path.abspath(path)
    for bad in FORBIDDEN_WRITE_ROOTS:
        b = os.path.abspath(bad)
        if p == b or p.startswith(b + os.sep):
            _record_violation("forbidden", p, b)
            raise WindPathViolation(
                f"拒绝写入 Wind 授权区：目标位于禁区\n  目标: {p}\n  禁区: {b}"
            )
    if not any(p == os.path.abspath(r) or p.startswith(os.path.abspath(r) + os.sep)
               for r in ALLOWED_WRITE_ROOTS):
        _record_violation("outside", p, "")
        raise WindPathViolation(
            f"拒绝写入：目标不在 Wind 授权区\n  目标: {p}\n"
            f"  授权: {ALLOWED_WRITE_ROOTS}"
        )
    return p


# ============================================================
# 三、凭据（环境变量，禁硬编码）
# ============================================================
WIND_API_KEY = os.environ.get("WIND_API_KEY", "").strip()


# ============================================================
# 四、端点（实测仅两个）
# ============================================================
WIND_ENDPOINTS = {
    "analytics": "vserver_analytics_data/mcp/",   # 自然语言 get_financial_data
    "index":      "vserver_index_data/mcp/",      # get_index_kline
}


# ============================================================
# 五、限流与预算（软闸，CLI 无 balance 命令）
# ============================================================
WIND_CALL_INTERVAL = 0.6          # 两次调用间至少 0.6 秒
WIND_QPS_LIMIT = 2                # QPS 上限（保守：避免 37 分钟卡死）
WIND_MAX_RETRY = 3                # 拒单重试次数
WIND_RPC_TIMEOUT = 60             # RPC 超时秒（⚠ 禁设 200，曾致 GL8 卡死 37 分钟）

# 预算软闸（超出仅告警不阻断；CLI 无 balance 命令且 QUOTA_ERROR 是复合码）
WIND_DAILY_CALL_SOFT_LIMIT = 200           # 每日软闸：调用次数
WIND_DAILY_POINT_SOFT_LIMIT = 5000         # 每日软闸：积分消耗


# ============================================================
# 六、日期归一化（ISO8601 带时区 → YYYYMMDD）
# ============================================================
WIND_DATE_FORMAT = "%Y%m%d"


def normalize_date_iso8601(s):
    """把 Wind ISO8601 带时区的 datetime 字符串（'2026-10-22T08:30:00+08:00'）
    归一化为 'YYYYMMDD'（'20261022'），向主库 trade_date 对齐。

    实现：直接字符串切片 '[:10]' 然后 '-' 替换 → YYYYMMDD。
    不引 pandas（避免把 config 层级拉重）。
    """
    t = str(s).strip()
    # 取前 10 个字符（YYYY-MM-DD）
    date_part = t[:10]
    return date_part.replace("-", "")


# ============================================================
# 七、自检：三库路径必须两两不等
# ============================================================
assert os.path.abspath(WIND_DATA_DIR) != os.path.abspath(MARKET_DATA_DIR), \
    "wind_data 不得与主库同路径"
assert os.path.abspath(WIND_DATA_DIR) != os.path.abspath(ALT_DATA_DIR), \
    "wind_data 不得与 alt 库同路径"
assert os.path.abspath(MARKET_DATA_DIR) != os.path.abspath(ALT_DATA_DIR), \
    "主库与 alt 库路径异常（自检）"
