# -*- coding: utf-8 -*-
"""ashare_vendor/chart_data.py 离线测试（CLAUDE.md §10 第10/11/12项）。

覆盖三个"错了不显眼"校验点:
  ① 5min→30/60min 分桶不跨午休拼接出错误的 bar（OHLCV 聚合正确）
  ② daily_rangebreaks 只标记真实缺失的非交易日，不误标已有交易日
  ③ 指标计算（MA/BOLL/VWMA/MACD/RSI）在已知输入下产出合理值，不崩溃
"""

import pandas as pd

from ashare_vendor import chart_data


def _bar(dt_str, o, h, l, c, vol=1000.0):
    dt = pd.Timestamp(dt_str)
    return {"datetime": dt, "date": dt.normalize(),
            "open": o, "high": h, "low": l, "close": c,
            "vol": vol, "amount": vol * c}


def _make_session_day(date_str, base=10.0):
    """构造一个完整交易日的 5min bar：早盘24根(9:35-11:30) + 午盘24根(13:05-15:00)。"""
    bars = []
    minute = pd.Timestamp(f"{date_str} 09:35")
    for i in range(24):
        t = minute + pd.Timedelta(minutes=5 * i)
        bars.append(_bar(t, base + i * 0.01, base + i * 0.01 + 0.05,
                         base + i * 0.01 - 0.05, base + i * 0.01, vol=100 + i))
    minute = pd.Timestamp(f"{date_str} 13:05")
    for i in range(24):
        t = minute + pd.Timedelta(minutes=5 * i)
        bars.append(_bar(t, base + i * 0.01, base + i * 0.01 + 0.05,
                         base + i * 0.01 - 0.05, base + i * 0.01, vol=100 + i))
    return bars


# ── 1. 分钟聚合：跨午休不拼错 ───────────────────────────────────────────────────

def test_resample_60min_splits_morning_and_afternoon():
    bars = _make_session_day("2026-06-10")
    df5 = pd.DataFrame(bars)
    out = chart_data._resample_from_5min(df5, "60min")

    # 24 根/时段 ÷ 12 = 每时段 2 根 60min bar，全天 4 根
    assert len(out) == 4
    # 第2根(早盘第二个60min) 收盘于 11:30，第3根(午盘第一个60min)收盘于 13:30 —
    # 二者之间应有缺口(午休)，绝不能把 11:35 后的不存在数据和午盘数据混进同一桶
    times = out["datetime"].tolist()
    assert times[1].time() == pd.Timestamp("11:30:00").time()
    assert times[2].time() == pd.Timestamp("14:00:00").time()


def test_resample_30min_open_high_low_close_aggregation():
    bars = _make_session_day("2026-06-10")
    df5 = pd.DataFrame(bars)
    out = chart_data._resample_from_5min(df5, "30min")

    # 24 ÷ 6 = 4 根/时段，全天 8 根
    assert len(out) == 8
    first_bucket_5min = pd.DataFrame(bars[:6])
    first_30min = out.iloc[0]
    assert first_30min["open"] == first_bucket_5min.iloc[0]["open"]
    assert first_30min["close"] == first_bucket_5min.iloc[-1]["close"]
    assert first_30min["high"] == first_bucket_5min["high"].max()
    assert first_30min["low"] == first_bucket_5min["low"].min()
    assert first_30min["vol"] == first_bucket_5min["vol"].sum()


def test_resample_across_multiple_days_does_not_merge_buckets():
    bars = _make_session_day("2026-06-10") + _make_session_day("2026-06-11")
    df5 = pd.DataFrame(bars)
    out = chart_data._resample_from_5min(df5, "60min")
    # 两个交易日各 4 根 60min bar，不应互相拼接
    assert len(out) == 8
    assert out["datetime"].dt.normalize().nunique() == 2


# ── 2. rangebreaks：只标真实缺口 ────────────────────────────────────────────────

def test_daily_rangebreaks_flags_only_missing_weekday():
    # 2026-06-10(三) 跳过 2026-06-11(四)，直接到 2026-06-12(五)
    dates = pd.Series(pd.to_datetime(["2026-06-10", "2026-06-12"]))
    breaks = chart_data.daily_rangebreaks(dates)
    assert len(breaks) == 1
    missing = breaks[0]["values"]
    assert pd.Timestamp("2026-06-11") in missing
    assert pd.Timestamp("2026-06-10") not in missing
    assert pd.Timestamp("2026-06-12") not in missing


def test_daily_rangebreaks_empty_when_no_gap():
    dates = pd.Series(pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"]))
    assert chart_data.daily_rangebreaks(dates) == []


def test_daily_rangebreaks_empty_series():
    assert chart_data.daily_rangebreaks(pd.Series([], dtype="datetime64[ns]")) == []


# ── 3. 指标计算：合理性 ──────────────────────────────────────────────────────────

def _trend_df(n=60, start=10.0, step=0.1, vol=1000.0):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes, "high": [c + 0.05 for c in closes],
        "low": [c - 0.05 for c in closes], "close": closes,
        "vol": [vol] * n,
    })


def test_ma_indicators_track_uptrend():
    df = _trend_df(n=400)
    ind = chart_data.compute_indicators(df, {"ma"})
    expected_keys = {f"sma{n}" for n in (5, 10, 20, 30, 60, 120, 240, 360)}
    assert expected_keys <= ind.keys()
    # 持续上涨中，短周期均线应高于长周期均线（最后一行）
    assert ind["sma5"].iloc[-1] > ind["sma60"].dropna().iloc[-1]
    assert ind["sma60"].dropna().iloc[-1] > ind["sma360"].dropna().iloc[-1]


def test_boll_bands_ordering():
    df = _trend_df()
    ind = chart_data.compute_indicators(df, {"boll"})
    last = -1
    assert ind["boll_lb"].iloc[last] <= ind["boll_mid"].iloc[last] <= ind["boll_ub"].iloc[last]


def test_vwma_within_price_range():
    df = _trend_df()
    ind = chart_data.compute_indicators(df, {"vwma"})
    vwma = ind["vwma20"].dropna()
    assert (vwma >= df["low"].min()).all() and (vwma <= df["high"].max()).all()


def test_macd_uptrend_positive():
    df = _trend_df(n=80)
    ind = chart_data.compute_indicators(df, {"macd"})
    # 持续上涨，MACD 线应为正
    assert ind["macd"].iloc[-1] > 0


def test_rsi_uptrend_above_50():
    df = _trend_df()
    ind = chart_data.compute_indicators(df, {"rsi"})
    rsi = ind["rsi14"].dropna()
    assert (rsi > 50).all()


def test_no_selected_indicators_returns_empty():
    df = _trend_df()
    assert chart_data.compute_indicators(df, set()) == {}
