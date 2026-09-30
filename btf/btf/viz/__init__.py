# -*- coding: utf-8 -*-
"""L5 可视化层：ChartSpec/ChartQuery 数据契约 + HTML 报告。

契约（13 号文档）：
    图表插件只消费 ChartQuery（runresult.v1 契约），不 import 引擎/分析内部
    报告结构含"假设与披露"强制章节（费率分段/规则版本/降级事件/应收简化 B1）
    单文件自包含（图表 data URI 内嵌）；可由 RunStore 产物再生成（幂等）
    Plotly 为主（HTML 交互）、matplotlib 为辅（PNG/PDF 附录）

纪律：表现层只认 api 与数据契约类型（03 §7.3 铁律 5）。
"""
