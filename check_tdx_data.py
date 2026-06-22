# -*- coding: utf-8 -*-
"""
通达信本地数据自检脚本

用法:
    python check_tdx_data.py                  # 读取环境变量 TDX_VIPDOC_PATH
    python check_tdx_data.py C:\\new_tdx\\vipdoc
    python check_tdx_data.py C:\\new_tdx\\vipdoc 600519
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tdx_local.reader import TdxLocalReader, normalize_symbol  # noqa: E402


def main():
    vipdoc = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TDX_VIPDOC_PATH", "")
    symbol = sys.argv[2] if len(sys.argv) > 2 else None

    if not vipdoc:
        print("❌ 请指定 vipdoc 路径: python check_tdx_data.py C:\\new_tdx\\vipdoc")
        sys.exit(1)

    reader = TdxLocalReader(vipdoc)
    if not reader.available():
        print(f"❌ 目录不存在: {vipdoc}")
        sys.exit(1)
    print(f"✅ vipdoc 目录: {reader.vipdoc}\n")

    # 统计各市场文件数
    for mkt in ("sh", "sz", "bj"):
        for sub, label in (("lday", "日线"), ("fzline", "5分钟线"), ("minline", "1分钟线")):
            d = reader.vipdoc / mkt / sub
            n = len(list(d.glob("*"))) if d.exists() else 0
            print(f"  {mkt}/{sub:8s} {label:6s}: {n:6d} 个文件")
    print()

    # 选一只股票做读取测试
    if symbol is None:
        for cand in ("600519", "000001", "300750"):
            if reader.has_local(cand, "day"):
                symbol = cand
                break
    if symbol is None:
        lday = reader.vipdoc / "sh" / "lday"
        files = sorted(lday.glob("sh6*.day")) if lday.exists() else []
        if files:
            symbol = files[0].stem[2:]
    if symbol is None:
        print("❌ 未找到任何日线文件，请在通达信中下载日线数据（选项-盘后数据下载）")
        sys.exit(1)

    mkt, code = normalize_symbol(symbol)
    print(f"📊 测试读取 {mkt}{code}:")

    df = reader.read_daily(symbol)
    if df.empty:
        print("  ❌ 日线读取失败/为空")
    else:
        print(f"  ✅ 日线 {len(df)} 条, {df['date'].min().date()} ~ {df['date'].max().date()}")
        print(df.tail(3).to_string(index=False))

    for freq, label in (("5min", "5分钟线"), ("1min", "1分钟线")):
        m = reader.read_minute(symbol, freq)
        if m.empty:
            print(f"  ⚠️ {label} 无本地数据（如需要请在通达信中下载）")
        else:
            print(f"  ✅ {label} {len(m)} 条, 最新: {m['datetime'].max()}")

    # 周线/月线转换测试
    if not df.empty:
        w = reader.resample(df, "weekly")
        mo = reader.resample(df, "monthly")
        print(f"  ✅ 周线转换 {len(w)} 条 / 月线转换 {len(mo)} 条")

    # pytdx 在线回退测试（可选）
    try:
        import pytdx  # noqa: F401
        print("\n🌐 测试 pytdx 在线回退（最多等几秒）...")
        odf = reader.get_bars_online(symbol, period="daily", max_bars=100)
        if odf is not None and not odf.empty:
            print(f"  ✅ 在线获取 {len(odf)} 条, 最新: {odf['date'].max().date()}")
        else:
            print("  ⚠️ 在线接口暂不可用（不影响本地数据使用）")
    except ImportError:
        print("\nℹ️ 未安装 pytdx，跳过在线回退测试 (pip install pytdx)")

    print("\n🎉 自检完成")


if __name__ == "__main__":
    main()
