# -*- coding: utf-8 -*-
"""
Tushare 特色数据增强（15000积分档/中转站可用接口）

把 Tushare 的 A股特色数据注入现有分析工具的返回文本，丰富各智能体的信息源:
- trading_extras(): 资金流向/龙虎榜/涨停榜/融资融券/筹码分布 -> 附加到个股新闻报告
- company_extras(): 质押/解禁/回购/业绩预告/业绩快报/机构调研 -> 附加到基本面报告
- macro_snapshot(): CPI/PMI/GDP/Shibor 宏观快照 -> 附加到宏观快讯报告

每个子项独立 try/except，单项失败不影响其他；输出行数有上限以控制 token。
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("ashare_vendor")


def _dt(curr_date):
    return datetime.strptime(str(curr_date).replace("-", "")[:8], "%Y%m%d")


def _ymd(d):
    return d.strftime("%Y%m%d")


def _section(title, fn):
    """执行子项，返回 markdown 段落或 None"""
    try:
        body = fn()
        if body:
            return f"\n### {title}\n{body}"
    except Exception as e:
        logger.debug(f"[ashare] tushare 特色数据 {title} 失败: {e}")
    return None


def _rows_to_lines(df, cols, n=5, rename=None):
    """DataFrame 关键列 -> 紧凑文本行"""
    rename = rename or {}
    use = [c for c in cols if c in df.columns]
    lines = []
    for _, r in df.head(n).iterrows():
        parts = []
        for c in use:
            v = r[c]
            if v is None or (isinstance(v, float) and v != v):
                continue
            parts.append(f"{rename.get(c, c)}:{v}")
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def trading_extras(symbol, curr_date) -> str:
    """交易行为类特色数据（供新闻/消息面分析师）"""
    from . import ts_relay
    if not ts_relay.configured():
        return ""
    p = ts_relay.pro()
    tsc = ts_relay.ts_code(symbol)
    end = _ymd(_dt(curr_date))
    start20 = _ymd(_dt(curr_date) - timedelta(days=20))
    secs = []

    def moneyflow():
        df = p.moneyflow(ts_code=tsc, start_date=start20, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return _rows_to_lines(df, ["trade_date", "net_mf_amount", "buy_lg_amount",
                                   "sell_lg_amount", "buy_elg_amount"],
                              n=5, rename={"trade_date": "日期",
                                           "net_mf_amount": "净流入(万元)",
                                           "buy_lg_amount": "大单买入(万元)",
                                           "sell_lg_amount": "大单卖出(万元)",
                                           "buy_elg_amount": "特大单买入(万元)"})

    def top_list():
        df = p.top_list(ts_code=tsc, start_date=start20, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return _rows_to_lines(df, ["trade_date", "pct_change", "net_amount",
                                   "reason"],
                              n=5, rename={"trade_date": "日期", "pct_change": "涨跌幅%",
                                           "net_amount": "龙虎榜净买额(元)", "reason": "上榜原因"})

    def limit_list():
        df = p.limit_list_d(ts_code=tsc, start_date=start20, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return _rows_to_lines(df, ["trade_date", "limit", "fd_amount", "limit_times",
                                   "open_times"],
                              n=5, rename={"trade_date": "日期", "limit": "类型(U涨停D跌停)",
                                           "fd_amount": "封单金额(元)",
                                           "limit_times": "连板数", "open_times": "开板次数"})

    def margin():
        df = p.margin_detail(ts_code=tsc, start_date=start20, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return _rows_to_lines(df, ["trade_date", "rzye", "rzmre", "rqye"],
                              n=3, rename={"trade_date": "日期", "rzye": "融资余额(元)",
                                           "rzmre": "融资买入(元)", "rqye": "融券余额(元)"})

    def chips():
        df = p.cyq_perf(ts_code=tsc, start_date=start20, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
        return _rows_to_lines(df, ["trade_date", "weight_avg", "winner_rate",
                                   "cost_50pct"],
                              n=3, rename={"trade_date": "日期", "weight_avg": "加权平均成本",
                                           "winner_rate": "胜率(获利盘%)",
                                           "cost_50pct": "50分位成本"})

    for title, fn in (("资金流向(近5日)", moneyflow), ("龙虎榜(近20日)", top_list),
                      ("涨停/跌停记录(近20日)", limit_list), ("融资融券", margin),
                      ("筹码分布", chips)):
        s = _section(title, fn)
        if s:
            secs.append(s)
    if not secs:
        return ""
    return ("\n\n---\n## A股特色数据 (Tushare, 截至 " + str(curr_date) + ")"
            + "".join(secs))


def company_extras(symbol, curr_date) -> str:
    """公司事件类特色数据（供基本面分析师）"""
    from . import ts_relay
    if not ts_relay.configured():
        return ""
    p = ts_relay.pro()
    tsc = ts_relay.ts_code(symbol)
    end = _ymd(_dt(curr_date))
    start180 = _ymd(_dt(curr_date) - timedelta(days=180))
    secs = []

    def pledge():
        df = p.pledge_stat(ts_code=tsc)
        if df is None or df.empty:
            return None
        df = df.sort_values("end_date", ascending=False)
        return _rows_to_lines(df, ["end_date", "pledge_count", "pledge_ratio"],
                              n=2, rename={"end_date": "截止日", "pledge_count": "质押笔数",
                                           "pledge_ratio": "质押比例%"})

    def share_float():
        df = p.share_float(ts_code=tsc, start_date=end,
                           end_date=_ymd(_dt(curr_date) + timedelta(days=365)))
        if df is None or df.empty:
            return None
        df = df.sort_values("float_date")
        return _rows_to_lines(df, ["float_date", "float_share", "float_ratio",
                                   "holder_name", "share_type"],
                              n=5, rename={"float_date": "解禁日", "float_share": "解禁股数",
                                           "float_ratio": "占总股本%",
                                           "holder_name": "股东", "share_type": "类型"})

    def repurchase():
        df = p.repurchase(ts_code=tsc, start_date=start180, end_date=end)
        if df is None or df.empty:
            return None
        if "ts_code" in df.columns:
            df = df[df["ts_code"] == tsc]
        if df.empty:
            return None
        return _rows_to_lines(df.sort_values("ann_date", ascending=False),
                              ["ann_date", "proc", "vol", "amount", "high_limit"],
                              n=3, rename={"ann_date": "公告日", "proc": "进度",
                                           "vol": "回购数量", "amount": "回购金额",
                                           "high_limit": "价格上限"})

    def forecast():
        df = p.forecast(ts_code=tsc, fields="ann_date,end_date,type,p_change_min,"
                                            "p_change_max,net_profit_min,net_profit_max,summary")
        if df is None or df.empty:
            return None
        df = df[df["ann_date"].astype(str) <= end].sort_values("ann_date", ascending=False)
        return _rows_to_lines(df, ["ann_date", "end_date", "type", "p_change_min",
                                   "p_change_max", "summary"],
                              n=2, rename={"ann_date": "公告日", "end_date": "报告期",
                                           "type": "类型", "p_change_min": "净利变动下限%",
                                           "p_change_max": "净利变动上限%", "summary": "摘要"})

    def express():
        df = p.express(ts_code=tsc,
                       fields="ann_date,end_date,revenue,n_income,yoy_net_profit")
        if df is None or df.empty:
            return None
        df = df[df["ann_date"].astype(str) <= end].sort_values("ann_date", ascending=False)
        return _rows_to_lines(df, ["ann_date", "end_date", "revenue", "n_income",
                                   "yoy_net_profit"],
                              n=2, rename={"ann_date": "公告日", "end_date": "报告期",
                                           "revenue": "营收(元)", "n_income": "净利润(元)",
                                           "yoy_net_profit": "净利同比%"})

    def survey():
        df = p.stk_surv(ts_code=tsc, start_date=start180, end_date=end)
        if df is None or df.empty:
            return None
        df = df.sort_values("surv_date", ascending=False)
        return _rows_to_lines(df, ["surv_date", "fund_visitors", "org_type", "content"],
                              n=3, rename={"surv_date": "调研日", "fund_visitors": "参与机构",
                                           "org_type": "机构类型", "content": "内容摘要"})

    for title, fn in (("股权质押", pledge), ("未来一年限售解禁", share_float),
                      ("股份回购(近180日)", repurchase), ("业绩预告", forecast),
                      ("业绩快报", express), ("机构调研(近180日)", survey)):
        s = _section(title, fn)
        if s:
            secs.append(s)
    if not secs:
        return ""
    return ("\n\n---\n## 公司事件数据 (Tushare, 截至 " + str(curr_date) + ")"
            + "".join(secs))


_REGIME_INDICES = [("000001.SH", "SSE Composite 上证指数"),
                   ("399001.SZ", "SZSE Component 深成指"),
                   ("000300.SH", "CSI 300 沪深300"),
                   ("399006.SZ", "ChiNext 创业板指"),
                   ("000688.SH", "STAR 50 科创50")]


def market_regime(curr_date) -> str:
    """市场风格量化快照（英文，注入 instrument_context 供智能体推理）。
    数据驱动地告诉模型当前哪个板块在主导市场（如科技/双创吸血行情），
    避免模型套用历史均值回归经验做出脱离当前风格的判断。"""
    from . import ts_relay
    if not ts_relay.configured():
        return ""
    p = ts_relay.pro()
    end = _ymd(_dt(curr_date))
    start = _ymd(_dt(curr_date) - timedelta(days=100))
    rows = []
    try:
        for code, label in _REGIME_INDICES:
            try:
                df = p.index_daily(ts_code=code, start_date=start, end_date=end)
                if df is None or len(df) < 21:
                    continue
                df = df.sort_values("trade_date")
                c = df["close"].astype(float)
                r20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
                r60 = (c.iloc[-1] / c.iloc[0] - 1) * 100
                rows.append((label, r20, r60))
            except Exception:
                continue
    except Exception:
        return ""
    if len(rows) < 3:
        return ""
    perf = "; ".join(f"{lb}: 20d {r20:+.1f}%, ~60d {r60:+.1f}%" for lb, r20, r60 in rows)
    spread = max(r[1] for r in rows) - min(r[1] for r in rows)
    leader = max(rows, key=lambda r: r[1])[0]
    concentration = ("HIGH style concentration — market breadth is narrow and "
                     f"leadership is concentrated in {leader}"
                     if spread > 5 else "moderate style dispersion")
    return (
        f" CURRENT MARKET REGIME (data as of {curr_date}): {perf}. "
        f"Regime read: {concentration}. "
        "Interpret valuation WITHIN this regime: in momentum-driven, "
        "concentrated markets (e.g. tech/AI-compute leadership draining flows "
        "from other sectors), elevated valuations can persist far longer than "
        "historical mean-reversion priors suggest, and being out of the "
        "leading theme often underperforms. Weigh relative strength, theme "
        "crowding, fund-flow direction and catalysts alongside valuation; do "
        "NOT recommend exit purely because valuation is high by historical "
        "standards — instead define concrete invalidation signals (trend "
        "break, volume exhaustion, policy shift).")


def macro_snapshot(curr_date) -> str:
    """宏观经济快照（供宏观新闻工具）"""
    from . import ts_relay
    if not ts_relay.configured():
        return ""
    p = ts_relay.pro()
    secs = []

    def cpi():
        df = p.cn_cpi()
        if df is None or df.empty:
            return None
        r = df.iloc[0]
        return f"{r.get('month', '')}: 全国CPI同比 {r.get('nt_yoy', 'N/A')}%, 环比 {r.get('nt_mom', 'N/A')}%"

    def pmi():
        df = p.cn_pmi()
        if df is None or df.empty:
            return None
        r = df.iloc[0]
        val = r.get("pmi010000", r.get("pmi", "N/A"))
        return f"{r.get('month', '')}: 制造业PMI {val}"

    def gdp():
        df = p.cn_gdp()
        if df is None or df.empty:
            return None
        r = df.iloc[0]
        return f"{r.get('quarter', '')}: GDP同比 {r.get('gdp_yoy', 'N/A')}%"

    def shibor():
        df = p.shibor(start_date=_ymd(_dt(curr_date) - timedelta(days=7)),
                      end_date=_ymd(_dt(curr_date)))
        if df is None or df.empty:
            return None
        r = df.sort_values("date").iloc[-1]
        return f"{r.get('date', '')}: 隔夜 {r.get('on', 'N/A')}%, 1周 {r.get('1w', 'N/A')}%, 1年 {r.get('1y', 'N/A')}%"

    for title, fn in (("CPI", cpi), ("PMI", pmi), ("GDP", gdp), ("Shibor利率", shibor)):
        s = _section(title, fn)
        if s:
            secs.append(s)
    if not secs:
        return ""
    return "\n\n---\n## 宏观经济快照 (Tushare)" + "".join(secs)
