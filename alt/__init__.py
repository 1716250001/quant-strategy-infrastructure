# -*- coding: utf-8 -*-
"""
alt — akshare 备用数据源（独立库）
====================================
与 tushare 主库 **物理分离**，严格遵守分立方案：
    - 只读主库，绝不写入（config_alt.assert_writable 为最后防线）
    - 独立库根 D:\\全量数据\\alt_data
    - 独立断点 .alt_checkpoint.json / 独立更新状态 _meta/update_state.json
    - 独立限流（akshare 累积式风控，与 tushare 配额制不同）

模块：
    spec.py         表规格（布局/日期列/去重键/取数模式/窗口/阈值）—— 唯一真源
    rate.py         限流与风控退避（按域名档位 + 被拦长静默）
    io.py           归一化（日期对齐主库）+ 落盘（复用 common.parquet_store）
    backfill.py     历史回补编排（含 dry-run、断点三态）
    update.py       每日增量（single 全量刷新 / by_year 按日增量 + 复核窗口）
    verify_spec.py  建库前规格校验（七项核对，零落盘）
    reader.py       读取 + 与主库联查
    audit.py        数据体检（断点×落盘/重复/缺口/规模/隔离，零请求）

典型用法：
    python -m alt.backfill --list              # 看规格
    python -m alt.backfill --dry-run           # 回补预演
    python -m alt.backfill --tier P0           # 回补 P0
    python -m alt.verify_spec --tier P1        # 建库前校验规格
    python -m alt.update                       # 每日增量
    python -m alt.update --status              # 新鲜度体检（零请求）
    python -m alt.reader status                # 库现状
    python -m alt.audit                        # 数据体检（零请求）

依赖约定：
    本包**单向依赖** config.py / common/*（取工程路径与存储设施），
    config.py 及既有模块**不反向依赖** alt。故对主工程零侵入。
"""

__all__ = [
    "spec", "rate", "io", "backfill", "update", "verify_spec", "reader", "audit",
]
