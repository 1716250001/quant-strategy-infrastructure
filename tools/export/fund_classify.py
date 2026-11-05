# -*- coding: utf-8 -*-
"""
tools/utils.py — 工具脚本共用函数
=================================
从 list_lof.py 和 export_lof.py 提取的公共分类逻辑。

包含:
  - classify_fund: 场内基金分类 (ETF/LOF/场外/其他)
"""

def classify_fund(row, include_53=True):
    """
    场内基金分类: 根据代码前缀判断 ETF / LOF / 场外基金 / 其他

    分类规则:
      .OF后缀 = 场外基金
      沪市(.SH):
        50xxxx: LOF/FOF
        51/52/55/56/58/59: ETF
        53xxxx: 科创板ETF (include_53=True时归ETF)
      深市(.SZ):
        159/158: ETF
        16/18: LOF

    参数:
        row: DataFrame行, 需含 'code' 和 'suffix' 列
        include_53: 是否将53前缀(科创板ETF)归为ETF
                    list_lof原版不含53, export_lof原版含53, 默认True(更完整)
    返回: 分类字符串
    """
    code = str(row["code"])
    suffix = str(row["suffix"])

    if suffix == "OF":
        return "场外基金"

    if suffix == "SH":
        etf_prefixes = ("51", "52", "55", "56", "58", "59")
        if include_53:
            etf_prefixes = ("51", "52", "53", "55", "56", "58", "59")
        if code.startswith("50"):
            return "LOF"
        elif code.startswith(etf_prefixes):
            return "ETF"
        else:
            return "其他"

    if suffix == "SZ":
        if code.startswith("159") or code.startswith("158"):
            return "ETF"
        elif code.startswith(("16", "18")):
            return "LOF"
        else:
            return "其他"

    return "其他"
