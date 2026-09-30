# -*- coding: utf-8 -*-
"""
alt/urls.py — 上游域名判定【唯一真源】
======================================
职责：把 akshare 接口名映射到**真实上游域名**，供限流档位使用。

⚠ 为什么必须独立成模块（两条实测教训）：

  1. **双实现漂移**（R5，2026-09-19 代码审查发现）：
     backfill.guess_url 与 verify_spec.guess_url 是两份重复实现，注释自称
     "保持一致"，实际规则不同：同一接口在两个模块被判入不同档位。
     收敛到本模块后，两处共同引用。

  2. **按函数名猜域名不可靠**（2026-09-19 实测）：
     逐表比对 akshare 源码里的真实 URL 后发现 19/63 张表推断错误——
       · QVIX 全系 18 张：真实走 1.optbbs.com（直读 CSV），
         原判为东财 push2his —— 错得最离谱，且使"push2his 不稳"的记录
         被错误归因到波指头上；
       · stock_market_activity_legu：真实走 legulegu.com，
         原判为东财 datacenter —— **冒险方向**（实际按 15/分跑，
         而乐咕实测 20/分即 429、安全值 10/分）；
       · 金十系宏观 14 张：真实走 jin10.com，原判为东财 datacenter
         （速率恰好相同，无实效影响，但归类错误会让限流规划失真）。
     → 故本模块对"函数名与域名不一致"的接口设**显式登记表**，
       不再依赖命名猜测；未登记的才走前缀规则兜底。

维护纪律：新增接口后应跑 `python -m alt.verify_spec`，
          并用 `python -m alt.urls --check` 核对真实域名与登记是否一致。
"""

# ============================================================
# 一、通用兜底域名（未登记接口的默认归属）
# ============================================================
URL_DATACENTER_WEB = "https://datacenter-web.eastmoney.com"   # 东财数据中台
URL_DATACENTER_API = "https://datacenter-api.jin10.com"       # 金十数据 API
URL_PUSH2EX = "https://push2ex.eastmoney.com"                 # 东财涨停池/异动
URL_PUSH2HIS = "https://push2his.eastmoney.com"               # 东财行情端点
URL_LEGULEGU = "https://legulegu.com"                         # 乐咕乐股
URL_OPTBBS = "https://1.optbbs.com"                           # QVIX 波指 CSV 源

# ============================================================
# 二、显式登记：函数名 → 真实域名（实测得出，优先于规则）
# ============================================================
# —— 金十系海外宏观（真实走 cdn.jin10.com / datacenter-api.jin10.com）——
#    注意：macro_usa_cpi_yoy 虽同属 macro_usa_ 前缀，但走的是**东财**，
#    故不能用前缀一刀切，必须逐函数登记。
_JIN10_FUNCS = {
    "macro_usa_cpi_monthly", "macro_usa_core_cpi_monthly",
    "macro_usa_gdp_monthly", "macro_usa_unemployment_rate",
    "macro_usa_non_farm", "macro_usa_ppi", "macro_usa_adp_employment",
    "macro_bank_usa_interest_rate", "macro_bank_euro_interest_rate",
    "macro_bank_english_interest_rate", "macro_bank_japan_interest_rate",
    "macro_bank_australia_interest_rate",
    "macro_bank_switzerland_interest_rate", "macro_euro_cpi_yoy",
}

# —— 单一接口但命名不具有识别特征，需点名登记 ——
_EXPLICIT = {
    # 乐咕"赚钱效应"：函数名以 _legu 结尾（不是 _lg），且不以 stock_a_ 开头，
    # 原按兜底落入东财档（15/分），实际应为乐咕档（10/分）→ 冒险方向，已收紧
    "stock_market_activity_legu": URL_LEGULEGU,
}


def guess_url(func_name: str) -> str:
    """从 akshare 函数名推断**上游真实域名**（用于限流档位判定）。

    判定顺序：显式登记表 → 命名规则 → 兜底。
    规则保守：宁慢勿快（判到更严的档位比判到更松的安全）。
    """
    n = str(func_name).lower()

    # 1) 显式登记优先（实测域名与命名不符的接口）
    if func_name in _JIN10_FUNCS:
        return URL_DATACENTER_API
    if func_name in _EXPLICIT:
        return _EXPLICIT[func_name]

    # 2) QVIX 波指全系：真实走 optbbs 直读 CSV（非东财！）
    if n.startswith("index_option_") and "qvix" in n:
        return URL_OPTBBS
    #    其他 index_option_ 接口仍按东财行情端点处理
    if n.startswith("index_option_"):
        return URL_PUSH2HIS

    # 3) 乐咕乐股系
    if n.startswith("stock_a_") or n.endswith("_lg"):
        return URL_LEGULEGU

    # 4) 涨停池 / 板块异动（东财 push2ex，实测稳定）
    if "zt_pool" in n or "board_change" in n:
        return URL_PUSH2EX

    # 5) 兜底：东财数据中台
    return URL_DATACENTER_WEB


def describe(func_name: str) -> dict:
    """返回某接口的域名判定明细（供 --check 打印）"""
    from config_alt import host_tier, rate_for_url

    u = guess_url(func_name)
    return {"func": func_name, "url": u,
            "tier": host_tier(u), "rate": rate_for_url(u)}


def registry_report():
    """列出所有经"显式登记/特殊规则"判定（非兜底）的接口。

    用途：人工核对登记是否仍然正确——兜底判定最不可靠，应定期复核。
    """
    rows = []
    for f in sorted(_JIN10_FUNCS):
        rows.append({"func": f, "url": URL_DATACENTER_API, "why": "金十系（显式登记）"})
    for f, u in sorted(_EXPLICIT.items()):
        rows.append({"func": f, "url": u, "why": "命名无识别特征（显式登记）"})
    return rows


def _main(argv=None):
    """CLI：核对真实域名与登记是否一致（零请求，需 akshare 已安装）"""
    import argparse
    import inspect
    import re
    import sys

    p = argparse.ArgumentParser(description="alt 上游域名判定核对（零请求）")
    p.add_argument("--check", action="store_true",
                   help="逐表比对 akshare 源码中的真实 URL 与登记值")
    a = p.parse_args(argv)

    try:
        from alt.spec import ALL_SPECS
    except Exception as e:  # noqa: BLE001
        print(f"无法导入 spec: {e}")
        return 1

    print("=" * 104)
    print("alt 上游域名登记表")
    print("=" * 104)
    for r in registry_report():
        d = describe(r["func"])
        print(f"  {r['func']:<38} → {d['tier']:<12}({d['rate']:>2}/分)  {r['why']}")

    if not a.check:
        print("\n  （加 --check 可逐表比对 akshare 源码里的真实 URL）")
        return 0

    try:
        import akshare as ak
    except Exception as e:  # noqa: BLE001
        print(f"akshare 不可用，跳过核对: {e}")
        return 0

    url_pat = re.compile(r"""url\s*=\s*[fr]?["'](https?://[^"']+)["']""")
    print("\n" + "=" * 104)
    print("逐表核对：登记域名 vs akshare 源码真实域名")
    print("=" * 104)
    bad = 0
    for sp in ALL_SPECS:
        fn = getattr(ak, sp.func, None)
        if fn is None:
            continue
        try:
            src = inspect.getsource(fn)
            whole = open(inspect.getsourcefile(fn), encoding="utf-8").read()
        except Exception:  # noqa: BLE001
            continue
        urls = set(url_pat.findall(src)) or set(url_pat.findall(whole))
        hosts = sorted({re.sub(r"^https?://", "", u).split("/")[0] for u in urls})
        if not hosts:
            continue
        reg_host = re.sub(r"^https?://", "", guess_url(sp.func)).split("/")[0]
        hit = any(reg_host == h or h.endswith(reg_host) or reg_host.endswith(h)
                  for h in hosts)
        mark = "OK " if hit else "!! "
        if not hit:
            bad += 1
        print(f"  {mark}{sp.name:<24}{sp.func:<38}登记={reg_host:<32}真实={hosts}")
    print(f"\n  不一致 {bad} 张（'!!' 行需更新登记表）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
