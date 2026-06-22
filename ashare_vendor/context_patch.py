# -*- coding: utf-8 -*-
"""
A股市场上下文补丁（借鉴 CN 版的领域知识，但保持官方的英文内部推理策略）

官方框架在每次分析开始时调用 TradingAgentsGraph.resolve_instrument_context()，
生成的 instrument_context 会注入所有智能体的提示词。本补丁:
1. A股代码跳过 yfinance 身份查询（必然失败且浪费时间），改用 AKShare 获取
   公司名称/行业（缓存、失败不阻塞）
2. 追加一段英文的 A股交易规则上下文（涨跌停按板块区分、T+1、手数、
   散户主导特征、北向资金），让智能体推理时考虑 A股市场机制
3. 非A股代码走官方原逻辑，零影响

英文注入是有意为之: 主流模型英文推理质量更高，官方通过 output_language
配置在输出层翻译成中文 —— 我们保留这一分层。
"""

import logging

from .symbols import parse

logger = logging.getLogger("ashare_vendor")

_IDENTITY_CACHE = {}

_BOARD_RULES = {
    # (代码前缀判断, 板块名, 涨跌停幅度)
    "star": ("STAR Market (科创板, Shanghai)", "20%"),
    "chinext": ("ChiNext (创业板, Shenzhen)", "20%"),
    "bse": ("Beijing Stock Exchange (北交所)", "30%"),
    "main": ("Main Board", "10%"),
}


def _board(code: str, mkt: str):
    if code.startswith(("688", "689")):
        return _BOARD_RULES["star"]
    if code.startswith(("300", "301")):
        return _BOARD_RULES["chinext"]
    if mkt == "bj":
        return _BOARD_RULES["bse"]
    return _BOARD_RULES["main"]


def _resolve_ashare_identity(code: str) -> dict:
    """解析公司当前名称/行业（多级回退，缓存，失败不阻塞）。
    名称必须解析成功才能防止模型用训练数据里的旧名（A股改名常见，
    如 600641 万业企业→先导基电）。"""
    if code in _IDENTITY_CACHE:
        return _IDENTITY_CACHE[code]
    identity = {}
    # 0) Tushare（配置中转站时最可靠）
    try:
        from . import ts_relay
        if ts_relay.configured():
            info = ts_relay.stock_basic(code)
            if info.get("name"):
                identity["company_name"] = str(info["name"])
            if info.get("industry"):
                identity["industry"] = str(info["industry"])
    except Exception as e:
        logger.debug(f"[ashare] tushare 身份解析失败 {code}: {e}")
    if identity.get("company_name"):
        _IDENTITY_CACHE[code] = identity
        return identity
    # 1) AKShare 个股信息
    try:
        import akshare as ak
        info = ak.stock_individual_info_em(symbol=code)
        kv = dict(zip(info["item"].astype(str), info["value"]))
        if kv.get("股票简称"):
            identity["company_name"] = str(kv["股票简称"])
        if kv.get("行业"):
            identity["industry"] = str(kv["行业"])
    except Exception as e:
        logger.debug(f"[ashare] akshare 个股信息失败 {code}: {e}")
    # 2) 东方财富直连行情快照
    if not identity.get("company_name"):
        try:
            from . import emdirect
            q = emdirect.quote(code)
            if q.get("name"):
                identity["company_name"] = str(q["name"])
            if q.get("industry") and not identity.get("industry"):
                identity["industry"] = str(q["industry"])
        except Exception as e:
            logger.debug(f"[ashare] 东财直连身份失败 {code}: {e}")
    # 3) AKShare 全市场代码-名称表（交易所源）
    if not identity.get("company_name"):
        try:
            import akshare as ak
            tbl = ak.stock_info_a_code_name()
            row = tbl[tbl["code"].astype(str) == code]
            if not row.empty:
                identity["company_name"] = str(row.iloc[0]["name"])
        except Exception as e:
            logger.debug(f"[ashare] 代码名称表失败 {code}: {e}")
    if not identity.get("company_name"):
        logger.warning(f"[ashare] ⚠️ 无法解析 {code} 的公司名称，"
                       f"模型可能使用过时名称，请检查网络")
    _IDENTITY_CACHE[code] = identity
    return identity


def build_ashare_context(ticker: str) -> str:
    """构造 A股 instrument_context（英文，供智能体推理用）"""
    _, code, mkt = parse(ticker)
    exchange = {"sh": "Shanghai Stock Exchange", "sz": "Shenzhen Stock Exchange",
                "bj": "Beijing Stock Exchange"}[mkt]
    board_name, limit = _board(code, mkt)
    identity = _resolve_ashare_identity(code)

    parts = [
        f"The instrument to analyze is `{ticker}`, a China A-share listed on the "
        f"{exchange} ({board_name}). Use this exact ticker in every tool call, "
        f"report, and recommendation."
    ]
    if identity.get("company_name"):
        ind = f"; Industry: {identity['industry']}" if identity.get("industry") else ""
        parts.append(
            f"Resolved identity: Company: {identity['company_name']}{ind}. "
            "Do not substitute a different company or ticker unless a tool result "
            "explicitly disproves this resolved identity.")
    else:
        parts.append(
            "IMPORTANT: the current company name could NOT be verified from live "
            "data. A-share companies are frequently renamed, so do NOT state a "
            "company name from memory — refer to the instrument by its ticker "
            "only, unless a tool result provides the verified current name.")

    parts.append(
        "A-share market mechanics that MUST inform your analysis: "
        f"(1) Daily price limit of +/-{limit} vs previous close (5% for ST-flagged "
        "stocks); consecutive limit-up/limit-down days indicate one-sided liquidity "
        "where exits may be impossible. "
        "(2) T+1 settlement: shares bought today cannot be sold until the next "
        "trading day, so same-day stop-losses are impossible — size positions "
        "accordingly. "
        "(3) Board lot is 100 shares; prices are quoted in CNY (¥), not USD. "
        "(4) Short selling is restricted (margin-eligible lists only), so bearish "
        "views are mainly expressed by exiting or avoiding, which shapes downside "
        "dynamics. "
        "(5) Turnover is retail-dominated, amplifying momentum, theme rotation and "
        "sentiment effects; northbound Stock Connect flows and 主力 (main-force) "
        "fund flows are widely watched institutional signals. "
        "(6) Key policy levers (PBoC liquidity, CSRC regulation, state media tone) "
        "can dominate fundamentals on short horizons.")

    # 数据驱动的市场风格快照（科技/双创吸血等行情特征）
    try:
        from datetime import datetime as _dtm
        from .ts_extras import market_regime
        regime = market_regime(_dtm.now().strftime("%Y-%m-%d"))
        if regime:
            parts.append(regime)
    except Exception as e:
        logger.debug(f"[ashare] 市场风格快照失败: {e}")

    # 文体约束已上移到 get_language_instruction()（拼到每个 agent 提示词末尾、
    # 位置更强），此处不再重复注入，避免冗余 token。

    return " ".join(parts)


def apply_context_patch(graph_cls) -> bool:
    """包装 TradingAgentsGraph.resolve_instrument_context；幂等"""
    if getattr(graph_cls, "_ashare_context_patched", False):
        return True
    orig = graph_cls.resolve_instrument_context

    def resolve_instrument_context(self, ticker, asset_type="stock"):
        try:
            ok, _, _ = parse(ticker)
        except Exception:
            ok = False
        base = None
        if ok and asset_type == "stock":
            try:
                base = build_ashare_context(ticker)
                logger.info(f"[ashare] 已注入A股市场上下文: {ticker}")
            except Exception as e:
                logger.warning(f"[ashare] A股上下文构造失败，回退官方逻辑: {e}")
        if base is None:
            base = orig(self, ticker, asset_type)
        # 本地资料夹注入（CLAUDE.md §11）：fail-open，无资料/未启用返回空串零开销。
        # 注入进 instrument_context 即随 state 存入 full_states_log 快照，
        # "记录到当次分析"无需额外通道。
        try:
            from .library import build_library_context
            extra = build_library_context(ticker)
            if extra:
                base = f"{base}\n\n{extra}"
        except Exception as e:
            logger.warning(f"[ashare] 本地资料注入失败（跳过）: {e}")
        return base

    graph_cls.resolve_instrument_context = resolve_instrument_context
    graph_cls._ashare_context_patched = True
    logger.info("✅ A股市场上下文补丁已生效")
    return True
