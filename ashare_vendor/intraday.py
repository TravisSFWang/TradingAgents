# -*- coding: utf-8 -*-
"""
分钟线日内微观结构分析（§10 第8项，CLAUDE.md §13）

对外提供:
- get_intraday_structure(symbol, curr_date) -> str   (注入 market_analyst 工具)
- compute_intraday_structure(df, code, company_name, lookback_days) -> dict  (可测)
- 辅助子函数（_compute_*）均为模块级，支持单测

价格口径：**不复权原始价**（涨跌停检测必须；TDX分钟线天然不复权）。
"""

import logging
import os
import statistics
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .symbols import parse

logger = logging.getLogger("ashare_vendor")

# ── 环境开关 ──────────────────────────────────────────────────────────────────

def _env_true(key: str, default: str = "true") -> bool:
    return os.getenv(key, default).strip().lower() in ("true", "1", "yes", "on")


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

# ── 涨跌停比例 ────────────────────────────────────────────────────────────────

def _limit_pct(code: str, company_name: str = "") -> float:
    """按板块+ST标志返回日涨跌幅上限（小数形式，如 0.10）。"""
    name_upper = company_name.upper()
    # ST 名称检测（优先级最高）
    if "ST" in name_upper:
        return 0.05
    if code.startswith(("688", "689")):
        return 0.20  # 科创板
    if code.startswith(("300", "301")):
        return 0.20  # 创业板
    # 北交所前缀
    if code.startswith(("43", "83", "87", "88", "92")) or (len(code) == 6 and code[0] in "48"):
        return 0.30
    return 0.10  # 主板

# ── 取数层 ────────────────────────────────────────────────────────────────────

def get_minute_df(symbol: str, freq: str = "5min",
                  lookback_days: int = 10, extra_days: int = 3):
    """获取分钟线 DataFrame + 来源标签。

    降级链：通达信本地 → pytdx 在线。返回 (df, source_str)；无数据返回 (空df, None)。
    extra_days：额外获取天数，用于 prev_close 计算（不计入 feature 窗口）。
    价格：**不复权**（TDX分钟线天然如此）。
    """
    _, code, _ = parse(symbol)
    total_days = lookback_days + extra_days
    # 取约 2×日历天以覆盖节假日
    start_dt = datetime.now() - timedelta(days=total_days * 2)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = datetime.now().strftime("%Y-%m-%d")

    df = pd.DataFrame()
    source = None

    vipdoc = os.getenv("TDX_VIPDOC_PATH", "").strip()
    if vipdoc:
        try:
            from tdx_local.reader import TdxLocalReader
            r = TdxLocalReader(vipdoc)
            kind = "5min" if "5" in freq else "1min"
            if r.available() and r.has_local(symbol, kind):
                df = r.read_minute(symbol, freq=freq,
                                   start_date=start_str, end_date=end_str)
                if not df.empty:
                    source = "TDX-local"
        except Exception as e:
            logger.debug(f"[intraday] TDX local failed for {symbol}: {e}")

    if df.empty and _env_true("TDX_ONLINE_FALLBACK"):
        try:
            from tdx_local.reader import TdxLocalReader
            r = TdxLocalReader()
            max_bars = total_days * (48 if "5" in freq else 240)
            online_df = r.get_bars_online(symbol, start_date=start_str, end_date=end_str,
                                          period=freq, max_bars=max_bars)
            if online_df is not None and not online_df.empty:
                df = online_df
                source = "pytdx"
        except Exception as e:
            logger.debug(f"[intraday] pytdx online failed for {symbol}: {e}")

    if df.empty:
        return df, None

    # 保证有 datetime + date 列
    if "datetime" not in df.columns:
        logger.warning(f"[intraday] minute df missing 'datetime' column for {symbol}")
        return pd.DataFrame(), None
    if "date" not in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["datetime"]).dt.normalize()

    df = df.sort_values("datetime").reset_index(drop=True)

    # 只保留最近 total_days 个交易日
    trading_dates = sorted(df["date"].unique())
    if len(trading_dates) > total_days:
        cutoff = trading_dates[-total_days]
        df = df[df["date"] >= cutoff].reset_index(drop=True)

    return df, source

# ── 特征计算子函数（纯计算，无副作用，可单测） ────────────────────────────────

def _compute_vol_profile(df: pd.DataFrame, n_bins: int = 30) -> dict:
    """Volume Profile: POC / 价值区 VAH/VAL / 最近支撑/压力。"""
    if df.empty or df["vol"].sum() == 0:
        return {}

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if price_max <= price_min:
        return {}

    edges = np.linspace(price_min, price_max, n_bins + 1)
    bin_vol = np.zeros(n_bins)

    for _, row in df.iterrows():
        typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3
        idx = int(np.searchsorted(edges, typical, side="right")) - 1
        idx = max(0, min(n_bins - 1, idx))
        bin_vol[idx] += float(row["vol"])

    poc_idx = int(np.argmax(bin_vol))
    poc_price = float((edges[poc_idx] + edges[poc_idx + 1]) / 2)

    total_vol = float(bin_vol.sum())
    target = total_vol * 0.70

    # 从 POC 向两侧展开，优先高量方向
    lo_idx = hi_idx = poc_idx
    accumulated = float(bin_vol[poc_idx])
    while accumulated < target and (lo_idx > 0 or hi_idx < n_bins - 1):
        lo_add = float(bin_vol[lo_idx - 1]) if lo_idx > 0 else 0.0
        hi_add = float(bin_vol[hi_idx + 1]) if hi_idx < n_bins - 1 else 0.0
        if lo_add > hi_add:
            lo_idx -= 1
            accumulated += float(bin_vol[lo_idx])
        elif hi_add > lo_add:
            hi_idx += 1
            accumulated += float(bin_vol[hi_idx])
        elif hi_idx < n_bins - 1:
            hi_idx += 1
            accumulated += float(bin_vol[hi_idx])
        else:
            lo_idx -= 1
            accumulated += float(bin_vol[lo_idx])

    val = float((edges[lo_idx] + edges[lo_idx + 1]) / 2)
    vah = float((edges[hi_idx] + edges[hi_idx + 1]) / 2)

    current_price = float(df.iloc[-1]["close"])
    threshold = total_vol * 0.03  # 高量节点：≥ 3% 总量

    support_price = support_vol_pct = None
    resist_price = resist_vol_pct = None

    for i in range(n_bins):
        bin_mid = float((edges[i] + edges[i + 1]) / 2)
        if bin_vol[i] < threshold:
            continue
        if bin_mid < current_price:
            if support_price is None or bin_mid > support_price:
                support_price = bin_mid
                support_vol_pct = float(bin_vol[i] / total_vol * 100)
        elif bin_mid > current_price:
            if resist_price is None or bin_mid < resist_price:
                resist_price = bin_mid
                resist_vol_pct = float(bin_vol[i] / total_vol * 100)

    return {
        "poc": round(poc_price, 3),
        "vah": round(vah, 3),
        "val": round(val, 3),
        "support": round(support_price, 3) if support_price is not None else None,
        "support_vol_pct": round(support_vol_pct, 1) if support_vol_pct is not None else None,
        "resistance": round(resist_price, 3) if resist_price is not None else None,
        "resist_vol_pct": round(resist_vol_pct, 1) if resist_vol_pct is not None else None,
        "current_price": current_price,
    }


def _compute_vwap(df: pd.DataFrame, feature_dates: list) -> dict:
    """窗口 VWAP + 每日收盘站上率。"""
    if df.empty or df["vol"].sum() == 0:
        return {}

    typical = (df["high"] + df["low"] + df["close"]) / 3
    window_vwap = float((typical * df["vol"]).sum() / df["vol"].sum())
    current_price = float(df.iloc[-1]["close"])
    deviation_pct = (current_price - window_vwap) / window_vwap * 100 if window_vwap else 0

    days_above = n_valid = 0
    for d in feature_dates:
        day_df = df[df["date"] == d]
        if day_df.empty or day_df["vol"].sum() == 0:
            continue
        day_typ = (day_df["high"] + day_df["low"] + day_df["close"]) / 3
        daily_vwap = float((day_typ * day_df["vol"]).sum() / day_df["vol"].sum())
        day_close = float(day_df.iloc[-1]["close"])
        n_valid += 1
        if day_close > daily_vwap:
            days_above += 1

    return {
        "vwap": round(window_vwap, 3),
        "current_price": current_price,
        "deviation_pct": round(deviation_pct, 2),
        "days_above": days_above,
        "n_days": n_valid,
    }


def _compute_intraday_character(df: pd.DataFrame, char_dates: list) -> dict:
    """日内性格（最近5日）：close-in-range + 尾盘净方向 → 吸筹/派发/中性。"""
    cirs = []
    final30_buys = 0
    n_valid = 0

    for d in char_dates:
        day_df = df[df["date"] == d].sort_values("datetime")
        if day_df.empty:
            continue

        day_high = float(day_df["high"].max())
        day_low = float(day_df["low"].min())
        day_close = float(day_df.iloc[-1]["close"])

        cir = ((day_close - day_low) / (day_high - day_low)
               if day_high > day_low else 0.5)
        cirs.append(cir)

        # 尾盘 30 分钟：A股下午 14:30-15:00（按 bar 起始时间）
        is_final = ((day_df["datetime"].dt.hour == 14) &
                    (day_df["datetime"].dt.minute >= 30))
        final_df = day_df[is_final]
        if len(final_df) >= 1:
            final_close = float(final_df.iloc[-1]["close"])
            final_open = float(final_df.iloc[0]["open"])
            if final_close > final_open:
                final30_buys += 1

        n_valid += 1

    if not cirs or n_valid == 0:
        return {}

    median_cir = statistics.median(cirs)

    if median_cir >= 0.65 and final30_buys >= max(1, round(n_valid * 0.5)):
        verdict = "accumulation"
    elif median_cir <= 0.35 and (n_valid - final30_buys) >= max(1, round(n_valid * 0.5)):
        verdict = "distribution"
    else:
        verdict = "neutral"

    return {
        "median_cir": round(median_cir, 2),
        "final30_buy_days": final30_buys,
        "n_days": n_valid,
        "verdict": verdict,
    }


def _compute_limit_board(df: pd.DataFrame, feature_dates: list,
                         prev_close_map: dict, pct: float) -> dict:
    """A股涨跌停盘口检测：触板天数、封板时间、开板次数、收盘状态。"""
    if pct <= 0 or not prev_close_map:
        return {}

    touched_days = 0
    last_touch = None

    for d in feature_dates:
        prev_close = prev_close_map.get(d)
        if prev_close is None or prev_close <= 0:
            continue
        limit_up = round(prev_close * (1 + pct), 2)

        day_df = df[df["date"] == d].sort_values("datetime")
        if day_df.empty:
            continue

        # 触板：日内最高价 ≥ 涨停价 - 1 tick
        if not (day_df["high"] >= limit_up - 0.01).any():
            continue

        touched_days += 1

        # 首次封板时间（close ≥ 涨停价 - 1 tick）
        sealed_mask = day_df["close"] >= limit_up - 0.01
        if sealed_mask.any():
            first_sealed = day_df[sealed_mask].iloc[0]
            seal_dt = pd.Timestamp(first_sealed["datetime"])
            seal_time_str = seal_dt.strftime("%H:%M")
        else:
            seal_time_str = None

        # 开板次数（封板 → 脱板 跳变数）
        open_count = 0
        was_sealed = False
        for _, row in day_df.iterrows():
            is_sealed = bool(float(row["close"]) >= limit_up - 0.01)
            if was_sealed and not is_sealed:
                open_count += 1
            was_sealed = is_sealed

        # 最终状态
        final_close = float(day_df.iloc[-1]["close"])
        if final_close >= limit_up - 0.01:
            final_status = "sealed"
        elif open_count > 0:
            final_status = "failed"
        else:
            final_status = "touched"

        d_ts = pd.Timestamp(d)
        last_touch = {
            "date": d_ts.strftime("%Y-%m-%d"),
            "seal_time": seal_time_str,
            "open_count": open_count,
            "final_status": final_status,
        }

    if touched_days == 0:
        return {}

    return {
        "pct": int(round(pct * 100)),
        "touched_days": touched_days,
        "n_days": len(feature_dates),
        "last_touch": last_touch,
    }


def _compute_intraday_volatility(df: pd.DataFrame, feature_dates: list,
                                 prev_close_map: dict) -> dict:
    """日内振幅中位数 + T+1 止损底线参考价。"""
    swings = []
    for d in feature_dates:
        prev_close = prev_close_map.get(d)
        if prev_close is None or prev_close <= 0:
            continue
        day_df = df[df["date"] == d]
        if day_df.empty:
            continue
        day_high = float(day_df["high"].max())
        day_low = float(day_df["low"].min())
        swings.append((day_high - day_low) / prev_close * 100)

    if not swings:
        return {}

    median_swing_pct = statistics.median(swings)
    current_price = float(df.iloc[-1]["close"])
    stop_yuan = round(current_price * median_swing_pct / 100, 2)

    return {
        "median_swing_pct": round(median_swing_pct, 1),
        "stop_yuan": stop_yuan,
    }

# ── 组合计算入口（可测） ───────────────────────────────────────────────────────

def compute_intraday_structure(df: pd.DataFrame, code: str,
                               company_name: str = "",
                               lookback_days: int = 10) -> dict:
    """纯计算：接收 DataFrame，返回各特征 dict。无网络、无 LLM、无副作用。

    df 可包含比 lookback_days 更多的交易日（用于 prev_close 计算）。
    """
    if df.empty:
        return {}

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)

    trading_dates = sorted(df["date"].unique())
    feature_dates = (trading_dates[-lookback_days:] if len(trading_dates) >= lookback_days
                     else trading_dates)

    # prev_close 映射（用全部可用日期算）
    prev_close_map = {}
    for i, d in enumerate(trading_dates):
        if i > 0:
            prev_day = df[df["date"] == trading_dates[i - 1]]
            if not prev_day.empty:
                prev_close_map[d] = float(prev_day.iloc[-1]["close"])

    feature_df = df[df["date"].isin(feature_dates)].copy()
    if feature_df.empty:
        return {}

    result: dict = {}

    try:
        vp = _compute_vol_profile(feature_df)
        if vp:
            result["vol_profile"] = vp
    except Exception as e:
        logger.debug(f"[intraday] vol_profile failed: {e}")

    try:
        vwap = _compute_vwap(feature_df, feature_dates)
        if vwap:
            result["vwap"] = vwap
    except Exception as e:
        logger.debug(f"[intraday] vwap failed: {e}")

    try:
        char_dates = feature_dates[-5:] if len(feature_dates) >= 5 else feature_dates
        char = _compute_intraday_character(feature_df, char_dates)
        if char:
            result["character"] = char
    except Exception as e:
        logger.debug(f"[intraday] character failed: {e}")

    try:
        pct = _limit_pct(code, company_name)
        lb = _compute_limit_board(feature_df, feature_dates, prev_close_map, pct)
        if lb:
            result["limit_board"] = lb
    except Exception as e:
        logger.debug(f"[intraday] limit_board failed: {e}")

    try:
        ivol = _compute_intraday_volatility(feature_df, feature_dates, prev_close_map)
        if ivol:
            result["intraday_vol"] = ivol
    except Exception as e:
        logger.debug(f"[intraday] intraday_vol failed: {e}")

    last_bar = feature_df.iloc[-1]
    result["current_price"] = float(last_bar["close"])
    result["last_bar_dt"] = pd.Timestamp(last_bar["datetime"]).strftime("%Y-%m-%d %H:%M")
    result["n_days"] = len(feature_dates)

    return result

# ── 输出格式化 ────────────────────────────────────────────────────────────────

def _format_output(features: dict, source: str,
                   freq: str, lookback_days: int) -> str:
    if not features or "current_price" not in features:
        return ("Intraday microstructure unavailable (no local TDX minute files "
                "and pytdx unreachable); proceeding on daily data only.")

    p = features["current_price"]
    last_dt = features.get("last_bar_dt", "")
    n_days = features.get("n_days", lookback_days)
    freq_label = "5-min" if "5" in freq else "1-min"

    lines = [
        f"## Intraday Microstructure (last {n_days} trading days, {freq_label} bars; "
        f"source: {source}; raw/unadjusted prices)",
        f"Current price ¥{p:.2f} (last bar {last_dt}).",
    ]

    vp = features.get("vol_profile")
    if vp:
        poc = vp.get("poc", p)
        val_ = vp.get("val", p)
        vah = vp.get("vah", p)
        sup = vp.get("support")
        res = vp.get("resistance")
        vl = (f"- Volume-profile: POC ¥{poc:.2f} (heaviest traded); "
              f"value area ¥{val_:.2f}–¥{vah:.2f}.")
        if sup is not None and p > 0:
            sup_pct = abs(p - sup) / p * 100
            svp = vp.get("support_vol_pct") or 0
            vl += (f" Nearest support ¥{sup:.2f} (HVN, {svp:.0f}% of window vol) "
                   f"~{sup_pct:.1f}% below price;")
        if res is not None and p > 0:
            res_pct = abs(res - p) / p * 100
            vl += f" nearest resistance ¥{res:.2f} ~{res_pct:.1f}% above."
        lines.append(vl)

    vwap_info = features.get("vwap")
    if vwap_info:
        v = vwap_info.get("vwap", p)
        dev = vwap_info.get("deviation_pct", 0)
        above = vwap_info.get("days_above", 0)
        n_v = vwap_info.get("n_days", n_days)
        sign = "+" if dev >= 0 else ""
        if n_v > 0 and above / n_v >= 0.60:
            pos = "holders in profit, dips likely bought"
        elif n_v > 0 and above / n_v < 0.40:
            pos = "price below cost basis, overhead supply"
        else:
            pos = "mixed positioning around VWAP"
        lines.append(
            f"- VWAP: {n_days}-day ¥{v:.2f}; price {sign}{dev:.1f}% vs VWAP; "
            f"closed above daily VWAP on {above}/{n_v} days ({pos})."
        )

    char = features.get("character")
    if char:
        cir = char.get("median_cir", 0.5)
        f30 = char.get("final30_buy_days", 0)
        n_c = char.get("n_days", 5)
        verdict = char.get("verdict", "neutral")
        lines.append(
            f"- Intraday character (last {n_c} sessions): median close-in-range {cir:.2f}; "
            f"final-30-min net buying {f30}/{n_c} days → {verdict}."
        )

    lb = features.get("limit_board")
    if lb and lb.get("touched_days", 0) > 0:
        pct_int = lb.get("pct", 10)
        touched = lb.get("touched_days", 0)
        n_lb = lb.get("n_days", n_days)
        lt = lb.get("last_touch") or {}
        lbl = f"- Limit board: touched +{pct_int}% limit on {touched}/{n_lb} days;"
        if lt:
            date_s = lt.get("date", "")
            seal_t = lt.get("seal_time", "")
            oc = lt.get("open_count", 0)
            fstatus = lt.get("final_status", "")
            if seal_t:
                lbl += f" last {date_s} sealed {seal_t},"
            else:
                lbl += f" last {date_s} touched but not sealed,"
            lbl += f" opened {oc}×,"
            if fstatus == "sealed":
                strength = ("strong follow-through odds" if oc == 0 else "re-sealed after opening")
                lbl += f" closed sealed ({strength})."
            elif fstatus == "failed":
                lbl += " closed failed (distribution signal)."
            else:
                lbl += " closed below limit."
        lines.append(lbl)

    ivol = features.get("intraday_vol")
    if ivol:
        swing = ivol.get("median_swing_pct", 2.0)
        stop_y = ivol.get("stop_yuan", 0.0)
        lines.append(
            f"- Intraday volatility: median daily swing ±{swing:.1f}%; "
            f"under T+1 a stop tighter than ~¥{stop_y:.2f} (1 swing) is likely noise-triggered."
        )

    lines.append(
        "These are intraday-verified levels; reconcile with daily indicators, "
        "do not override the daily trend."
    )
    return "\n".join(lines)

# ── 主入口（fail-open） ───────────────────────────────────────────────────────

def get_intraday_structure(symbol: str, curr_date: str) -> str:
    """A股日内微观结构。始终返回字符串（fail-open，不阻塞分析）。"""
    if not _env_true("INTRADAY_ENABLED"):
        return "Intraday microstructure disabled (INTRADAY_ENABLED=false)."

    try:
        ok, code, _ = parse(symbol)
    except Exception:
        ok = False

    if not ok:
        return "Intraday microstructure not available for non-A-share symbols."

    freq = os.getenv("INTRADAY_FREQ", "5min").strip()
    lookback_days = _cfg_int("INTRADAY_LOOKBACK_DAYS", 10)

    try:
        df, source = get_minute_df(symbol, freq=freq, lookback_days=lookback_days)
    except Exception as e:
        logger.warning(f"[intraday] data fetch failed for {symbol}: {e}")
        return ("Intraday microstructure unavailable (data fetch error); "
                "proceeding on daily data only.")

    if df is None or df.empty:
        return ("Intraday microstructure unavailable (no local TDX minute files "
                "and pytdx unreachable); proceeding on daily data only.")

    try:
        company_name = ""
        try:
            from .context_patch import _IDENTITY_CACHE
            company_name = _IDENTITY_CACHE.get(code, {}).get("company_name", "")
        except Exception:
            pass

        features = compute_intraday_structure(
            df, code=code, company_name=company_name,
            lookback_days=lookback_days,
        )
        return _format_output(features, source=source or "unknown",
                              freq=freq, lookback_days=lookback_days)
    except Exception as e:
        logger.warning(f"[intraday] structure computation failed for {symbol}: {e}")
        return ("Intraday microstructure unavailable (computation error); "
                "proceeding on daily data only.")
