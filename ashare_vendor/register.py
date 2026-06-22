# -*- coding: utf-8 -*-
"""
把 ashare vendor 注册进官方 TradingAgents 的数据路由层。

由 install_ashare_patch.py 注入到 tradingagents/dataflows/interface.py 末尾的
代码块调用，也可在自己的脚本里手动调用:

    from ashare_vendor import register
    register()
"""

import logging
import os

logger = logging.getLogger("ashare_vendor")

_CATEGORIES = ("core_stock_apis", "technical_indicators",
               "fundamental_data", "news_data")

# 国内数据源域名：本机若开了代理（梯子），这些请求必须直连，
# 否则会出现 ProxyError: Unable to connect to proxy（东方财富/新浪/财联社等）
_CN_NO_PROXY = ("eastmoney.com,sina.com.cn,sinajs.cn,gtimg.cn,cls.cn,"
                "tushare.pro,baostock.com,10jqka.com.cn,akfamily.xyz,"
                "ima.qq.com,localhost,127.0.0.1")


def _setup_no_proxy():
    """把国内数据源域名并入 NO_PROXY（不覆盖用户已有配置）"""
    for key in ("NO_PROXY", "no_proxy"):
        cur = os.environ.get(key, "")
        have = {d.strip().lower() for d in cur.split(",") if d.strip()}
        add = [d for d in _CN_NO_PROXY.split(",") if d.lower() not in have]
        if add:
            os.environ[key] = (cur + "," if cur else "") + ",".join(add)


def _env_true(key, default="true"):
    return os.getenv(key, default).strip().lower() in ("true", "1", "yes", "on")


def register():
    """注册 vendor 实现并（默认）将 ashare 设为各数据类别的最高优先级。幂等。"""
    _setup_no_proxy()

    from tradingagents.dataflows import interface as itf

    from . import fundamentals, indicators, market_data, news

    impls = {
        "get_stock_data": market_data.get_stock_data,
        "get_indicators": indicators.get_indicators,
        "get_fundamentals": fundamentals.get_fundamentals,
        "get_balance_sheet": fundamentals.get_balance_sheet,
        "get_cashflow": fundamentals.get_cashflow,
        "get_income_statement": fundamentals.get_income_statement,
        "get_news": news.get_news,
        "get_global_news": news.get_global_news,
        "get_insider_transactions": news.get_insider_transactions,
    }

    for method, fn in impls.items():
        itf.VENDOR_METHODS.setdefault(method, {})["ashare"] = fn
    if "ashare" not in itf.VENDOR_LIST:
        itf.VENDOR_LIST.insert(0, "ashare")

    # 默认把 ashare 放到各类别 vendor 链最前（A股走 ashare，
    # 非A股代码 ashare 会抛 NoMarketDataError，自动落到 yfinance）
    if _env_true("ASHARE_VENDOR_PRIORITY"):
        try:
            import tradingagents.default_config as dc
            from tradingagents.dataflows.config import set_config

            patch = {}
            for cat in _CATEGORIES:
                cur = dc.DEFAULT_CONFIG.get("data_vendors", {}).get(cat, "yfinance")
                if "ashare" not in cur:
                    patch[cat] = f"ashare,{cur}"
                    dc.DEFAULT_CONFIG["data_vendors"][cat] = patch[cat]
            if patch:
                set_config({"data_vendors": patch})
        except Exception as e:
            logger.warning(f"[ashare] 设置 vendor 优先级失败（可在配置中手动指定）: {e}")

    # 启动时明确提示 Tushare 状态，便于确认配置生效
    try:
        from . import ts_relay
        if ts_relay.preferred():
            logger.info(f"✅ Tushare 中转已启用（{ts_relay.relay_url()}），"
                        "行情/估值/财报/快讯/增减持/特色数据全部优先走 Tushare")
        elif ts_relay.configured():
            logger.info("✅ Tushare 官方接口已配置（作为备用数据源）")
        else:
            logger.info("ℹ️ 未配置 TUSHARE_TOKEN，Tushare 数据源未启用")
    except Exception:
        pass

    logger.info("✅ ashare vendor 已注册（A股数据: 通达信本地/pytdx/AKShare，非A股自动转交 yfinance）")
    return True
