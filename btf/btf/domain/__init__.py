# -*- coding: utf-8 -*-
"""D 领域层（最稳定 S1）。

契约（03 §7.3 铁律 3）：零第三方依赖——import 白名单仅
typing / dataclasses / enum / datetime / __future__（import-linter 强制）。

内容（04 号文档）：
    types       TradingDate / Instrument / AssetClass / BoardRule
    market      Bar / TradingState
    action      CorporateAction（pay_date 两时点，终审 E5 回退）
    orders      Order / Fill / Fee / FeeSchedule / Trade / OrderType
    portfolio   Position / Portfolio / PortfolioSnapshot / Account
    signal      TargetPortfolio / Signal
    events      七类引擎事件（04 §8.4）
    run         RunManifest / DataVersion / BacktestRun

记账口径（04 §8.2.6，全文唯一）：名义价记账 + 除权日事件再除权。
"""
