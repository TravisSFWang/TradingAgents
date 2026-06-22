# -*- coding: utf-8 -*-
"""检查通达信本地数据的覆盖范围与断裂缺口。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TDX_VIPDOC_PATH", r"C:\new_tdx64\vipdoc")

from tdx_local.reader import TdxLocalReader
import pandas as pd

r = TdxLocalReader()
print(f"vipdoc 路径: {r.vipdoc}")
print(f"vipdoc 可用: {r.available()}")
if not r.available():
    print("vipdoc 不可用，退出。")
    sys.exit(1)

print()

# 代表性股票：沪主板、深主板、创业板、科创板
test_stocks = {
    "600519 (茅台-沪主板)": "600519",
    "000001 (平安-深主板)": "000001",
    "300750 (宁德-创业板)": "300750",
    "688017 (科创板)":       "688017",
    "000858 (五粮液)":       "000858",
    "601318 (平安保险)":     "601318",
}

print("=" * 70)
print("日线覆盖范围")
print("=" * 70)
for label, sym in test_stocks.items():
    df = r.read_daily(sym)
    if df.empty:
        print(f"  {label}: 无数据")
        continue
    dates = pd.to_datetime(df["date"]).sort_values().reset_index(drop=True)
    start = dates.iloc[0].strftime("%Y-%m-%d")
    end   = dates.iloc[-1].strftime("%Y-%m-%d")
    n     = len(dates)

    # 断裂：相邻两行自然日差 > 10（覆盖 7 天节假日 + 3 天缓冲）
    diffs = dates.diff().dt.days.dropna()
    gaps  = diffs[diffs > 10]
    gap_str = ""
    if not gaps.empty:
        gap_after = dates[gaps.index].dt.strftime("%Y-%m-%d").tolist()[:5]
        gap_str = f"  ** {len(gaps)} 处断裂，断后第一条: {gap_after}"

    print(f"  {label}: {start} ~ {end}  ({n} 条){gap_str}")

print()
print("=" * 70)
print("5 分钟线覆盖范围")
print("=" * 70)
for label, sym in test_stocks.items():
    if not r.has_local(sym, "5min"):
        print(f"  {label}: 无 5min 文件")
        continue
    df = r.read_minute(sym, freq="5min")
    if df.empty:
        print(f"  {label}: 5min 文件为空")
        continue
    dates = pd.to_datetime(df["datetime"]).sort_values()
    tdays = df["date"].nunique()
    start = dates.iloc[0].strftime("%Y-%m-%d %H:%M")
    end   = dates.iloc[-1].strftime("%Y-%m-%d %H:%M")
    bars  = len(df)

    # 断裂：按日期去重后检查
    day_series = pd.to_datetime(df["date"].unique())
    day_series = pd.Series(sorted(day_series))
    diffs = day_series.diff().dt.days.dropna()
    gaps  = diffs[diffs > 10]
    gap_str = ""
    if not gaps.empty:
        gap_after = day_series[gaps.index].dt.strftime("%Y-%m-%d").tolist()[:3]
        gap_str = f"  ** {len(gaps)} 处断裂，断后: {gap_after}"

    print(f"  {label}: {start} ~ {end}  ({tdays} 交易日, {bars} 条){gap_str}")

print()
print("=" * 70)
print("1 分钟线覆盖范围（仅 600519）")
print("=" * 70)
sym = "600519"
if r.has_local(sym, "1min"):
    df = r.read_minute(sym, freq="1min")
    if not df.empty:
        dates = pd.to_datetime(df["datetime"]).sort_values()
        tdays = df["date"].nunique()
        start = dates.iloc[0].strftime("%Y-%m-%d %H:%M")
        end   = dates.iloc[-1].strftime("%Y-%m-%d %H:%M")
        print(f"  600519 1min: {start} ~ {end}  ({tdays} 交易日, {len(df)} 条)")
    else:
        print("  600519 1min: 文件为空")
else:
    print("  600519: 无 1min 文件（lc1）")

print()
print("检查完毕。")
