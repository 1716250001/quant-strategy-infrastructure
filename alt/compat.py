# -*- coding: utf-8 -*-
"""akshare 兼容补丁 —— 只在第三方库与本机依赖不兼容时使用，逐条注明根因。"""
_patched = False


def patch_akshare():
    """修复 ak.fund_etf_scale_szse 在 pandas>=2.0 下的 TypeError。

    根因：akshare 把 requests 的 r.content(bytes) 直接传给 pd.read_excel，
         pandas>=2.0 要求 file-like 对象。
    修法：用同一端点本地重实现，仅加 io.BytesIO 包装；列处理与原函数逐字一致。
    """
    global _patched
    if _patched:
        return
    import io
    import warnings

    import akshare as ak
    import pandas as pd
    import requests

    def _fund_etf_scale_szse_fixed():
        url = "https://fund.szse.cn/api/report/ShowReport"
        params = {"SHOWTYPE": "xlsx", "CATALOGID": "1000_lf",
                  "TABKEY": "tab1", "random": "0.07610353191740105"}
        headers = {
            "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/88.0.4324.150 Safari/537.36",
        }
        r = requests.get(url, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = pd.read_excel(io.BytesIO(r.content), engine="openpyxl",
                               dtype={"基金代码": str})
        df.rename(columns={"当前规模(份)": "基金份额"}, inplace=True)
        df = df[["基金代码", "基金简称", "基金类别", "投资类别", "上市日期",
                 "基金份额", "基金管理人", "基金发起人", "基金托管人", "净值"]]
        df["上市日期"] = pd.to_datetime(df["上市日期"], errors="coerce").dt.date
        df["基金份额"] = pd.to_numeric(
            df["基金份额"].astype(str).str.replace(",", "", regex=False), errors="coerce")
        df["净值"] = pd.to_numeric(df["净值"], errors="coerce")
        return df

    ak.fund_etf_scale_szse = _fund_etf_scale_szse_fixed
    _patched = True
