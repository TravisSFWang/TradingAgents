from typing import Annotated

from langchain_core.tools import tool


@tool
def get_intraday_structure(
    symbol: Annotated[str, "A-share ticker symbol, e.g. '600519' or '600519.SH'"],
    curr_date: Annotated[str, "Current trading date, YYYY-mm-dd"],
) -> str:
    """Retrieve intraday (minute-bar) microstructure for an A-share: volume-profile
    support/resistance (POC + value area), VWAP cost basis, intraday character
    (accumulation/distribution), limit-board dynamics, and T+1-aware stop sizing.

    Call this once per analysis to get intraday-verified price levels.
    Non-A-share symbols and missing data return a graceful one-liner.
    """
    try:
        from ashare_vendor.intraday import get_intraday_structure as _impl
        return _impl(symbol, curr_date)
    except Exception as e:
        return (f"Intraday microstructure unavailable ({e}); "
                "proceeding on daily data only.")
