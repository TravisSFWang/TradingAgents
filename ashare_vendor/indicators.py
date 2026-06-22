# -*- coding: utf-8 -*-
"""官方 get_indicators 工具的 ashare 实现：用 stockstats 在 A股行情上计算技术指标"""

from datetime import datetime, timedelta

import pandas as pd

from .market_data import get_daily_df
from .symbols import reject_if_not_a_share

_WARMUP_DAYS = 400  # 指标预热窗口（200日均线等需要足够历史）

_DESC = {
    "close_50_sma": "50日简单均线：中期趋势，动态支撑/阻力",
    "close_200_sma": "200日简单均线：长期趋势基准，金叉/死叉确认",
    "close_10_ema": "10日指数均线：短线动量入场参考",
    "macd": "MACD(DIF)：EMA差值动量，关注交叉与背离",
    "macds": "MACD信号线(DEA)：与DIF交叉作为买卖触发",
    "macdh": "MACD柱：动量强弱与背离的可视化",
    "rsi": "RSI：超买(>70)/超卖(<30)，注意强趋势中可长期钝化",
    "boll": "布林带中轨：20日SMA，价格相对基准",
    "boll_ub": "布林带上轨：超买/突破区域参考",
    "boll_lb": "布林带下轨：超卖/支撑区域参考",
    "atr": "ATR：波动率度量，用于止损与仓位管理",
    "vwma": "VWMA：成交量加权均线，量价结合确认趋势",
    "mfi": "MFI：资金流量指标，量价版RSI",
}


def get_indicators(symbol, indicator, curr_date, look_back_days=30):
    reject_if_not_a_share(symbol)
    curr = datetime.strptime(str(curr_date), "%Y-%m-%d")
    start_window = curr - timedelta(days=int(look_back_days))
    fetch_start = (start_window - timedelta(days=_WARMUP_DAYS)).strftime("%Y-%m-%d")

    df, source = get_daily_df(symbol, fetch_start, curr_date)
    if df.empty:
        from tradingagents.dataflows.symbol_utils import NoMarketDataError
        raise NoMarketDataError(symbol, str(symbol), f"no A-share rows up to {curr_date}")

    from stockstats import wrap
    sdf = df.rename(columns={"vol": "volume"})[
        ["date", "open", "high", "low", "close", "volume"]].copy()
    sdf = wrap(sdf)
    try:
        series = sdf[indicator]
    except Exception as e:
        supported = ", ".join(_DESC)
        return (f"指标 {indicator} 不受支持或计算失败: {e}\n"
                f"常用指标: {supported}（stockstats 语法均可）")

    # stockstats.wrap 会把 date 列设为索引
    out = pd.DataFrame({"date": pd.to_datetime(series.index), "value": series.values})
    out = out[out["date"] >= pd.Timestamp(start_window)].dropna()

    lines = [f"{d.strftime('%Y-%m-%d')}: {v:.4f}" for d, v in
             zip(out["date"], out["value"])]
    desc = _DESC.get(indicator, "")
    return (
        f"## {indicator} values for A-share {symbol} "
        f"from {start_window.strftime('%Y-%m-%d')} to {curr_date} "
        f"(数据来源: {source}):\n\n" + "\n".join(lines) +
        (f"\n\n{indicator}: {desc}" if desc else "")
    )
