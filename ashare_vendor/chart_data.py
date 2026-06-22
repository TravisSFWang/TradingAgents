# -*- coding: utf-8 -*-
"""
Web 工作台 K 线图数据与技术指标（CLAUDE.md §10 第10/11/12项）

对外提供:
- get_period_df(symbol, period, end) -> (df, source_label)   多周期K线(日/周/月/60分/30分/15分)
- compute_indicators(df, selected) -> dict[str, pd.Series]    叠加指标(MA/BOLL/VWMA/MACD/RSI)
- daily_rangebreaks(dates) / minute_rangebreaks()             剔除休市空档(plotly rangebreaks)
"""

from datetime import date, timedelta

import pandas as pd

from .symbols import is_a_share

PERIOD_LABELS = {
    "daily": "日线 Daily",
    "weekly": "周线 Weekly",
    "monthly": "月线 Monthly",
    "60min": "60分钟 60min",
    "30min": "30分钟 30min",
    "15min": "15分钟 15min",
}

# 从 5 分钟 bar 聚合到更大分钟周期所需的 bar 数。A股早晚两个连续交易时段
# (9:30-11:30, 13:00-15:00) 各含 24 根 5min bar，按"每日内顺序位置"分桶天然
# 整除两个时段，不会跨午休拼接出错误的 bar。
_MINUTE_BUCKETS = {"60min": 12, "30min": 6, "15min": 3}

# 取日线窗口天数（周/月线需要更长历史才能看出趋势；日线拉长到约2年，
# 让 SMA360 等长周期均线在可视区间内有数据可画）
_DAILY_WINDOW_DAYS = {"daily": 720, "weekly": 1100, "monthly": 2200}

# 多周期均线（A股软件常用口径）
_MA_PERIODS = (5, 10, 20, 30, 60, 120, 240, 360)


def _resample_from_5min(df5: pd.DataFrame, period: str) -> pd.DataFrame:
    """从 5 分钟线聚合出 15/30/60 分钟线。"""
    if df5.empty:
        return df5
    n = _MINUTE_BUCKETS[period]
    df = df5.sort_values("datetime").reset_index(drop=True)
    pos = df.groupby("date").cumcount()
    bucket_idx = pos // n
    grouped = df.groupby([df["date"], bucket_idx], sort=False)
    out = grouped.agg(datetime=("datetime", "last"), open=("open", "first"),
                      high=("high", "max"), low=("low", "min"), close=("close", "last"),
                      vol=("vol", "sum"), amount=("amount", "sum"))
    return out.reset_index(drop=True).sort_values("datetime").reset_index(drop=True)


def get_period_df(symbol: str, period: str, end: str):
    """按周期取K线。period: daily/weekly/monthly/60min/30min/15min。

    日/周/月线复用现有降级链(get_daily_df) + TdxLocalReader.resample；
    分钟周期仅 A股，走通达信本地/在线 5分钟线(intraday.get_minute_df)再聚合，不复权。
    """
    from .market_data import get_daily_df

    if period in _DAILY_WINDOW_DAYS:
        days = _DAILY_WINDOW_DAYS[period]
        start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
        df, source = get_daily_df(symbol, start, end)
        if df.empty or period == "daily":
            return df, source
        from tdx_local.reader import TdxLocalReader
        return TdxLocalReader.resample(df, period), source

    if period not in _MINUTE_BUCKETS:
        return pd.DataFrame(), None
    if not is_a_share(symbol):
        return pd.DataFrame(), None

    from .intraday import get_minute_df
    df5, source = get_minute_df(symbol, freq="5min", lookback_days=30)
    if df5.empty:
        return df5, source
    df5 = df5[df5["date"] <= pd.Timestamp(end)]
    if df5.empty:
        return df5, source
    return _resample_from_5min(df5, period), source


def compute_indicators(df: pd.DataFrame, selected) -> dict:
    """对 close/vol 计算所选技术指标。selected 为 {'ma','boll','vwma','macd','rsi'} 子集。"""
    out = {}
    close = df["close"]

    if "ma" in selected:
        for n in _MA_PERIODS:
            out[f"sma{n}"] = close.rolling(n).mean()

    if "boll" in selected:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        out["boll_mid"] = mid
        out["boll_ub"] = mid + 2 * std
        out["boll_lb"] = mid - 2 * std

    if "vwma" in selected and "vol" in df.columns:
        vol = df["vol"]
        out["vwma20"] = (close * vol).rolling(20).sum() / vol.rolling(20).sum()

    if "macd" in selected:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        out["macd"] = macd_line
        out["macd_signal"] = signal
        out["macd_hist"] = macd_line - signal

    if "rsi" in selected:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        out["rsi14"] = 100 - (100 / (1 + rs))

    return out


def daily_rangebreaks(dates: pd.Series) -> list:
    """剔除日/周/月线 x 轴上的非交易日空档(周末+假期)。"""
    dates = pd.to_datetime(dates).dropna()
    if dates.empty:
        return []
    all_days = pd.date_range(dates.min(), dates.max(), freq="D")
    have = set(dates.dt.normalize())
    missing = [d for d in all_days if d not in have]
    return [dict(values=missing)] if missing else []


def minute_rangebreaks() -> list:
    """剔除分钟图非交易时段：周末 + 隔夜 + 午休(11:30-13:00)。"""
    return [
        dict(bounds=["sat", "mon"]),
        dict(bounds=[15, 9.5], pattern="hour"),
        dict(bounds=[11.5, 13], pattern="hour"),
    ]
