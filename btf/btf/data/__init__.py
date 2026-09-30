# -*- coding: utf-8 -*-
"""L0 数据层：DataFeed 协议 + Tushare Parquet 适配器 + 合成层。

模块清单（与 `btf/data/` 实存一一对应；分节便于定位）：
    —— 存储与登记 ——
    parquet_reader   三布局（by_year/single/metadata）统一读取 + 谓词下推 + 单位换算单点
    tables_meta      **18 表**元数据登记（05 §9.4 字典数字化：起点/单位换算标记/标的路由）
    core             YearTableStore：**唯一 IO 入口**（年表缓存/按日索引/线程安全）
    —— 数据源（DataFeed 协议实现）——
    feed             DataFeed 协议 + TushareParquetFeed + MixedDailyFeed（股票/ETF 混合表路由）
    memory           MemoryFeed（测试专用，脱盘）
    —— 合成与派生服务 ——
    state            交易日状态面板合成（stk_limit/etf_limit/suspend_d/namechange/stock_basic/daily 六源）
    liq              L0-LIQ 流动性状态机（index_pct/limit_stats/classify/state_of/预计算序列）
    fundamentals     每日基本面与 v7.7 L2 硬筛子（daily_basic/roe_pit/screen_l2）
    adjust           AdjustService（hfq/qfq——仅研究侧指标与展示，不进引擎记账，ADR-6）
    index_series     指数收盘序列（基准取数，缺日 fail-closed）
    index_universe   index_weight 月末快照复算 PIT 指数成分宇宙
    —— 治理 ——
    coverage         回测区间覆盖度守卫（必需表起点 / 降级表）
    quality          数据不变量校验 CC-1..CC-5（装配期门 + 产物 + 披露）
    version          数据内容指纹 DataVersion（meta/fast/full 三档）

纪律：只读主库；禁止反向依赖引擎层（契约 4）；上层不得直接 import `parquet_reader`（契约 9）；
`core` 不得依赖本包其它领域模块（契约 7）；域内模块互不依赖（契约 8，唯一已知例外 feed→state）。
"""
