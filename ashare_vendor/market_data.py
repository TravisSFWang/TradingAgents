# -*- coding: utf-8 -*-
"""
A股行情数据: 通达信本地 vipdoc -> pytdx 在线 -> AKShare 自动降级

对外提供:
- get_daily_df(symbol, start, end) -> (DataFrame, 来源标签)
- get_stock_data(symbol, start_date, end_date) -> str   (官方 get_stock_data 工具的 ashare 实现)
"""

import logging
import os
from datetime import datetime, timedelta

import pandas as pd

from .symbols import parse, reject_if_not_a_share

logger = logging.getLogger("ashare_vendor")


def _env_true(key, default="true"):
    return os.getenv(key, default).strip().lower() in ("true", "1", "yes", "on")


def _stale_days():
    try:
        return int(os.getenv("TDX_MAX_STALE_DAYS", "7"))
    except ValueError:
        return 7


def _covers_window(df, end_date) -> bool:
    """本地数据是否覆盖到请求区间末尾（允许 TDX_MAX_STALE_DAYS 的缺口）"""
    if df is None or df.empty:
        return False
    need = min(pd.Timestamp(end_date), pd.Timestamp(datetime.now().date()))
    return (need - pd.Timestamp(df["date"].max())) <= timedelta(days=_stale_days())


def _fetch_tdx_local(symbol, start, end):
    vipdoc = os.getenv("TDX_VIPDOC_PATH", "")
    if not vipdoc:
        return None
    try:
        from tdx_local.reader import TdxLocalReader
        r = TdxLocalReader(vipdoc)
        if not r.available():
            return None
        df = r.read_daily(symbol, start, end)
        return df if not df.empty else None
    except Exception as e:
        logger.warning(f"[ashare] 通达信本地读取失败 {symbol}: {e}")
        return None


def _fetch_tdx_online(symbol, start, end):
    if not _env_true("TDX_ONLINE_FALLBACK"):
        return None
    try:
        from tdx_local.reader import TdxLocalReader
        df = TdxLocalReader().get_bars_online(symbol, start, end, "daily")
        return df if df is not None and not df.empty else None
    except Exception as e:
        logger.warning(f"[ashare] pytdx 在线获取失败 {symbol}: {e}")
        return None


def _fetch_akshare(symbol, start, end):
    from .symbols import use_akshare
    if not use_akshare():
        return None
    try:
        import akshare as ak
        _, code, _ = parse(symbol)
        adjust = os.getenv("ASHARE_ADJUST", "qfq")
        raw = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=str(start).replace("-", ""),
            end_date=str(end).replace("-", ""),
            adjust="" if adjust in ("none", "raw") else adjust,
        )
        if raw is None or raw.empty:
            return None
        df = raw.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
            "收盘": "close", "成交量": "vol", "成交额": "amount"})
        df["date"] = pd.to_datetime(df["date"])
        df["vol"] = pd.to_numeric(df["vol"], errors="coerce") * 100  # 手 -> 股
        return df[["date", "open", "high", "low", "close", "vol", "amount"]]
    except Exception as e:
        logger.warning(f"[ashare] AKShare 行情获取失败 {symbol}: {e}")
        return None


def _fetch_tushare(symbol, start, end):
    """Tushare pro.daily（不复权），支持第三方中转站（TUSHARE_HTTP_URL）"""
    from . import ts_relay
    if not ts_relay.configured():
        return None
    try:
        raw = ts_relay.pro().daily(ts_code=ts_relay.ts_code(symbol),
                                   start_date=str(start).replace("-", ""),
                                   end_date=str(end).replace("-", ""))
        if raw is None or raw.empty:
            return None
        df = raw.rename(columns={"trade_date": "date"})
        df["date"] = pd.to_datetime(df["date"])
        df["vol"] = pd.to_numeric(df["vol"], errors="coerce") * 100      # 手 -> 股
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000  # 千元 -> 元
        return df[["date", "open", "high", "low", "close", "vol", "amount"]] \
            .sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"[ashare] Tushare 行情获取失败 {symbol}: {e}")
        return None


_FETCHERS = {
    "tdx": ("通达信本地", _fetch_tdx_local),
    "pytdx": ("通达信在线(pytdx)", _fetch_tdx_online),
    "akshare": ("AKShare", _fetch_akshare),
    "tushare": ("Tushare", _fetch_tushare),
}


def _build_chain():
    """降级链可用 ASHARE_PRICE_CHAIN 自定义，如 'tdx,pytdx,tushare,akshare'；
    否则按 ASHARE_PRICE_SOURCE(tdx/akshare) 生成默认链"""
    raw = os.getenv("ASHARE_PRICE_CHAIN", "").strip().lower()
    if raw:
        names = [n.strip() for n in raw.split(",") if n.strip() in _FETCHERS]
        if names:
            return [_FETCHERS[n] for n in names]
    # 配置了 Tushare 中转站 -> Tushare 最优先
    from . import ts_relay
    if ts_relay.preferred():
        return [_FETCHERS[n] for n in ["tushare", "tdx", "pytdx", "akshare"]]
    prefer = os.getenv("ASHARE_PRICE_SOURCE", "tdx").strip().lower()
    order = (["tdx", "pytdx", "akshare", "tushare"] if prefer == "tdx"
             else ["akshare", "tushare", "tdx", "pytdx"])
    return [_FETCHERS[n] for n in order]


def get_daily_df(symbol, start, end):
    """按降级链取日线。返回 (df, 来源标签)；全部失败返回 (空df, None)"""
    chain = _build_chain()

    fallback_df, fallback_label = None, None
    for label, fn in chain:
        df = fn(symbol, start, end)
        if df is None or df.empty:
            continue
        # 本地数据若没覆盖到请求末尾，继续尝试下一级，但保底留着
        if label == "通达信本地" and not _covers_window(df, end):
            logger.info(f"[ashare] {symbol} 本地数据未覆盖到 {end}，尝试在线源")
            fallback_df, fallback_label = df, label
            continue
        logger.info(f"[ashare] {symbol} 日线 {len(df)} 条，来源: {label}")
        return df.reset_index(drop=True), label

    if fallback_df is not None:
        logger.info(f"[ashare] {symbol} 在线源均失败，使用未完全覆盖的本地数据")
        return fallback_df.reset_index(drop=True), fallback_label
    return pd.DataFrame(), None


def get_stock_data(symbol, start_date, end_date):
    """官方 get_stock_data 工具的 ashare 实现：返回带表头的 CSV 文本"""
    reject_if_not_a_share(symbol)
    datetime.strptime(str(start_date), "%Y-%m-%d")
    datetime.strptime(str(end_date), "%Y-%m-%d")

    df, source = get_daily_df(symbol, start_date, end_date)
    if df.empty:
        from tradingagents.dataflows.symbol_utils import NoMarketDataError
        raise NoMarketDataError(symbol, str(symbol),
                                f"no A-share rows between {start_date} and {end_date}")

    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.rename(columns={"date": "Date", "open": "Open", "high": "High",
                              "low": "Low", "close": "Close", "vol": "Volume",
                              "amount": "Amount"})
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col].round(2)

    adjust_note = (f"复权方式: {os.getenv('ASHARE_ADJUST', 'qfq')}"
                   if source == "AKShare" else "不复权(原始价)")
    header = (
        f"# Stock data for A-share {symbol} from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Data source: {source} | {adjust_note} | Volume unit: 股\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + out.to_csv(index=False)


# A股指数代码（结算层 benchmark 用），走 Tushare index_daily 而非个股降级链
_INDEX_CODES = {"000001", "000016", "000300", "000688", "000852", "000905",
                "399001", "399005", "399006", "399300"}


def get_close_series(symbol, start, end):
    """收盘价序列（pandas Series，按日期升序，index 为 'YYYY-MM-DD' 字符串）。

    供反思/结算层算收益用，避免依赖 yfinance：
    - A股个股 -> 现有本地/Tushare 降级链 get_daily_df
    - A股指数（benchmark，如 000001.SS/399001.SZ）-> Tushare index_daily
    非A股代码或失败返回 None，交由调用方回退。
    symbol 接受 '688017' / '688017.SH' / '000001.SS' 等写法。
    """
    low = str(symbol).strip().lower()
    suf2ex = {".ss": "SH", ".sh": "SH", ".sz": "SZ", ".bj": "BJ"}
    exch, body = None, low
    for suf, ex in suf2ex.items():
        if low.endswith(suf):
            exch, body = ex, low[: -len(suf)]
            break
    if body[:2] in ("sh", "sz", "bj") and len(body) > 6:
        exch = exch or body[:2].upper()
        body = body[2:]
    code = body
    if len(code) != 6 or not code.isdigit():
        return None  # 非A股，调用方回退

    if code in _INDEX_CODES:
        ex = exch or ("SZ" if code.startswith("39") else "SH")
        try:
            from . import ts_relay
            if not ts_relay.configured():
                return None
            df = ts_relay.pro().index_daily(
                ts_code=f"{code}.{ex}",
                start_date=str(start).replace("-", ""),
                end_date=str(end).replace("-", ""))
            if df is None or df.empty:
                return None
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.sort_values("trade_date")
            return df.set_index("trade_date")["close"].astype(float)
        except Exception as e:
            logger.warning(f"[ashare] 指数收盘序列失败 {symbol}: {e}")
            return None

    df, _src = get_daily_df(code, start, end)
    if df is None or df.empty:
        return None
    out = df.sort_values("date")
    return pd.Series(out["close"].astype(float).values,
                     index=pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d"))
