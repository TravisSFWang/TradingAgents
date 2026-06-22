# -*- coding: utf-8 -*-
"""
A股新闻类工具的 ashare 实现（数据源: AKShare）
- get_news: 个股新闻（东方财富）
- get_global_news: 宏观快讯（财联社电报，回退东方财富全球财经快讯）
- get_insider_transactions: 高管/股东增减持（东方财富）
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from .symbols import parse, reject_if_not_a_share

logger = logging.getLogger("ashare_vendor")


def _cfg(key, default):
    try:
        from tradingagents.dataflows.config import get_config
        return get_config().get(key) or default
    except Exception:
        return default


def _timed(fn, *args, **kwargs):
    """给 akshare 抓取加超时（默认45秒，ASHARE_NEWS_TIMEOUT 可调），
    防止接口挂起导致整个分析流程卡死。用守护线程，超时后不阻塞进程退出。"""
    import os
    import threading
    timeout = int(os.getenv("ASHARE_NEWS_TIMEOUT", "45"))
    box = {}

    def _run():
        try:
            box["r"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"akshare 接口超时(>{timeout}s)")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def get_news(ticker, start_date, end_date):
    """个股新闻：ak.stock_news_em，按日期窗口过滤"""
    reject_if_not_a_share(ticker)
    _, code, _ = parse(ticker)
    limit = int(_cfg("news_article_limit", 20))

    raw = None
    from .symbols import use_akshare
    if use_akshare():
        try:
            import akshare as ak
            raw = _timed(ak.stock_news_em, symbol=code)
        except Exception as e:
            logger.warning(f"[ashare] akshare 个股新闻失败 {code}: {e}，尝试东财直连")

    if raw is None or raw.empty:
        # 兜底: 东方财富搜索 API 直连（AKShare 接口故障时仍可用）
        try:
            from . import emdirect
            arts = _timed(emdirect.stock_news, code, limit=max(limit, 30))
            if arts:
                raw = pd.DataFrame({
                    "新闻标题": [a["title"] for a in arts],
                    "新闻内容": [a["content"] for a in arts],
                    "发布时间": [a["time"] for a in arts],
                    "文章来源": [a["source"] for a in arts]})
                logger.info(f"[ashare] {code} 东财直连获取新闻 {len(raw)} 条")
        except Exception as e:
            logger.warning(f"[ashare] 东财直连新闻也失败 {code}: {e}")

    if raw is None or raw.empty:
        return (f"## {ticker} 新闻暂不可用（akshare 与东财直连均失败，"
                f"请检查网络/代理设置，或升级 akshare）")

    df = raw.copy()
    df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
    s = pd.Timestamp(start_date)
    e = pd.Timestamp(end_date) + timedelta(days=1)
    win = df[(df["发布时间"] >= s) & (df["发布时间"] < e)]
    note = ""
    if win.empty:
        # 东方财富只返回最近约100条；回测较早日期时窗口可能为空，给最近新闻并注明
        win = df.head(limit)
        note = ("\n> 注意: 接口仅提供最近约100条新闻，请求窗口内无数据，"
                "以下为当前最新新闻，时间可能晚于分析日期。\n")
    win = win.sort_values("发布时间", ascending=False).head(limit)

    blocks = []
    for _, r in win.iterrows():
        t = r["发布时间"].strftime("%Y-%m-%d %H:%M") if pd.notna(r["发布时间"]) else ""
        content = str(r.get("新闻内容", "") or "")[:300]
        blocks.append(f"### {r.get('新闻标题', '')} ({t})\n来源: {r.get('文章来源', '')}\n{content}")

    result = (f"## {ticker} 个股新闻 ({start_date} ~ {end_date}), 共{len(win)}条:{note}\n\n"
              + "\n\n".join(blocks))
    # 金十快讯补充（按公司名搜索）
    try:
        from . import jin10
        if jin10.configured():
            from .context_patch import _resolve_ashare_identity
            name = _resolve_ashare_identity(code).get("company_name")
            if name:
                hits = _timed(jin10.flash_search, name, limit=8)
                if hits:
                    result += ("\n\n## 相关快讯 (金十数据):\n" +
                               "\n".join(f"- [{h['time']}] {h['content'][:200]}"
                                         for h in hits))
    except Exception as e:
        logger.debug(f"[ashare] 金十个股快讯失败 {code}: {e}")
    # Tushare 特色数据: 资金流向/龙虎榜/涨停/两融/筹码（消息面分析师可用）
    try:
        from .ts_extras import trading_extras
        result += trading_extras(code, end_date)
    except Exception as e:
        logger.debug(f"[ashare] 交易特色数据失败 {code}: {e}")
    return result


def get_global_news(curr_date, look_back_days=None, limit=None):
    """宏观/全市场快讯：财联社电报优先，回退东方财富全球财经直播"""
    look_back = int(look_back_days or _cfg("global_news_lookback_days", 7))
    max_n = int(limit or _cfg("global_news_article_limit", 10))
    e = pd.Timestamp(curr_date) + timedelta(days=1)
    s = e - timedelta(days=look_back + 1)

    # Tushare 快讯优先（中转站支持 news 接口；财联社源）
    try:
        from . import ts_relay
        if ts_relay.configured():
            df = _timed(ts_relay.pro().news, src="cls",
                        start_date=s.strftime("%Y-%m-%d %H:%M:%S"),
                        end_date=e.strftime("%Y-%m-%d %H:%M:%S"))
            if df is not None and not df.empty:
                df["dt"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df.sort_values("dt", ascending=False).head(max_n)
                blocks = [f"### {r['dt'].strftime('%Y-%m-%d %H:%M')} [财联社] "
                          f"{str(r.get('title', '') or '')}\n{str(r.get('content', '') or '')[:300]}"
                          for _, r in df.iterrows()]
                result = (f"## A股宏观/市场快讯 (Tushare/财联社, 截至 {curr_date}, "
                          f"回看{look_back}天):\n\n" + "\n\n".join(blocks))
                try:
                    from .ts_extras import macro_snapshot
                    result += macro_snapshot(curr_date)
                except Exception as e2:
                    logger.debug(f"[ashare] 宏观快照失败: {e2}")
                return result
    except Exception as e1:
        logger.warning(f"[ashare] tushare 快讯失败: {e1}")

    # 金十快讯 + 财经日历（第二优先级）
    try:
        from . import jin10
        if jin10.configured():
            items = _timed(jin10.flash_list, limit=max_n + 5)
            if items:
                blocks = [f"### {it['time']} [金十] {it['content'][:300]}"
                          for it in items[:max_n]]
                result = (f"## A股宏观/市场快讯 (金十数据, 截至 {curr_date}):\n\n"
                          + "\n\n".join(blocks))
                try:
                    cal = _timed(jin10.calendar, limit=10)
                    if cal:
                        result += ("\n\n## 财经日历 (金十):\n" + "\n".join(
                            f"- [{c.get('pub_time', '')}] {'★' * int(c.get('star') or 0)} "
                            f"{c.get('title', '')} 前值:{c.get('previous', '—')} "
                            f"预期:{c.get('consensus', '—')} 公布:{c.get('actual', '—')}"
                            for c in cal))
                except Exception:
                    pass
                try:
                    from .ts_extras import macro_snapshot
                    result += macro_snapshot(curr_date)
                except Exception:
                    pass
                return result
    except Exception as ej:
        logger.warning(f"[ashare] 金十快讯失败: {ej}")

    from .symbols import use_akshare
    if not use_akshare():
        return (f"## 宏观快讯暂不可用（Tushare/金十均失败，AKShare 已禁用。"
                f"请检查 TUSHARE_TOKEN / JIN10_API_KEY 配置）")

    import akshare as ak
    rows = []
    try:
        raw = _timed(ak.stock_info_global_cls)
        raw["dt"] = pd.to_datetime(
            raw["发布日期"].astype(str) + " " + raw["发布时间"].astype(str),
            errors="coerce")
        win = raw[(raw["dt"] >= s) & (raw["dt"] < e)].sort_values("dt", ascending=False)
        for _, r in win.head(max_n).iterrows():
            rows.append((r["dt"], str(r.get("标题", "") or ""), str(r.get("内容", "") or "")[:300], "财联社"))
    except Exception as e1:
        logger.warning(f"[ashare] 财联社快讯失败: {e1}")
        try:
            raw = _timed(ak.stock_info_global_em)
            raw["dt"] = pd.to_datetime(raw["发布时间"], errors="coerce")
            win = raw[(raw["dt"] >= s) & (raw["dt"] < e)].sort_values("dt", ascending=False)
            for _, r in win.head(max_n).iterrows():
                rows.append((r["dt"], str(r.get("标题", "") or ""), str(r.get("摘要", "") or "")[:300], "东方财富"))
        except Exception as e2:
            return f"## 宏观快讯获取失败: 财联社({e1}) / 东方财富({e2})"

    if not rows:
        return (f"## {curr_date} 前{look_back}天无宏观快讯数据"
                "（快讯接口仅保留近期内容，回测较早日期时为空属正常）")

    blocks = [f"### {t.strftime('%Y-%m-%d %H:%M')} [{src}] {title}\n{content}"
              for t, title, content, src in rows]
    return f"## A股宏观/市场快讯 (截至 {curr_date}, 回看{look_back}天):\n\n" + "\n\n".join(blocks)


def get_insider_transactions(ticker):
    """高管及重要股东增减持：ak.stock_ggcg_em"""
    reject_if_not_a_share(ticker)
    _, code, _ = parse(ticker)

    # Tushare 股东增减持优先
    try:
        from . import ts_relay
        if ts_relay.configured():
            df = _timed(ts_relay.pro().stk_holdertrade,
                        ts_code=ts_relay.ts_code(code))
            if df is not None and not df.empty:
                df = df.sort_values("ann_date", ascending=False).head(20)
                label = {"IN": "增持", "DE": "减持"}
                lines = []
                for _, r in df.iterrows():
                    lines.append(
                        f"公告日:{r.get('ann_date', '')} | 股东:{r.get('holder_name', '')} "
                        f"| 类型:{label.get(str(r.get('in_de', '')), r.get('in_de', ''))} "
                        f"| 变动股数:{r.get('change_vol', '')}万股 "
                        f"| 变动后持股比例:{r.get('after_share', '')}")
                return (f"## {ticker} 股东增减持 (Tushare, 最近{len(df)}条):\n\n"
                        + "\n".join(lines))
    except Exception as e:
        logger.warning(f"[ashare] tushare 增减持失败 {code}: {e}")

    from .symbols import use_akshare
    if not use_akshare():
        return f"## {ticker} 增减持: Tushare 暂无数据（AKShare 已禁用）"

    import akshare as ak
    try:
        raw = _timed(ak.stock_ggcg_em, symbol="全部")
        df = raw[raw["代码"].astype(str) == code]
    except Exception as e:
        logger.warning(f"[ashare] 增减持数据失败 {code}: {e}")
        return f"## {ticker} 高管增减持数据暂不可用: {e}"

    if df is None or df.empty:
        return f"## {ticker} 近期无高管/重要股东增减持记录"

    # 字段名随接口版本浮动，模糊挑选关键列
    want = ("名称", "变动人", "变动股数", "成交均价", "变动金额", "占总股本比例",
            "变动比例", "变动原因", "变动开始日", "变动截止日", "公告日")
    cols = [c for c in df.columns
            if any(w in str(c) for w in want)][:10]
    date_cols = [c for c in df.columns if "日" in str(c)]
    if date_cols:
        df = df.sort_values(date_cols[-1], ascending=False)
    df = df.head(20)
    lines = [" | ".join(f"{c}:{r[c]}" for c in cols) for _, r in df.iterrows()]
    return f"## {ticker} 高管/重要股东增减持（最近{len(df)}条）:\n\n" + "\n".join(lines)
