# -*- coding: utf-8 -*-
"""
tools/list_lof.py — 场内 ETF / LOF 清单统计
============================================
区分 ETF / LOF / 场外 / 其他，并输出 LOF 明细。

分类规则:
  - ETF: 代码以 5 开头(沪市 50/51/52/55/56/58/59) 或 1 开头(深市 159)
  - LOF: 代码以 16/18 开头(深市) 或 50 开头(沪市, 501 为 FOF-LOF)
  - 场外基金: .OF 后缀

重构要点：原实现把流程写在模块顶层（import 即执行），现封装为 main()。
"""
import os

import pandas as pd

from common.paths import FUND_DAILY_DIR, META_DIR
from common import reader
from tools.utils import classify_fund


def load_on_market():
    """读取在场内基金（库内有行情数据 = 已上市交易）并打上分类标签"""
    fb = pd.read_parquet(os.path.join(META_DIR, "fund_basic.parquet"))
    fb["delist_date"] = fb["delist_date"].fillna("")

    # fund_daily 已改为按年分区，无法再从文件名取代码（旧写法在 by_year
    # 下会把 "2026" 当成代码），改走 reader.codes_from_store。
    fund_files = set(reader.codes_from_store("fund_daily"))
    on_market = fb[fb["ts_code"].isin(fund_files)].copy()

    on_market["code"] = (on_market["ts_code"]
                         .str.replace(".SH", "").str.replace(".SZ", "").str.replace(".BJ", ""))
    on_market["suffix"] = on_market["ts_code"].str[-2:]
    # 分类: 使用 tools.utils.classify_fund (include_53=False, 与原版行为一致)
    on_market["market_class"] = on_market.apply(
        lambda r: classify_fund(r, include_53=False), axis=1)
    return on_market


def run():
    """打印场内 ETF/LOF 分类统计与 LOF 明细。

    供本文件 CLI 与统一入口 main.py 的 lof-list 子命令共用。
    """
    on_market = load_on_market()

    print("=" * 70)
    print("场内基金分类统计")
    print("=" * 70)
    print(on_market["market_class"].value_counts().to_string())

    lof = on_market[on_market["market_class"] == "LOF"].copy()
    lof = lof.sort_values("code").reset_index(drop=True)
    print(f"\n{'=' * 70}")
    print(f"场内LOF清单（共 {len(lof)} 只）")
    print(f"{'=' * 70}")
    print("\n按fund_type分布:")
    print(lof["fund_type"].value_counts().to_string())

    print("\n完整LOF列表:")
    print(f"{'序号':>4s} {'代码':12s} {'简称':30s} {'类型':8s} {'上市日':10s}")
    print("-" * 70)
    for i, (_, r) in enumerate(lof.iterrows(), 1):
        name = r["name"][:28] if isinstance(r["name"], str) else ""
        print(f"{i:4d}  {r['ts_code']:12s} {name:30s} {r['fund_type']:8s} {str(r['list_date']):10s}")

    etf = on_market[on_market["market_class"] == "ETF"]
    print(f"\n{'=' * 70}")
    print(f"场内ETF数量: {len(etf)} 只")
    print(f"{'=' * 70}")

    other = on_market[on_market["market_class"] == "其他"]
    if len(other) > 0:
        print(f"\n其他类型: {len(other)} 只")
        print(other[["ts_code", "name", "fund_type"]].to_string())


def main():
    run()


if __name__ == "__main__":
    main()
