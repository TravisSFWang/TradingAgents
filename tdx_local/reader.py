# -*- coding: utf-8 -*-
"""
通达信本地数据读取器

文件格式（每条记录 32 字节）:
- 日线 .day   (vipdoc/<mkt>/lday/<mkt><code>.day):
    date(u32, YYYYMMDD), open(u32, 价格x100), high(u32), low(u32), close(u32),
    amount(f32, 元), volume(u32, 股), reserved(u32)
- 5分钟 .lc5  (vipdoc/<mkt>/fzline/<mkt><code>.lc5) /
  1分钟 .lc1  (vipdoc/<mkt>/minline/<mkt><code>.lc1):
    date(u16, (year-2004)*2048 + month*100 + day), time(u16, hour*60+minute),
    open/high/low/close/amount(f32 x5), volume(u32), reserved(u32)
"""

import os
import struct
from datetime import datetime
from pathlib import Path

import pandas as pd

_DAY_STRUCT = struct.Struct("<IIIIIfII")
_LC_STRUCT = struct.Struct("<HHfffffII")
_REC_SIZE = 32

# pytdx 在线回退使用的行情服务器（依次尝试）
_TDX_SERVERS = [
    ("119.147.212.81", 7709),
    ("119.147.212.81", 7727),
    ("114.80.63.12", 7709),
    ("114.80.63.35", 7709),
    ("180.153.18.170", 7709),
    ("202.108.253.130", 7709),
    ("202.108.253.131", 7709),
    ("60.191.117.167", 7709),
]


def _to_date(d):
    """接受 'YYYY-MM-DD' / 'YYYYMMDD' / datetime / None，返回 pd.Timestamp 或 None"""
    if d is None or d == "":
        return None
    if isinstance(d, (datetime, pd.Timestamp)):
        return pd.Timestamp(d)
    s = str(d).strip().replace("/", "-")
    if "-" not in s and len(s) == 8:
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return pd.Timestamp(s)


def normalize_symbol(symbol):
    """
    解析股票代码，返回 (市场前缀, 6位代码)。
    支持: '600519' / '600519.SH' / 'sh600519' / '000001.SZ' 等
    """
    s = str(symbol).strip().lower()
    for suf, mkt in ((".sh", "sh"), (".ss", "sh"), (".sz", "sz"), (".bj", "bj")):
        if s.endswith(suf):
            return mkt, s[: -len(suf)]
    if len(s) > 6 and s[:2] in ("sh", "sz", "bj"):
        return s[:2], s[2:]
    code = s
    # 按代码段推断市场
    if code.startswith(("600", "601", "603", "605", "688", "689", "900",
                        "110", "111", "113", "118")) or code[0] == "5":
        return "sh", code
    if code.startswith(("000", "001", "002", "003", "300", "301", "200",
                        "120", "123", "127", "128", "12", "15", "16", "18")):
        return "sz", code
    if code.startswith(("43", "83", "87", "88", "92")) or code[0] in "48":
        return "bj", code
    return ("sh", code) if code[0] == "6" else ("sz", code)


class TdxLocalReader:
    """通达信本地 vipdoc 数据读取器，含可选 pytdx 在线回退"""

    def __init__(self, vipdoc_path=None):
        p = vipdoc_path or os.getenv("TDX_VIPDOC_PATH", "")
        self.vipdoc = Path(p) if p else None

    # ---------------- 基础 ----------------

    def available(self) -> bool:
        return self.vipdoc is not None and self.vipdoc.exists()

    def _file_path(self, symbol, kind="day"):
        mkt, code = normalize_symbol(symbol)
        sub, ext = {
            "day": ("lday", ".day"),
            "5min": ("fzline", ".lc5"),
            "1min": ("minline", ".lc1"),
        }[kind]
        return self.vipdoc / mkt / sub / f"{mkt}{code}{ext}"

    def has_local(self, symbol, kind="day") -> bool:
        return self.available() and self._file_path(symbol, kind).exists()

    # ---------------- 本地文件解析 ----------------

    def read_daily(self, symbol, start_date=None, end_date=None) -> pd.DataFrame:
        """读取本地日线，返回列: date, open, high, low, close, vol, amount"""
        fp = self._file_path(symbol, "day")
        if not fp.exists():
            return pd.DataFrame()
        raw = fp.read_bytes()
        n = len(raw) // _REC_SIZE
        rows = []
        for i in range(n):
            d, o, h, l, c, amount, vol, _ = _DAY_STRUCT.unpack_from(raw, i * _REC_SIZE)
            try:
                dt = pd.Timestamp(year=d // 10000, month=d % 10000 // 100, day=d % 100)
            except ValueError:
                continue
            rows.append((dt, o / 100.0, h / 100.0, l / 100.0, c / 100.0,
                         float(vol), float(amount)))
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close",
                                         "vol", "amount"])
        return self._slice(df, start_date, end_date)

    def read_minute(self, symbol, freq="5min", start_date=None, end_date=None) -> pd.DataFrame:
        """读取本地分钟线 (freq='5min' 或 '1min')，返回含 datetime 列的 DataFrame"""
        kind = "5min" if str(freq).startswith("5") else "1min"
        fp = self._file_path(symbol, kind)
        if not fp.exists():
            return pd.DataFrame()
        raw = fp.read_bytes()
        n = len(raw) // _REC_SIZE
        rows = []
        for i in range(n):
            d, t, o, h, l, c, amount, vol, _ = _LC_STRUCT.unpack_from(raw, i * _REC_SIZE)
            year = d // 2048 + 2004
            month = d % 2048 // 100
            day = d % 2048 % 100
            try:
                dt = pd.Timestamp(year=year, month=month, day=day,
                                  hour=t // 60, minute=t % 60)
            except ValueError:
                continue
            rows.append((dt, round(o, 3), round(h, 3), round(l, 3), round(c, 3),
                         float(vol), float(amount)))
        df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close",
                                         "vol", "amount"])
        if df.empty:
            return df
        df["date"] = df["datetime"].dt.normalize()
        return self._slice(df, start_date, end_date)

    @staticmethod
    def _slice(df, start_date, end_date):
        if df.empty:
            return df
        s, e = _to_date(start_date), _to_date(end_date)
        if s is not None:
            df = df[df["date"] >= s]
        if e is not None:
            df = df[df["date"] <= e]
        return df.reset_index(drop=True)

    # ---------------- 周期转换 ----------------

    @staticmethod
    def resample(df_daily: pd.DataFrame, period: str) -> pd.DataFrame:
        """日线 -> 周线/月线"""
        if df_daily.empty or period == "daily":
            return df_daily
        rule = {"weekly": "W-FRI", "monthly": "ME"}.get(period)
        if rule is None:
            return df_daily
        g = df_daily.set_index("date")
        try:
            r = g.resample(rule)
        except ValueError:  # pandas < 2.2 不支持 'ME'
            r = g.resample(rule.replace("ME", "M"))
        out = r.agg({"open": "first", "high": "max", "low": "min",
                     "close": "last", "vol": "sum", "amount": "sum"}).dropna()
        return out.reset_index()

    # ---------------- 统一入口 ----------------

    def get_bars(self, symbol, start_date=None, end_date=None, period="daily") -> pd.DataFrame:
        """
        统一获取接口。
        period: daily / weekly / monthly / 5min / 1min
        """
        if period in ("5min", "1min"):
            return self.read_minute(symbol, period, start_date, end_date)
        daily = self.read_daily(symbol, start_date, end_date)
        return self.resample(daily, period)

    # ---------------- pytdx 在线回退 ----------------

    def get_bars_online(self, symbol, start_date=None, end_date=None,
                        period="daily", max_bars=2400) -> pd.DataFrame:
        """通过 pytdx 在线接口获取数据（需 pip install pytdx；不支持北交所）"""
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            return pd.DataFrame()

        mkt, code = normalize_symbol(symbol)
        if mkt == "bj":
            return pd.DataFrame()
        market = 1 if mkt == "sh" else 0
        category = {"daily": 9, "weekly": 5, "monthly": 6,
                    "5min": 0, "1min": 8}.get(period, 9)

        api = TdxHq_API()
        for host, port in _TDX_SERVERS:
            try:
                if not api.connect(host, port, time_out=5):
                    continue
                bars = []
                for start in range(0, max_bars, 800):
                    chunk = api.get_security_bars(category, market, code,
                                                  start, min(800, max_bars - start))
                    if not chunk:
                        break
                    bars = chunk + bars
                api.disconnect()
                if not bars:
                    return pd.DataFrame()
                df = pd.DataFrame(bars)
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.rename(columns={"vol": "vol"})
                df["date"] = df["datetime"].dt.normalize()
                df = df[["date", "datetime", "open", "high", "low", "close",
                         "vol", "amount"]].sort_values("datetime")
                if period in ("daily", "weekly", "monthly"):
                    df = df.drop(columns=["datetime"])
                return self._slice(df.reset_index(drop=True), start_date, end_date)
            except Exception:
                try:
                    api.disconnect()
                except Exception:
                    pass
                continue
        return pd.DataFrame()
