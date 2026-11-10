# -*- coding: utf-8 -*-
"""全库数值域量级锚（L1）—— R11-b 提案稿，**须赤潮逐行复核后才准启用全库跑**。

生成口径：
  · 每表 1–2 列，优先金额/数量/比率列；跳过纯文本/日期/枚举列；
  · 低界：历史 q0.001（不可负列收口到 0；可负列 ×1.5 放宽）；
  · 高界：历史 q0.999 ×2 放宽；比率类另按业务上限截断；
  · why 只允许三类来源：业务常识／历史 0.1–99.9 分位（附脚本）／勾稽推导；
  · 数据基线：2026-11-10 快照（2026 年分片不参与分位，留作未来漂移观察）。

⚠ 单位陷阱（逐字声明，勿改口径）：
  · fund_daily.amount / daily.amount＝千元；moneyflow.net_mf_amount＝万元；
  · fund_share.fd_share＝万份；daily_basic.total_share＝万股，balancesheet.total_share＝股（差 1e4）。
"""

SCALE_ANCHORS = {
    "market_data/adj_factor": {
        "adj_factor": {"lo": 0.0, "hi": 11541.968,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.979／q999=5770.98，全样本极值 0.645–10055.6，n=15942422，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/balancesheet": {
        "total_share": {"lo": 0.0, "hi": 558294000000.0,
                   "why": "单位：股（与 daily_basic.total_share 万股差 1e4）；历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.79147e+11，全样本极值 0–3.56407e+11，n=392577，脚本 tools/db/scale_check.py --stats）"},
        "total_assets": {"lo": 0.0, "hi": 67318092000000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=517565／q999=3.3659e+13，全样本极值 0–5.34778e+13，n=394204，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/bc_otcqt": {
        "buy_yield": {"lo": -12112.74315, "hi": 40.91439,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-2.1207／q999=2.5，全样本极值 -8075.16–37.1949，n=2539009，脚本 tools/db/scale_check.py --stats）"},
        "coupon_rate": {"lo": 0.0, "hi": 9.96,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=4.98，全样本极值 0–5.24，n=2539009，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/block_trade": {
        "amount": {"lo": 0.0, "hi": 1590297.56963,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=6.9638／q999=128698，全样本极值 0.0135–1.44573e+06，n=601955，脚本 tools/db/scale_check.py --stats）"},
        "vol": {"lo": 0.0, "hi": 181511.55,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.07737／q999=17573.6，全样本极值 0.0003–165010，n=601955，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cashflow": {
        "net_profit": {"lo": -137715136652.23502, "hi": 407842600000.00006,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.77223e+10／q999=1.54487e+11，全样本极值 -9.18101e+10–3.70766e+11，n=206820，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cb_daily": {
        "close": {"lo": 0.0, "hi": 3762.44,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1689.62，全样本极值 0–3420.4，n=770676，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 3299600.51894,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=945007，全样本极值 0–2.99964e+06，n=770792，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cb_issue": {
        "issue_price": {"lo": 0.0, "hi": 200.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=100／q999=100，全样本极值 100–100，n=1081，脚本 tools/db/scale_check.py --stats）"},
        "issue_size": {"lo": 0.0, "hi": 973.2,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.110042／q999=486.6，全样本极值 0.106–500，n=1081，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cb_share": {
        "issue_size": {"lo": 0.0, "hi": 100000000000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.14e+08／q999=5e+10，全样本极值 1.06e+07–5e+10，n=102302，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cn_cpi": {
        "nt_val": {"lo": 0.0, "hi": 256.288,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=95.2264／q999=128.144，全样本极值 94.1–128.4，n=513，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cn_gdp": {
        "gdp": {"lo": 0.0, "hi": 2784600.972,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=704.81／q999=1.3923e+06，全样本极值 679–1.40188e+06，n=179，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cn_m": {
        "m0": {"lo": 0.0, "hi": 301362.35552,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=179.955／q999=150681，全样本极值 178.46–151400，n=585，脚本 tools/db/scale_check.py --stats）"},
        "m1": {"lo": 0.0, "hi": 2376560.21904,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=745.868／q999=1.18828e+06，全样本极值 745.08–1.1932e+06，n=585，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/cn_ppi": {
        "ppi_yoy": {"lo": -12.33, "hi": 44.607,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-8.08428／q999=22.3035，全样本极值 -8.22–24，n=378，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/daily": {
        "close": {"lo": 0.0, "hi": 2861.1,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.93／q999=609.526，全样本极值 0.01–2601，n=16451114，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 99041412.0784,
                   "why": "单位：千元（tushare daily.amount 千元）；历史 0.1–99.9 分位（≤2025 分片包络 q001=0.2032／q999=1.08719e+07，全样本极值 0.001–9.00376e+07，n=16451100，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/daily_basic": {
        "close": {"lo": 0.0, "hi": 5393.336,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.93／q999=2696.67，全样本极值 0.09–3275，n=17587142，脚本 tools/db/scale_check.py --stats）"},
        "total_mv": {"lo": 0.0, "hi": 885016240.33669,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=154.4／q999=1.90616e+08，全样本极值 154.4–8.0456e+08，n=17586911，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/daily_info": {
        "amount": {"lo": 0.0, "hi": 34287.44332,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=17143.7，全样本极值 0–19435.8，n=97401，脚本 tools/db/scale_check.py --stats）"},
        "total_mv": {"lo": 0.0, "hi": 1296591.95666,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=648296，全样本极值 0–651470，n=66612，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/express": {
        "n_income": {"lo": -68719272450.0, "hi": 212258200000.00003,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-9.85578e+09／q999=8.56572e+10，全样本极值 -4.58128e+10–1.92962e+11，n=27376，脚本 tools/db/scale_check.py --stats）"},
        "revenue": {"lo": -172285278.555, "hi": 3169740200000.0005,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=21861.5／q999=4.94879e+11，全样本极值 -1.14857e+08–2.88158e+12，n=27416，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fina_indicator": {
        "revenue_ps": {"lo": -2.14065, "hi": 1869925.67652,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.137929／q999=205.696，全样本极值 -1.4271–1.69993e+06，n=245543，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/forecast": {
        "net_profit_max": {"lo": -8700000.0, "hi": 19545790.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.7492e+06／q999=7.26047e+06，全样本极值 -5.8e+06–1.77689e+07，n=94326，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund/qdii6": {
        "close": {"lo": 0.0, "hi": 2.512292,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.178588／q999=1.25615，全样本极值 0.17–1.397，n=10834，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 1750059.5211,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.104／q999=595817，全样本极值 0.046–1.59096e+06，n=10834，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund/qdii7": {
        "close": {"lo": 0.0, "hi": 13.30088,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.67092／q999=6.65044，全样本极值 0.654–6.687，n=6559，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 48455429.412712,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.06812／q999=2.42277e+07，全样本极值 0.282–2.59846e+07，n=6559，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund_adj": {
        "adj_factor": {"lo": 0.0, "hi": 50.728,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0137／q999=25.364，全样本极值 0.0137–30.0024，n=2767841，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund_daily": {
        "close": {"lo": 0.0, "hi": 270.51315,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.173／q999=135.257，全样本极值 0.132–155.1，n=2830001，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 159531483.0021,
                   "why": "单位：千元（tushare fund_daily.amount 千元）；历史 0.1–99.9 分位（≤2025 分片包络 q001=0.001／q999=1.93037e+07，全样本极值 0–1.45029e+08，n=2830001，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund_div": {
        "ear_amount": {"lo": -41821750.83, "hi": 10123379999.999994,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=5.06169e+09，全样本极值 -2.78812e+07–6.72083e+09，n=17773，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund_nav": {
        "unit_nav": {"lo": 0.0, "hi": 219.40437,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.1257／q999=109.702，全样本极值 0.0234–141.78，n=21451960，脚本 tools/db/scale_check.py --stats）"},
        "accum_nav": {"lo": -0.7095, "hi": 103.83219,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0249／q999=12.4271，全样本极值 -0.473–94.3929，n=21338768，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fund_share": {
        "fd_share": {"lo": 0.0, "hi": 35389281.04,
                   "why": "单位：万份（tushare fund_share 万份）；历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.76946e+07，全样本极值 0–2.174e+07，n=1873040，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_basic": {
        "multiplier": {"lo": 0.0, "hi": 40000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=50／q999=20000，全样本极值 50–20000，n=712，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_daily": {
        "close": {"lo": -0.15, "hi": 675179.6,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=42.2／q999=337590，全样本极值 -0.1–391660，n=2543394，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 428961504.276,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.14481e+08，全样本极值 0–3.59888e+08，n=2680296，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_holding": {
        "vol": {"lo": 0.0, "hi": 26227592.6,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.24493e+06，全样本极值 0–2.38433e+07，n=17657002，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_index_daily": {
        "close": {"lo": 0.0, "hi": 14498.2113,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=645.595／q999=7249.11，全样本极值 641.438–7300.99，n=24295，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 57018963.636001,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3.29317e+06／q999=2.85095e+07，全样本极值 3.2535e+06–3.04424e+07，n=10335，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_settle": {
        "settle": {"lo": 0.0, "hi": 669029.94,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=40.65／q999=334515，全样本极值 37–379840，n=1473745，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_weekly_detail": {
        "amount": {"lo": 0.0, "hi": 186615.661277,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=84950，全样本极值 0–169651，n=20587，脚本 tools/db/scale_check.py --stats）"},
        "vol": {"lo": 0.0, "hi": 51316804.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.98973e+07，全样本极值 0–4.66516e+07，n=20548，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_weekly_monthly": {
        "close": {"lo": -0.15, "hi": 676016.8,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=42.2／q999=338008，全样本极值 -0.1–355900，n=370993，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 389782005.338,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.32378e+08，全样本极值 0–3.54347e+08，n=397699，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fut_wsr": {
        "vol": {"lo": -51160.5, "hi": 17119300.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-204／q999=7.25497e+06，全样本极值 -34107–1.5563e+07，n=2207641，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/fx_daily": {
        "ask_close": {"lo": 0.0, "hi": 121519.752,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0488164／q999=60759.9，全样本极值 0.03216–67767，n=646277，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/ggt_daily": {
        "buy_amount": {"lo": 0.0, "hi": 1446.86556,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=2.23／q999=723.433，全样本极值 0–845.37，n=2735，脚本 tools/db/scale_check.py --stats）"},
        "sell_amount": {"lo": 0.0, "hi": 1374.6708,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.74468／q999=687.335，全样本极值 0–841，n=2735，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/ggt_top10": {
        "close": {"lo": 0.0, "hi": 1616.207,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.056606／q999=808.104，全样本极值 0.029–904，n=41107，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 49447711328.32774,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=6.92992e+06／q999=2.47239e+10，全样本极值 5.55139e+06–3.32835e+10，n=21680，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/hibor": {
        "on": {"lo": 0.0, "hi": 1.165733,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0353518／q999=0.582866，全样本极值 0.035–0.66214，n=247，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/hk_hold": {
        "vol": {"lo": 0.0, "hi": 65630713123.842026,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1／q999=3.28154e+10，全样本极值 1–3.49232e+10，n=5492641，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/hsgt_top10": {
        "close": {"lo": 0.0, "hi": 4685.9099,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=2.87236／q999=2342.95，全样本极值 1.037–2601，n=41690，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 18516763030.7,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.30511e+07／q999=8.36851e+09，全样本极值 1.10494e+07–1.68334e+10，n=41770，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/income": {
        "n_income": {"lo": -137715136652.23502, "hi": 407842600000.00006,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.19826e+10／q999=1.48381e+11，全样本极值 -9.18101e+10–3.70766e+11，n=410010，脚本 tools/db/scale_check.py --stats）"},
        "revenue": {"lo": -1476222622.32, "hi": 3649984800000.0005,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-4.25693e+06／q999=8.04083e+11，全样本极值 -9.84148e+08–3.31817e+12，n=410166，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_daily": {
        "close": {"lo": 0.0, "hi": 205229.85923,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.1602／q999=52531.4，全样本极值 0–186573，n=33737154，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": -0.1065, "hi": 17618701056.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.86825e+09，全样本极值 -0.071–1.6017e+10，n=23191464，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_dailybasic": {
        "total_mv": {"lo": 0.0, "hi": 153139206111913.88,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3.99114e+10／q999=7.65696e+13，全样本极值 3.8876e+10–7.7e+13，n=50843，脚本 tools/db/scale_check.py --stats）"},
        "pb": {"lo": 0.0, "hi": 29.54446,
                   "why": "业务常识：pb 上限 10000；历史 0.1–99.9 分位（≤2025 分片包络 q001=0.97／q999=14.7722，全样本极值 0.82–15.01，n=50843，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_global": {
        "close": {"lo": 0.0, "hi": 321734.6585,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=4.40499／q999=160867，全样本极值 4.4–164456，n=247876，脚本 tools/db/scale_check.py --stats）"},
        "vol": {"lo": 0.0, "hi": 53627043.715212,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.68135e+07，全样本极值 0–3.93929e+07，n=175606，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_monthly": {
        "close": {"lo": 0.0, "hi": 25662.734594,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=143.973／q999=12831.4，全样本极值 143.8–13027.2，n=2855，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 2.1214797290345644e+16,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.06074e+16，全样本极值 0–1.07352e+16，n=2855，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_weekly": {
        "close": {"lo": 0.0, "hi": 29451.54758,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=14725.8，全样本极值 0–15006.3，n=12102，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 6129927715801513.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=3.06496e+15，全样本极值 0–3.3027e+15，n=12102，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/index_weight": {
        "weight": {"lo": 0.0, "hi": 83.7892,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=15.512，全样本极值 0–76.172，n=1255770，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/margin": {
        "rzye": {"lo": 0.0, "hi": 2962304375533.236,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=9.75154e+06／q999=1.48115e+12，全样本极值 3.65118e+06–1.48291e+12，n=7503，脚本 tools/db/scale_check.py --stats）"},
        "rqye": {"lo": 0.0, "hi": 216660507329.68,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.0833e+11，全样本极值 0–1.08416e+11，n=7503，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/margin_detail": {
        "rzye": {"lo": 0.0, "hi": 84851206785.40001,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=5360／q999=3.89046e+10，全样本极值 0–7.71375e+10，n=6046836，脚本 tools/db/scale_check.py --stats）"},
        "rqye": {"lo": 0.0, "hi": 21155169287.221203,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=4.64511e+09，全样本极值 0–1.9232e+10，n=6044052，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/moneyflow": {
        "net_mf_amount": {"lo": -2569367.64, "hi": 976398.456,
                   "why": "单位：万元（tushare moneyflow 万元）；历史 0.1–99.9 分位（≤2025 分片包络 q001=-77137.4／q999=79944.3，全样本极值 -1.71291e+06–887635，n=12982017，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/moneyflow_hsgt": {
        "north_money": {"lo": -26867.28, "hi": 950091.67808,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-16086.7／q999=475046，全样本极值 -17911.5–510101，n=2814，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/monthly": {
        "close": {"lo": 0.0, "hi": 2439.8,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.164／q999=615.23，全样本极值 0.019–2218，n=512236，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 727273161149.81,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=329236／q999=1.77649e+11，全样本极值 96–6.61157e+11，n=512236，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/new_share": {
        "amount": {"lo": 0.0, "hi": 1255167.688,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=575.725／q999=627584，全样本极值 120–1.05748e+06，n=3030，脚本 tools/db/scale_check.py --stats）"},
        "pe": {"lo": 0.0, "hi": 1911.239,
                   "why": "业务常识：pe 上限 100000；历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=573.534，全样本极值 0–1737.49，n=3030，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/opt_basic": {
        "exercise_price": {"lo": 0.0, "hi": 19400.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.5／q999=9700，全样本极值 0.45–10000，n=30730，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/opt_daily": {
        "close": {"lo": 0.0, "hi": 284926.2,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=142463，全样本极值 0–185500，n=19146600，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 441475729.2,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=12569.8，全样本极值 0–4.01342e+08，n=19179658，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/pledge_detail": {
        "holding_amount": {"lo": 0.0, "hi": 523488998.201332,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.61744e+08，全样本极值 0–2.7095e+08，n=208242，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/pledge_stat": {
        "total_share": {"lo": 0.0, "hi": 47718027.08,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=4440／q999=2.3859e+07，全样本极值 4000–3.19244e+07，n=1659876，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/repo_daily": {
        "close": {"lo": 0.0, "hi": 99.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=36.7919，全样本极值 0–90，n=188810，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 1546913233.324,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=7.73457e+08，全样本极值 0–1e+09，n=197502，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/repurchase": {
        "amount": {"lo": -118620.0, "hi": 24254112459.57532,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1281.05／q999=1.21271e+10，全样本极值 -79080–1.5e+10，n=54432，脚本 tools/db/scale_check.py --stats）"},
        "vol": {"lo": 0.0, "hi": 3891134399.999943,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=249.577／q999=1.94557e+09，全样本极值 1–2.415e+09，n=35857，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/sge_daily": {
        "close": {"lo": 0.0, "hi": 2695.9448,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.2／q999=1347.97，全样本极值 0–1427.7，n=66799，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 1518101141551.1987,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=7.59051e+11，全样本极值 0–8.28865e+11，n=51833，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/share_float": {
        "float_share": {"lo": 0.0, "hi": 188457945214.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=11／q999=1.27765e+10，全样本极值 1–1.71325e+11，n=5990195，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/shibor": {
        "on": {"lo": 0.0, "hi": 5.957696,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.56294／q999=2.97885，全样本极值 0.441–3.282，n=2685，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/stk_holdertrade": {
        "total_share": {"lo": 0.0, "hi": 297513338512.7946,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.48757e+11，全样本极值 0–1.89179e+11，n=154818，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/stk_week_month_adj": {
        "close": {"lo": 0.0, "hi": 2861.1,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.14／q999=607.813，全样本极值 0.14–2601，n=2163890，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 286907810.728,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.7／q999=4.19586e+07，全样本极值 0–2.60825e+08，n=2163890，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/stk_weekly_monthly": {
        "close": {"lo": 0.0, "hi": 2861.1,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.12／q999=614.159，全样本极值 0.14–2601，n=2106777，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 286907810.728,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.31133／q999=4.23135e+07，全样本极值 0–2.60825e+08，n=2106777，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/sz_daily_info": {
        "amount": {"lo": 0.0, "hi": 3373568033435.997,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=1.68678e+12，全样本极值 0–1.94358e+12，n=66817，脚本 tools/db/scale_check.py --stats）"},
        "total_mv": {"lo": 0.0, "hi": 194263632119913.72,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=9.71318e+13，全样本极值 0–9.7247e+13，n=54191，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/top10_floatholders": {
        "hold_amount": {"lo": 0.0, "hi": 207671097186.30002,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=600／q999=2.61327e+10，全样本极值 0–1.88792e+11，n=1841250，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/top10_holders": {
        "hold_amount": {"lo": 0.0, "hi": 207671097186.30002,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=7008.36／q999=2.78246e+10，全样本极值 0–1.88792e+11，n=1946932，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/top_list": {
        "close": {"lo": 0.0, "hi": 1910.7,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.02／q999=458.16，全样本极值 0.017–1737，n=237766，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 508651110774.4,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=582.45／q999=4.23584e+10，全样本极值 0–4.6241e+11，n=237818，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/us_tycr": {
        "m1": {"lo": 0.0, "hi": 11.59156,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=5.79578，全样本极值 0–6.02，n=2712，脚本 tools/db/scale_check.py --stats）"},
        "m2": {"lo": 0.0, "hi": 11.2,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.01／q999=5.6，全样本极值 0–5.61，n=2014，脚本 tools/db/scale_check.py --stats）"},
    },
    "market_data/weekly": {
        "close": {"lo": 0.0, "hi": 2861.1,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.163／q999=608.801，全样本极值 0.018–2601，n=2169397，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 286907810727.56006,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=118562／q999=4.20696e+10，全样本极值 56–2.60825e+11，n=2169397，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/all_pb_lg": {
        "close": {"lo": 0.0, "hi": 11683.35186,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1066.42／q999=5841.68，全样本极值 1060.74–5954.77，n=282，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/buffett_lg": {
        "总市值": {"lo": 0.0, "hi": 2696644.23444,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=31452.3／q999=1.34832e+06，全样本极值 31106.9–1.37303e+06，n=5234，脚本 tools/db/scale_check.py --stats）"},
        "收盘价": {"lo": 0.0, "hi": 11532.8922,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=835.999／q999=5766.45，全样本极值 818.03–5877.2，n=5234，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/commodity_foreign": {
        "最新价": {"lo": 0.0, "hi": 8271.468336,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=48.9132／q999=4135.73，全样本极值 48.847–4136.94，n=48，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/congestion_lg": {
        "close": {"lo": 0.0, "hi": 10217.66754,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1959.67／q999=5108.83，全样本极值 1950.01–5166.35，n=3628，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/etf_scale_sse": {
        "基金份额": {"lo": 0.0, "hi": 166065554238.80206,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=7.13985e+06／q999=8.30328e+10，全样本极值 6.7066e+06–1.08474e+11，n=7384，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/etf_scale_szse": {
        "净值": {"lo": 0.0, "hi": 228.026272,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.334766／q999=114.013，全样本极值 0.2586–120.513，n=7381，脚本 tools/db/scale_check.py --stats）"},
        "基金份额": {"lo": 0.0, "hi": 108844719944.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=315922／q999=5.44224e+10，全样本极值 301850–5.83979e+10，n=7598，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/fund_aum_em": {
        "总规模": {"lo": 0.0, "hi": 45511.92718,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=22756，全样本极值 0–25304.5，n=1418，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/fund_linghuo_position_lg": {
        "close": {"lo": 0.0, "hi": 11588.92816,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3019.26／q999=5794.46，全样本极值 3010.65–5807.72，n=460，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/fund_stock_position_lg": {
        "close": {"lo": 0.0, "hi": 11588.92816,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3019.26／q999=5794.46，全样本极值 3010.65–5807.72，n=460，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/gxl_lg": {
        "股息率": {"lo": 0.0, "hi": 8.44,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=4.22，全样本极值 0–4.55，n=16509，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/hibor": {
        "rate": {"lo": 0.0, "hi": 52.25,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.03／q999=14.2696，全样本极值 0.01289–47.5，n=50976，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/high_low_stat": {
        "close": {"lo": 0.0, "hi": 17871.31474,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=2218.91／q999=8935.66，全样本极值 2205.81–9031.38，n=2120，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/hk_daily": {
        "close": {"lo": 0.0, "hi": 6752.7779,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.01／q999=1376.6，全样本极值 0–6138.89，n=8108228，脚本 tools/db/scale_check.py --stats）"},
        "amount": {"lo": 0.0, "hi": 163550344049.40002,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=9.477e+09，全样本极值 0–1.48682e+11，n=8108228，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/hk_fina_snap": {
        "市净率": {"lo": -284.284434, "hi": 633.512461,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-88.9872／q999=316.756，全样本极值 -189.523–533.114，n=2772，脚本 tools/db/scale_check.py --stats）"},
        "市盈率": {"lo": -1894.154404, "hi": 33994.924747,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-880.763／q999=2862.88，全样本极值 -1262.77–30904.5，n=2777，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/hk_qfq_factor": {
        "qfq_factor": {"lo": 0.0, "hi": 11000000000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0209843／q999=259509，全样本极值 0.00343722–1e+10，n=33799，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_au_cpi": {
        "现值": {"lo": -0.45, "hi": 15.526,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.2112／q999=7.763，全样本极值 -0.3–7.8，n=75，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_au_unemp": {
        "现值": {"lo": 0.0, "hi": 14.876257,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3.44531／q999=7.43813，全样本极值 3.44251–7.44345，n=288，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_australia": {
        "今值": {"lo": 0.0, "hi": 39.2405,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.1／q999=19.6203，全样本极值 0.1–19.93，n=526，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_euro": {
        "今值": {"lo": 0.0, "hi": 9.5,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=4.75，全样本极值 0–4.75，n=278，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_japan": {
        "今值": {"lo": -0.15, "hi": 1.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.1／q999=0.5，全样本极值 -0.1–0.5，n=190，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_swiss": {
        "今值": {"lo": -1.125, "hi": 5.5,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.75／q999=2.75，全样本极值 -0.75–2.75，n=74，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_uk": {
        "今值": {"lo": 0.0, "hi": 34.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.1／q999=17，全样本极值 0.1–17，n=636，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_bank_usa": {
        "今值": {"lo": 0.0, "hi": 22.855,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.25／q999=11.4275，全样本极值 0.25–11.5，n=291，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_ca_cpi": {
        "现值": {"lo": -1.42487, "hi": 16.0208,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.930737／q999=8.0104，全样本极值 -0.949914–8.1，n=225，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_ca_gdp": {
        "现值": {"lo": -17.4, "hi": 11.6864,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-10.6188／q999=5.8432，全样本极值 -11.6–6.2，n=224，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_ca_unemp": {
        "现值": {"lo": 0.0, "hi": 27.0864,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=4.9／q999=13.5432，全样本极值 4.9–13.7，n=225，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_de_cpi": {
        "现值": {"lo": -1.05, "hi": 23.065,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.7／q999=11.5325，全样本极值 -0.7–11.6，n=226，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_de_gdp": {
        "现值": {"lo": -10.195934, "hi": 24.919772,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-6.77753／q999=12.4599，全样本极值 -6.79729–12.6599，n=74，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_euro_cpi": {
        "今值": {"lo": -1.05, "hi": 21.2754,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.6377／q999=10.6377，全样本极值 -0.7–10.7，n=624，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_jp_cpi": {
        "现值": {"lo": -3.75, "hi": 8.4656,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-2.4328／q999=4.2328，全样本极值 -2.5–4.3，n=225，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_jp_unemp": {
        "现值": {"lo": 0.0, "hi": 10.9552,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=2.2／q999=5.4776，全样本极值 2.2–5.5，n=225，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_uk_cpi": {
        "现值": {"lo": -0.15, "hi": 22.0208,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.1／q999=11.0104，全样本极值 -0.1–11.1，n=225，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_uk_gdp": {
        "现值": {"lo": -11.4, "hi": 17.9796,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-7.4917／q999=8.9898，全样本极值 -7.6–9.1，n=20，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_uk_unemp": {
        "现值": {"lo": 0.0, "hi": 16.9554,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3.5223／q999=8.4777，全样本极值 3.5–8.5，n=224，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_adp": {
        "今值": {"lo": -3035.4, "hi": 393.4002,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1518.54／q999=196.7，全样本极值 -2023.6–236.9，n=290，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_core_cpi": {
        "今值": {"lo": -0.6, "hi": 2.8,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-0.2666／q999=1.4，全样本极值 -0.4–1.4，n=668，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_cpi": {
        "今值": {"lo": -2.55, "hi": 3.0664,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.2331／q999=1.5332，全样本极值 -1.7–1.8，n=668，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_cpi_yoy_em": {
        "现值": {"lo": -3.15, "hi": 17.977,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.9662／q999=8.9885，全样本极值 -2.1–9.1，n=224，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_gdp": {
        "今值": {"lo": -49.35, "hi": 66.6752,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-32.6504／q999=33.3376，全样本极值 -32.9–33.4，n=209，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_non_farm": {
        "今值": {"lo": -3075.0, "hi": 654.3806,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-729.407／q999=327.19，全样本极值 -2050–480，n=668，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_ppi": {
        "今值": {"lo": -4.2, "hi": 3.6,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-2.674／q999=1.8，全样本极值 -2.8–1.8，n=211，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/macro_usa_unemp": {
        "今值": {"lo": 0.0, "hi": 27.5324,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=3.4／q999=13.7662，全样本极值 3.4–14.7，n=668，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/market_pb_lg": {
        "市净率": {"lo": 0.0, "hi": 19.37352,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.16649／q999=9.68676，全样本极值 1.14–11.7，n=884，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/market_pe_kcb": {
        "总市值": {"lo": 0.0, "hi": 357545.14784,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=5104.54／q999=178773，全样本极值 4821.95–182401，n=1770，脚本 tools/db/scale_check.py --stats）"},
        "市盈率": {"lo": 0.0, "hi": 254.8984,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=26.8283／q999=127.449，全样本极值 25.26–130.69，n=1770，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qdii_spot_asia": {
        "净值": {"lo": 0.0, "hi": 4.803638,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.41544／q999=2.40182，全样本极值 0.4138–2.5029，n=140，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_1000index": {
        "close": {"lo": 0.0, "hi": 84.45056,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=42.2253，全样本极值 0–43.57，n=863，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_100etf": {
        "close": {"lo": 0.0, "hi": 99.06442,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=15.1706／q999=49.5322，全样本极值 15.03–50.16，n=938，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_300etf": {
        "close": {"lo": 0.0, "hi": 116.0214,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=13.0192／q999=44.3729，全样本极值 12.87–105.474，n=1659，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_300index": {
        "close": {"lo": 0.0, "hi": 94.152965,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=47.0765，全样本极值 0–52.1127，n=1478，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_500etf": {
        "close": {"lo": 0.0, "hi": 107.04392,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=13.7793／q999=53.522，全样本极值 13.68–53.8，n=994，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_50etf": {
        "close": {"lo": 0.0, "hi": 124.8855,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=8.4869／q999=62.4428，全样本极值 8.31–103.673，n=2846，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_50index": {
        "close": {"lo": 0.0, "hi": 72.07228,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=36.0361，全样本极值 0–36.89，n=750，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_cyb": {
        "close": {"lo": 0.0, "hi": 135.38754,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=17.0795／q999=67.6938，全样本极值 17.01–76.74，n=994，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/qvix_kcb": {
        "close": {"lo": 0.0, "hi": 180.03688,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=19.5532／q999=90.0184，全样本极值 19.3006–97.4，n=823，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/st_list": {
        "成交额": {"lo": 0.0, "hi": 1130438314.680062,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=6.242／q999=5.65219e+08，全样本极值 5–5.88099e+08，n=208，脚本 tools/db/scale_check.py --stats）"},
        "成交量": {"lo": 0.0, "hi": 2622655.008,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1／q999=1.31133e+06，全样本极值 1–1.40368e+06，n=208，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/stock_gdhs": {
        "总市值": {"lo": 0.0, "hi": 4091306767271.7134,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=4.8056e+08／q999=9.65219e+11，全样本极值 2.38286e+08–3.71937e+12，n=192165，脚本 tools/db/scale_check.py --stats）"},
        "最新价": {"lo": 0.0, "hi": 1583.01,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.06／q999=438.491，全样本极值 0.24–1439.1，n=201208，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/ttm_lyr_lg": {
        "close": {"lo": 0.0, "hi": 11316.32028,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=5658.16，全样本极值 0–5688.54，n=283，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_daily_raw": {
        "close": {"lo": 0.0, "hi": 16139.248183,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.38／q999=8069.62，全样本极值 0.2–9924.4，n=2620144，脚本 tools/db/scale_check.py --stats）"},
        "volume": {"lo": 0.0, "hi": 1134967680.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=2.22771e+08，全样本极值 0–1.03179e+09，n=2620144，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_fina_ind": {
        "NET_PROFIT_RATIO": {"lo": -5899768.882979, "hi": 928164.6,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-706400／q999=17820.1，全样本极值 -3.93318e+06–843786，n=47022，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_qfq_factor": {
        "qfq_factor": {"lo": 0.0, "hi": 16.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.00882024／q999=8，全样本极值 0.00208333–10，n=39819，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_report_balance": {
        "amount": {"lo": -387592500000.0, "hi": 1771200000000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-2.14787e+11／q999=8.856e+11，全样本极值 -2.58395e+11–8.856e+11，n=22998，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_report_cashflow": {
        "amount": {"lo": -953715000000.0, "hi": 1364699839999.9902,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-5.38e+11／q999=6.8235e+11，全样本极值 -6.3581e+11–8.13184e+11，n=19412，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/us_report_income": {
        "amount": {"lo": -388888500000.0, "hi": 9315424800000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-1.4182e+11／q999=1.15359e+12，全样本极值 -2.59259e+11–8.46857e+12，n=19393，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool": {
        "总市值": {"lo": 0.0, "hi": 499209259590.0513,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.23385e+09／q999=2.49605e+11，全样本极值 7.17237e+07–4.51624e+11，n=2738，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 19209065974.272,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=9.98769e+06／q999=9.60453e+09，全样本极值 4.94098e+06–9.98486e+09，n=2738，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool_dtgc": {
        "总市值": {"lo": 0.0, "hi": 429488053052.9541,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=6.90187e+07／q999=2.14744e+11，全样本极值 6.4039e+07–2.7595e+11，n=649，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 25589521870.84793,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=4.79777e+06／q999=1.27948e+10，全样本极值 2.07548e+06–1.37084e+10，n=649，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool_previous": {
        "总市值": {"lo": 0.0, "hi": 527237024424.1951,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.12563e+09／q999=2.63619e+11，全样本极值 0–4.68771e+11，n=2728，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 22158727021.56812,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=9.33245e+06／q999=1.10794e+10，全样本极值 0–1.28483e+10，n=2728，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool_strong": {
        "总市值": {"lo": 0.0, "hi": 6139668002994.598,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=9.02916e+08／q999=3.06983e+12，全样本极值 6.56991e+08–3.1549e+12，n=8336，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 14013020928.00072,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.45295e+07／q999=7.00651e+09，全样本极值 4.94098e+06–1.18493e+10，n=8336，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool_sub_new": {
        "总市值": {"lo": 0.0, "hi": 7713686469735.14,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.0874e+09／q999=3.85684e+12，全样本极值 1.03437e+09–3.97937e+12，n=7393，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 32994574360.57618,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.8138e+06／q999=1.64973e+10，全样本极值 1.03149e+06–2.09968e+10，n=7393，脚本 tools/db/scale_check.py --stats）"},
    },
    "alt_data/zt_pool_zbgc": {
        "总市值": {"lo": 0.0, "hi": 547970797446.1741,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.51429e+09／q999=2.73985e+11，全样本极值 1.00555e+09–3.65959e+11，n=1145，脚本 tools/db/scale_check.py --stats）"},
        "成交额": {"lo": 0.0, "hi": 23712824352.767994,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=7.60993e+07／q999=1.18564e+10，全样本极值 5.62078e+07–1.23929e+10，n=1145，脚本 tools/db/scale_check.py --stats）"},
    },
    "derived_data/fut_basis": {
        "settle": {"lo": 0.0, "hi": 22709.5516,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1844.08／q999=11354.8，全样本极值 1819.6–11461.4，n=9869，脚本 tools/db/scale_check.py --stats）"},
    },
    "derived_data/opt_contract_meta": {
        "strike": {"lo": 0.0, "hi": 940000.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.7／q999=470000，全样本极值 0.45–530000，n=239820，脚本 tools/db/scale_check.py --stats）"},
    },
    "derived_data/opt_iv": {
        "settle": {"lo": 0.0, "hi": 1582.6208,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0001／q999=791.31，全样本极值 0.0001–1189，n=935885，脚本 tools/db/scale_check.py --stats）"},
        "iv": {"lo": 0.0, "hi": 7.853106,
                   "why": "业务常识：iv 上限 10；历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0358575／q999=3.92655，全样本极值 0.0135024–5，n=935885，脚本 tools/db/scale_check.py --stats）"},
    },
    "derived_data/opt_iv_comm": {
        "settle": {"lo": 0.0, "hi": 33572.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.02／q999=16360，全样本极值 0.02–30520，n=8563249，脚本 tools/db/scale_check.py --stats）"},
        "iv": {"lo": 0.0, "hi": 5.5,
                   "why": "业务常识：iv 上限 10；历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0442206／q999=2.70916，全样本极值 0.00199757–5，n=8563249，脚本 tools/db/scale_check.py --stats）"},
    },
    "derived_data/opt_pcr": {
        "call_amount": {"lo": 0.0, "hi": 922578382.0,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=341485，全样本极值 0–8.38708e+08，n=60310，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/series/ibov_weekly": {
        "close": {"lo": 0.0, "hi": 391727.83904,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.01／q999=195864，全样本极值 0.01–206912，n=1919，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/series/spx_weekly": {
        "close": {"lo": 0.0, "hi": 15559.49742,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=442.938／q999=7779.75，全样本极值 442.8–7785.76，n=1814，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/series/tasi_weekly": {
        "close": {"lo": 0.0, "hi": 27467.74,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=6297／q999=13733.9，全样本极值 6267.56–13733.9，n=497，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/series/wind_index_daily": {
        "close": {"lo": -15777.6495, "hi": 47294876.69942,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=-6721.13／q999=2.36474e+07，全样本极值 -10518.4–2.46659e+07，n=12664749，脚本 tools/db/scale_check.py --stats）"},
        "volume": {"lo": 0.0, "hi": 12611170903995.941,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0／q999=6.30559e+12，全样本极值 0–8.52093e+12，n=11187680，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/snap/macro_wind/brazil_index": {
        "成交量": {"lo": 0.0, "hi": 49762356737.999985,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.66796e+08／q999=2.48812e+10，全样本极值 9.79423e+07–4.12324e+10，n=2852，脚本 tools/db/scale_check.py --stats）"},
        "收盘": {"lo": 0.0, "hi": 413781.03502,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=37992.6／q999=206891，全样本极值 37497.5–209067，n=2878，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/snap/macro_wind/fx": {
        "汇率": {"lo": 0.0, "hi": 304.721778,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=0.0258307／q999=152.361，全样本极值 0.025819–152.552，n=5447，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/snap/macro_wind/india_index": {
        "成交量": {"lo": 0.0, "hi": 792924415.999974,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=2.66117e+06／q999=3.96462e+08，全样本极值 1.35405e+06–4.13869e+08，n=2073，脚本 tools/db/scale_check.py --stats）"},
        "收盘": {"lo": 0.0, "hi": 171426.6624,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=23146.5／q999=85713.3，全样本极值 22976–85836.1，n=2881，脚本 tools/db/scale_check.py --stats）"},
    },
    "wind_data/norm/snap/macro_wind/vietnam": {
        "成交量": {"lo": 0.0, "hi": 4481967519.999998,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=1.87421e+07／q999=2.24098e+09，全样本极值 1.67695e+07–2.69703e+09，n=5712，脚本 tools/db/scale_check.py --stats）"},
        "收盘": {"lo": 0.0, "hi": 4165.6454,
                   "why": "历史 0.1–99.9 分位（≤2025 分片包络 q001=528.659／q999=2082.82，全样本极值 521.88–2096.76，n=5713，脚本 tools/db/scale_check.py --stats）"},
    },
}

SCALE_FLOORS = SCALE_ANCHORS  # 兼容别名
