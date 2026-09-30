# -*- coding: utf-8 -*-
"""
tools/config_check.py — 配置完整性检查（单一真源）
====================================================
为什么需要（真实教训）:
  编辑 config.py（1407 行单文件）时，回归断言 R9/R10/R11 三次抓到
  "改一处、顺手删掉别处"：
    ① _SINGLE_MODES 常量未随 docstring 同步
    ② TABLE_DATE_COL 被误删 7 个条目
       (cb_issue/cb_rating/cb_share/weekly/monthly/index_weekly/index_monthly)
    ③ （2026-09-19 本次）回归的 CLI 白名单未随新增命令更新
  三次都不是"写错"，而是"误删"。

把判据集中到本模块，供两个消费者共用:
  - tools/regress_pipeline.py  R11 回归断言（改数据层后必跑）
  - tools/doctor.py            环境自检（排查问题的第一站，秒级）

⚠ 设计纪律：不要让两处各维护一份下限表 —— 那正是 R10 揭示的
  "两个本该联动的结构各管一摊，新增时只改一处" 的复发形态。

判据（八条）:
  ① 条目数下限       只在**异常缩小**时报警；不做精确匹配
                     （精确匹配是脆弱断言，每次新增接口都要改）
                     ⚠ 它只是**粗网**：真正的精准防护是 ⑥⑦⑧ 三条交叉校验
  ② 关键常量在位     存在且类型合法、非空
  ③ 跨结构一致性     登记的表都应在 DB_AUDIT_PKEYS 里有归宿
                     （显式 None 也算归宿 = "跳过重复检查"；缺失才是问题）
  ④ 组结构完整       DAILY_UPDATE_GROUPS 必须有 core 组
  ⑤ 豁免须给理由     DAILY_UPDATE_EXEMPT 的每个条目都必须写明原因
                     （无理由的豁免 = 静默漏登的垃圾桶）
  ⑥ 主键列名有效性   已建库表的 DB_AUDIT_PKEYS 键名必须**真实存在于数据列**
                     （2026-09-19 独立审查发现：fund_company 配了不存在的
                      ts_code，判重从此静默跳过 —— 而 ③ 只查"键是否存在"，
                      查不出"键名是否真实"，即"断言被敷衍满足"）
  ⑦ 反向归属         磁盘上每张表都必须在配置里有归宿（或属已知的外部表）
                     （捕捉"删掉某表条目"这类误删 —— 这是 ① 的精准补充：
                      条目数下限有容差，而本判据只要表还在磁盘上就能发现）
  ⑧ 正向覆盖         配置里每张表必须"已建库 / 已豁免 / 死结"，不允许悬空
                     （2026-09-19 实证：gz_index/wz_index 配了却从未拉取、
                      无任何归宿、无告警 —— 数据缺口是静默的）
                     ⚠ 只对**已建库**表可判；未建库表的"该不该存在"由本判据管

判据 ⑥⑦⑧ 需要读磁盘（只读 parquet **元数据**，不读数据本体，全库秒级）。

用法:
    from tools.config_check import check_config
    res = check_config()
    if not res.ok:
        print(res.failures)
    else:
        print(res.summary)
"""
import os
import sys

# 允许作为脚本/包两种方式调用（与 regress_pipeline 一致）
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)


# ── ① 条目数下限（低于此值说明被批量误删；有意精简时同步下调此表）──
# 格式: (配置名, 下限)
FLOORS = [
    ("TABLE_DATE_COL", 55),
    ("BACKFILL_TARGETS", 65),
    ("DB_AUDIT_PKEYS", 60),
    ("API_RATE_LIMIT", 12),
    ("BACKFILL_INDEX_CODES", 10),
    ("BACKFILL_GLOBAL_INDEX_CODES", 20),
]


# ── 非 tushare backfill 管理的库表（由其他流水线维护）──
# 用途: 判据 ⑦（反向归属）的白名单。这些目录有数据但**本就不该**出现在
#       BACKFILL_TARGETS 里，若不列白名单会产生稳定误报（"狼来了"）。
NON_BACKFILL_TABLES = {
    "metadata":       "元数据目录（stock_basic / fund_basic / trade_cal / cb_basic 等）",
    "income":         "download-full 下载（利润表）",
    "balancesheet":   "download-full 下载（资产负债表）",
    "cashflow":       "download-full 下载（现金流量表）",
    "fina_indicator": "download-full 下载（财务指标）",
    "fund_nav":       "fund_nav_update 每日更新（基金净值）",
    "custom_index":   "build_micro_index 产出（本地自建指数）",
}


class ConfigCheckResult:
    """检查结果（一份数据，两个消费者各自渲染）"""

    def __init__(self):
        self.failures = []          # list[str]：每条都是可直接展示的完整句子
        self.checks_done = []       # 已执行的判据名（用于摘要）
        self.summary_extra = ""     # 磁盘类判据的摘要（⑥⑦⑧）

    @property
    def ok(self):
        return not self.failures

    @property
    def summary(self):
        if not self.ok:
            return f"{len(self.failures)} 项异常"
        return (f"(下限检查 {len(FLOORS)} 项通过 | 关键常量在位 | "
                f"判重主键无缺口 | 组结构完整{self.summary_extra})")

    def render(self, indent="      "):
        """把失败项渲染成多行文本（供断言消息使用）"""
        return "\n".join(indent + f for f in self.failures)


def _disk_tables(root):
    """磁盘上有 parquet 的表目录名集合"""
    out = set()
    if not os.path.isdir(root):
        return out
    for e in os.scandir(root):
        if not e.is_dir() or e.name.startswith("."):
            continue
        try:
            names = os.listdir(e.path)
        except OSError:
            continue
        if any(f.endswith(".parquet") for f in names):
            out.add(e.name)
    return out


def _pk_column_names(table, root):
    """读该表任一 parquet 文件的**列名**（只读元数据，不读数据本体）。

    返回 (colnames, err)：
        colnames=None 表示无法读取（表不存在/无文件/读取失败）
    """
    d = os.path.join(root, table)
    if not os.path.isdir(d):
        return None, "目录不存在"
    files = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    if not files:
        return None, "无 parquet 文件"
    for f in (files[0], files[-1]):        # 首尾各试一次（防个别文件损坏）
        try:
            import pyarrow.parquet as pq
            return set(pq.ParquetFile(os.path.join(d, f)).schema_arrow.names), None
        except Exception as e:
            err = str(e)[:50]
    return None, err


def check_config(check_disk=True):
    """执行全部配置完整性判据。

    参数:
        check_disk: 是否执行需要读磁盘的判据 ⑥⑦⑧（默认 True；全库秒级）
    返回:
        ConfigCheckResult
    """
    from config import (
        TABLE_DATE_COL, BACKFILL_TARGETS, DB_AUDIT_PKEYS,
        DAILY_UPDATE_GROUPS, DAILY_UPDATE_EXEMPT, API_RATE_LIMIT,
        BACKFILL_INDEX_CODES, BACKFILL_GLOBAL_INDEX_CODES,
        MARKET_DATA_DIR, META_DIR, DAILY_UPDATE_EXTRA,
    )

    res = ConfigCheckResult()

    # ① 条目数下限
    present = {
        "TABLE_DATE_COL": TABLE_DATE_COL,
        "BACKFILL_TARGETS": BACKFILL_TARGETS,
        "DB_AUDIT_PKEYS": DB_AUDIT_PKEYS,
        "API_RATE_LIMIT": API_RATE_LIMIT,
        "BACKFILL_INDEX_CODES": BACKFILL_INDEX_CODES,
        "BACKFILL_GLOBAL_INDEX_CODES": BACKFILL_GLOBAL_INDEX_CODES,
    }
    shrunk = []
    for name, floor in FLOORS:
        val = present.get(name)
        if val is None:
            shrunk.append(f"{name}=缺失(下限{floor})")
        elif len(val) < floor:
            shrunk.append(f"{name}={len(val)}(下限{floor})")
    res.checks_done.append("floors")
    if shrunk:
        res.failures.append(
            f"配置疑似被误删（条目数低于下限）: {shrunk}\n"
            f"    → 若是有意精简，请同步下调 tools/config_check.py 的 FLOORS")

    # ② 关键常量存在且非空
    for const, val in [("MARKET_DATA_DIR", MARKET_DATA_DIR),
                       ("META_DIR", META_DIR)]:
        if not val or not isinstance(val, str):
            res.failures.append(f"{const} 缺失或非法: {val!r}")
    res.checks_done.append("constants")

    # ③ 跨结构一致性：每张登记的表都应在 DB_AUDIT_PKEYS 里有归宿
    #    ⚠ 覆盖**全部模式**，不只 by_code/by_date：
    #      db_audit.audit_duplicates 遍历的是 DB_AUDIT_PKEYS，
    #      不在其中的表会被**静默跳过判重** —— paged/by_param/once 同样需要归宿。
    no_pk = []
    for name in set(BACKFILL_TARGETS) | set(DAILY_UPDATE_EXTRA):
        if name not in DB_AUDIT_PKEYS:
            no_pk.append(name)
    res.checks_done.append("pk_coverage")
    if no_pk:
        res.failures.append(
            f"以下表缺少 DB_AUDIT_PKEYS 归宿（应配主键或显式 None）: "
            f"{sorted(no_pk)}")

    # ④ 每日更新组非空且有 core
    res.checks_done.append("groups")
    if not DAILY_UPDATE_GROUPS.get("core"):
        res.failures.append("DAILY_UPDATE_GROUPS 缺 core 组")

    # ⑤ 每日更新豁免项必须写明原因（否则豁免成了"静默漏登"的垃圾桶）
    #    ⚠ 值的实际类型是**字符串**（豁免原因本身）。此处刻意做宽容判定：
    #      若未来改成 {"reason": ...} 形式也接受 —— 不要假设结构，
    #      否则会引入一条"每天必报"的假断言（狼来了）。
    def _has_reason(v):
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, dict):
            return bool(str(v.get("reason", "")).strip())
        return False

    no_reason = [k for k, v in (DAILY_UPDATE_EXEMPT or {}).items()
                 if not _has_reason(v)]
    if no_reason:
        res.failures.append(
            f"DAILY_UPDATE_EXEMPT 以下条目未写原因（豁免必须给理由）: "
            f"{sorted(no_reason)}")

    # ⑥⑦⑧ 需要读磁盘（只读 parquet 元数据）
    if not check_disk:
        res.checks_done.append("disk_skipped")
        return res

    disk = _disk_tables(MARKET_DATA_DIR)

    # ⑥ 主键列名有效性：配置的键名必须真实存在于数据列中
    #    ⚠ 盲区（如实说明）：未建库的表无法验证，跳过；pk 为 None 是合法的
    #      "跳过判重"，不做列名检查。
    bad_pk, unverified = [], 0
    for name, pk in DB_AUDIT_PKEYS.items():
        if not pk:
            continue
        if name not in disk:
            unverified += 1
            continue
        cols, err = _pk_column_names(name, MARKET_DATA_DIR)
        if cols is None:
            bad_pk.append(f"{name}(读取失败: {err})")
            continue
        miss = [c for c in pk if c not in cols]
        if miss:
            bad_pk.append(f"{name}(缺列 {miss}，实际列示例 {sorted(cols)[:6]})")
    res.checks_done.append("pk_columns")
    if bad_pk:
        res.failures.append(
            f"以下表的 DB_AUDIT_PKEYS 键名不存在于数据列中 → 判重会**静默失效**: "
            f"{bad_pk}\n"
            f"    → 请改为真实存在的列，或显式 None（=跳过判重）")

    # ⑦ 反向归属：磁盘上的表必须在配置里有归宿（或属已知外部表）
    #    这是"条目被误删"的精准捕捉 —— ① 的条目数下限有容差，本判据没有。
    orphan = sorted(t for t in disk
                    if t not in BACKFILL_TARGETS
                    and t not in DAILY_UPDATE_EXTRA
                    and t not in NON_BACKFILL_TABLES)
    res.checks_done.append("orphan_disk")
    if orphan:
        res.failures.append(
            f"磁盘上以下表在配置中无归属（BACKFILL_TARGETS / DAILY_UPDATE_EXTRA）: "
            f"{orphan}\n"
            f"    → 若是新表请补登记；若是外部流水线产出，请加入 "
            f"config_check.NON_BACKFILL_TABLES 并写明来源")

    # ⑧ 正向覆盖：配置的每张表必须"已建库 / 已豁免 / 死结"，不允许悬空
    #    （tier X 或 enabled=False 本身已是"明确不拉"的标记）
    leaked = []
    for name, conf in BACKFILL_TARGETS.items():
        if conf.get("tier") == "X" or conf.get("enabled") is False:
            continue
        if name in DAILY_UPDATE_EXEMPT:
            continue
        if name in disk:
            continue
        leaked.append(f"{name}(tier={conf.get('tier') or '-'},"
                      f"mode={conf.get('mode')})")
    res.checks_done.append("coverage")
    if leaked:
        res.failures.append(
            f"以下表已配置但**从未拉取、且无任何归宿**（静默数据缺口）: {leaked}\n"
            f"    → 请三选一：补拉取 / 加入 DAILY_UPDATE_EXEMPT（附原因）/ "
            f"标 tier=X 或 enabled=False")

    res.summary_extra = (f" | 主键列名{len(DB_AUDIT_PKEYS) - unverified}张已验"
                         f"(未建库{unverified}张跳过) | 磁盘归属无孤儿")
    return res


def main(argv=None):
    """独立运行：python -m tools.config_check"""
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    except Exception:
        pass
    r = check_config()
    print("=" * 72)
    print("  config.py 完整性检查")
    print("=" * 72)
    if r.ok:
        print(f"  [通过] {r.summary}")
        return 0
    for f in r.failures:
        print(f"  [失败] {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
