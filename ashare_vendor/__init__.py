"""
ashare_vendor - 官方 TauricResearch/TradingAgents 的 A股数据适配包

设计（借鉴 TradingAgents-CN 思路，但基于官方 vendor 路由架构，零侵入注册）:
- 注册为官方框架的新数据 vendor "ashare"，覆盖全部 9 个数据工具
- 行情: 通达信本地 vipdoc -> pytdx 在线 -> AKShare (自动降级)
- 新闻/财务/基本面: AKShare (东方财富/财联社/新浪)
- 非 A 股代码自动抛 NoMarketDataError，路由层无缝转交 yfinance，
  美股/港股分析不受任何影响

环境变量:
- TDX_VIPDOC_PATH        通达信 vipdoc 目录（不设则跳过本地数据）
- ASHARE_PRICE_SOURCE    行情主源 tdx / akshare（默认 tdx）
- ASHARE_ADJUST          AKShare 复权方式 qfq/hfq/""（默认 qfq）
- TDX_ONLINE_FALLBACK    是否启用 pytdx 在线回退（默认 true）
- TDX_MAX_STALE_DAYS     本地数据允许滞后天数（默认 7）
- ASHARE_VENDOR_PRIORITY 注册后是否自动设为最高优先级（默认 true）
"""

__version__ = "1.0.0"

from .register import register  # noqa: F401
