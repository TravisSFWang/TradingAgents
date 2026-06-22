# -*- coding: utf-8 -*-
"""A股代码识别与归一化 + 全局数据源开关"""

import os


def use_akshare() -> bool:
    """AKShare 总开关。财联社/东财对 akshare 部署了反爬后默认禁用，
    需要时在 .env 设 ASHARE_USE_AKSHARE=true 重新启用"""
    return os.getenv("ASHARE_USE_AKSHARE", "false").strip().lower() in ("true", "1", "yes")

_SUFFIXES = (".sh", ".ss", ".sz", ".bj")


def parse(symbol):
    """返回 (是否A股, 6位代码, 市场前缀 sh/sz/bj)"""
    s = str(symbol).strip().lower()
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    if s[:2] in ("sh", "sz", "bj") and len(s) > 6:
        s = s[2:]
    if len(s) != 6 or not s.isdigit():
        return False, None, None
    code = s
    if code.startswith(("600", "601", "603", "605", "688", "689", "900",
                        "110", "111", "113", "118")) or code[0] == "5":
        return True, code, "sh"
    if code.startswith(("000", "001", "002", "003", "300", "301", "200",
                        "12", "15", "16", "18")):
        return True, code, "sz"
    if code.startswith(("43", "83", "87", "88", "92")) or code[0] in "48":
        return True, code, "bj"
    if code[0] == "6":
        return True, code, "sh"
    return True, code, "sz"


def is_a_share(symbol) -> bool:
    return parse(symbol)[0]


def to_code(symbol) -> str:
    """'600519.SS' / 'sh600519' / '600519' -> '600519'"""
    ok, code, _ = parse(symbol)
    return code if ok else str(symbol)


def reject_if_not_a_share(symbol):
    """非A股代码抛 NoMarketDataError，让官方路由层转交 yfinance 等其他 vendor"""
    if not is_a_share(symbol):
        from tradingagents.dataflows.symbol_utils import NoMarketDataError
        raise NoMarketDataError(symbol, str(symbol), "not an A-share symbol (ashare vendor skipped)")
