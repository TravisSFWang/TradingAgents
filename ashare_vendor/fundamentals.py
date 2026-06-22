# -*- coding: utf-8 -*-
"""
A股基本面/财务报表工具的 ashare 实现（数据源: AKShare）
- get_fundamentals: 公司概况 + 估值 + 关键财务指标
- get_balance_sheet / get_income_statement / get_cashflow: 新浪财经三大报表
"""

import logging
from datetime import datetime

import pandas as pd

from .symbols import parse, reject_if_not_a_share

logger = logging.getLogger("ashare_vendor")

# 三大报表关键科目（新浪接口列名，模糊匹配）
_KEY_ITEMS = {
    "资产负债表": ["货币资金", "应收账款", "存货", "流动资产合计", "固定资产",
              "资产总计", "短期借款", "应付账款", "流动负债合计", "长期借款",
              "负债合计", "未分配利润", "所有者权益"],
    "利润表": ["营业总收入", "营业收入", "营业成本", "销售费用", "管理费用",
            "研发费用", "财务费用", "营业利润", "利润总额", "净利润",
            "归属于母公司所有者的净利润", "基本每股收益"],
    "现金流量表": ["经营活动产生的现金流量净额", "投资活动产生的现金流量净额",
              "筹资活动产生的现金流量净额", "现金及现金等价物净增加额",
              "期末现金及现金等价物余额"],
}


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1e8:
        return f"{f / 1e8:.2f}亿"
    if abs(f) >= 1e4:
        return f"{f / 1e4:.2f}万"
    return f"{f:.2f}"


def _company_extras_part(code, curr_date):
    """Tushare 公司事件段（质押/解禁/回购/预告/快报/调研），失败返回空"""
    try:
        from . import ts_relay
        if not ts_relay.configured():
            return []
        from .ts_extras import company_extras
        extra = company_extras(code, curr_date or datetime.now().strftime("%Y-%m-%d"))
        return [extra] if extra else []
    except Exception:
        return []


def get_fundamentals(ticker, curr_date=None):
    """公司概况 + 估值指标 + 财务摘要"""
    reject_if_not_a_share(ticker)
    _, code, _ = parse(ticker)
    import akshare as ak

    parts = [f"# A股 {ticker} 基本面概览" + (f" (分析日: {curr_date})" if curr_date else "")]

    from . import ts_relay
    ts_ok = ts_relay.configured()

    # 0. Tushare 优先（配置中转站时）: 公司信息 + 估值（支持回测日期）
    info_done = val_done0 = False
    if ts_ok:
        try:
            info = ts_relay.stock_basic(code)
            if info.get("name"):
                seg = ["\n## 公司信息 (Tushare)"]
                seg.append(f"- 股票简称: {info['name']}")
                if info.get("industry"):
                    seg.append(f"- 行业: {info['industry']}")
                if info.get("list_date"):
                    seg.append(f"- 上市日期: {info['list_date']}")
                parts.append("\n".join(seg))
                info_done = True
        except Exception as e:
            logger.warning(f"[ashare] tushare 公司信息失败 {code}: {e}")
        try:
            v = ts_relay.daily_basic(code, curr_date)
            if v:
                parts.append(
                    "\n## 估值指标 (Tushare, 截至 {})\n- PE(TTM): {}\n- PE(静): {}\n- PB: {}\n"
                    "- 股息率(TTM): {}%\n- 总市值: {}\n- 换手率: {}%".format(
                        v.get("trade_date", curr_date or "最新"),
                        v.get("pe_ttm", "N/A"), v.get("pe", "N/A"), v.get("pb", "N/A"),
                        v.get("dv_ttm", "N/A"),
                        _fmt_num(v["total_mv"]) if v.get("total_mv") else "N/A",
                        v.get("turnover_rate", "N/A")))
                val_done0 = True
        except Exception as e:
            logger.warning(f"[ashare] tushare 估值失败 {code}: {e}")

    # 1. 公司基本信息（Tushare 已成功则跳过；AKShare 默认禁用 -> 东财直连）
    from .symbols import use_akshare
    if not info_done:
        if use_akshare():
            try:
                info = ak.stock_individual_info_em(symbol=code)
                kv = dict(zip(info["item"].astype(str), info["value"]))
                seg = ["\n## 公司信息"]
                for k in ("股票简称", "行业", "上市时间", "总市值", "流通市值", "总股本", "流通股"):
                    if k in kv:
                        v = kv[k]
                        seg.append(f"- {k}: {_fmt_num(v) if k in ('总市值', '流通市值', '总股本', '流通股') else v}")
                parts.append("\n".join(seg))
                info_done = True
            except Exception as e:
                logger.warning(f"[ashare] akshare 公司信息失败 {code}: {e}")
        if not info_done:
            try:
                from . import emdirect
                q = emdirect.quote(code)
                seg = ["\n## 公司信息 (东方财富直连)"]
                if q.get("name"):
                    seg.append(f"- 股票简称: {q['name']}")
                if q.get("industry"):
                    seg.append(f"- 行业: {q['industry']}")
                if q.get("total_mv"):
                    seg.append(f"- 总市值: {_fmt_num(q['total_mv'])}")
                if len(seg) > 1:
                    parts.append("\n".join(seg))
            except Exception as e2:
                parts.append(f"\n## 公司信息: 暂不可用({e2})")

    # 2. 估值指标: Tushare已成功则跳过；否则乐咕历史序列 -> 东财直连快照
    val_done = val_done0
    if not val_done and use_akshare() and hasattr(ak, "stock_a_indicator_lg"):
        try:
            val = ak.stock_a_indicator_lg(symbol=code)
            val["trade_date"] = pd.to_datetime(val["trade_date"])
            if curr_date:
                val = val[val["trade_date"] <= pd.Timestamp(str(curr_date))]
            row = val.iloc[-1]
            parts.append(
                "\n## 估值指标 (截至 {})\n- PE(TTM): {}\n- PE(静): {}\n- PB: {}\n- 股息率(TTM): {}%\n- 总市值: {}".format(
                    row["trade_date"].strftime("%Y-%m-%d"),
                    round(row.get("pe_ttm", float("nan")), 2),
                    round(row.get("pe", float("nan")), 2),
                    round(row.get("pb", float("nan")), 2),
                    round(row.get("dv_ttm", float("nan")), 2),
                    _fmt_num(row.get("total_mv", "")) if "total_mv" in row else "N/A",
                ))
            val_done = True
        except Exception as e:
            logger.warning(f"[ashare] 乐咕估值指标失败 {code}: {e}")
    if not val_done:
        try:
            from . import emdirect
            q = emdirect.quote(code)
            if q:
                parts.append(
                    "\n## 估值指标 (东方财富实时快照，注意: 为当前值而非分析日历史值)\n"
                    f"- PE(TTM): {q.get('pe_ttm', 'N/A')}\n"
                    f"- PE(动): {q.get('pe', 'N/A')}\n"
                    f"- PB: {q.get('pb', 'N/A')}\n"
                    f"- 总市值: {_fmt_num(q['total_mv']) if q.get('total_mv') else 'N/A'}")
                val_done = True
        except Exception as e:
            logger.warning(f"[ashare] 东财直连估值失败 {code}: {e}")
    if not val_done:
        parts.append("\n## 估值指标: 暂不可用（数据源故障），分析时请勿凭记忆估算 PE/PB")

    # 3. 关键财务指标: Tushare fina_indicator 优先
    fin_done = False
    if ts_ok:
        try:
            fi = ts_relay.pro().fina_indicator(
                ts_code=ts_relay.ts_code(code),
                fields="end_date,eps,bps,roe,grossprofit_margin,netprofit_margin,"
                       "debt_to_assets,ocfps,netprofit_yoy,tr_yoy")
            if fi is not None and not fi.empty:
                fi = fi.drop_duplicates(subset="end_date", keep="first")
                fi["end_date"] = fi["end_date"].astype(str)
                if curr_date:
                    fi = fi[fi["end_date"] <= str(curr_date).replace("-", "")]
                fi = fi.sort_values("end_date", ascending=False).head(4)
                labels = [("eps", "每股收益"), ("bps", "每股净资产"), ("roe", "ROE%"),
                          ("grossprofit_margin", "毛利率%"), ("netprofit_margin", "净利率%"),
                          ("debt_to_assets", "资产负债率%"), ("ocfps", "每股经营现金流"),
                          ("netprofit_yoy", "净利同比%"), ("tr_yoy", "营收同比%")]
                seg = ["\n## 关键财务指标 (Tushare, 最近" + str(len(fi)) + "期: "
                       + ", ".join(fi["end_date"]) + ")"]
                for f, lb in labels:
                    if f in fi.columns:
                        vals = " / ".join("—" if (v is None or v != v) else f"{v}"
                                          for v in fi[f])
                        seg.append(f"- {lb}: {vals}")
                parts.append("\n".join(seg))
                fin_done = True
        except Exception as e:
            logger.warning(f"[ashare] tushare 财务指标失败 {code}: {e}")

    # 3b. AKShare 财务摘要（仅在启用且 Tushare 失败时）
    if fin_done or not use_akshare():
        return "\n".join(parts + _company_extras_part(code, curr_date))
    try:
        fin = ak.stock_financial_abstract(symbol=code)
        # 行: 指标, 列: 报告期(yyyymmdd)。取最近4个报告期
        period_cols = [c for c in fin.columns if str(c).isdigit()][:4]
        keep = ["归母净利润", "营业总收入", "净资产收益率(ROE)", "销售毛利率",
                "资产负债率", "基本每股收益", "每股净资产", "经营现金流量净额"]
        seg = ["\n## 关键财务指标（最近4个报告期: " + ", ".join(period_cols) + "）"]
        sel_col = "指标" if "指标" in fin.columns else fin.columns[1]
        for k in keep:
            m = fin[fin[sel_col].astype(str).str.contains(k.split("(")[0], na=False)]
            if not m.empty:
                vals = " / ".join(_fmt_num(m.iloc[0][c]) for c in period_cols)
                seg.append(f"- {k}: {vals}")
        parts.append("\n".join(seg))
    except Exception as e:
        logger.warning(f"[ashare] 财务摘要失败 {code}: {e}")
        parts.append(f"\n## 关键财务指标: 获取失败({e})")

    # Tushare 公司事件: 质押/解禁/回购/业绩预告快报/机构调研
    if ts_ok:
        try:
            from .ts_extras import company_extras
            extra = company_extras(code, curr_date or datetime.now().strftime("%Y-%m-%d"))
            if extra:
                parts.append(extra)
        except Exception as e:
            logger.debug(f"[ashare] 公司事件数据失败 {code}: {e}")

    return "\n".join(parts)


# Tushare 三大报表接口及关键科目（字段名 -> 中文标签）
_TS_REPORTS = {
    "资产负债表": ("balancesheet", [
        ("money_cap", "货币资金"), ("accounts_receiv", "应收账款"),
        ("inventories", "存货"), ("total_cur_assets", "流动资产合计"),
        ("fix_assets", "固定资产"), ("total_assets", "资产总计"),
        ("st_borr", "短期借款"), ("acct_payable", "应付账款"),
        ("total_cur_liab", "流动负债合计"), ("lt_borr", "长期借款"),
        ("total_liab", "负债合计"),
        ("total_hldr_eqy_exc_min_int", "归母所有者权益合计")]),
    "利润表": ("income", [
        ("total_revenue", "营业总收入"), ("revenue", "营业收入"),
        ("oper_cost", "营业成本"), ("sell_exp", "销售费用"),
        ("admin_exp", "管理费用"), ("rd_exp", "研发费用"),
        ("fin_exp", "财务费用"), ("operate_profit", "营业利润"),
        ("total_profit", "利润总额"), ("n_income", "净利润"),
        ("n_income_attr_p", "归属于母公司所有者的净利润"),
        ("basic_eps", "基本每股收益")]),
    "现金流量表": ("cashflow", [
        ("n_cashflow_act", "经营活动产生的现金流量净额"),
        ("n_cashflow_inv_act", "投资活动产生的现金流量净额"),
        ("n_cash_flows_fnc_act", "筹资活动产生的现金流量净额"),
        ("n_incr_cash_cash_equ", "现金及现金等价物净增加额"),
        ("c_cash_equ_end_period", "期末现金及现金等价物余额")]),
}


def _tushare_report(ticker, table, freq, curr_date, periods=3):
    """用 Tushare 取三大报表；失败返回 None 交由新浪链路处理"""
    from . import ts_relay
    if not ts_relay.configured():
        return None
    api_name, fields = _TS_REPORTS[table]
    try:
        kw = {"ts_code": ts_relay.ts_code(ticker),
              "fields": "end_date," + ",".join(f for f, _ in fields)}
        if curr_date:
            kw["end_date"] = str(curr_date).replace("-", "")
        df = getattr(ts_relay.pro(), api_name)(**kw)
        if df is None or df.empty:
            return None
        df = df.drop_duplicates(subset="end_date", keep="first")
        df["end_date"] = df["end_date"].astype(str)
        if curr_date:
            df = df[df["end_date"] <= str(curr_date).replace("-", "")]
        if str(freq).lower() == "annual":
            df = df[df["end_date"].str.endswith("1231")]
        df = df.sort_values("end_date", ascending=False).head(periods)
        if df.empty:
            return None
        lines = [f"## {ticker} {table} (Tushare, "
                 f"{'年报' if str(freq).lower() == 'annual' else '季报'}, "
                 f"最近{len(df)}期, 单位:元)"]
        for _, row in df.iterrows():
            p = row["end_date"]
            seg = [f"\n### 报告期 {p[:4]}-{p[4:6]}-{p[6:]}"]
            for f, label in fields:
                v = row.get(f)
                if v is not None and not (isinstance(v, float) and v != v):
                    seg.append(f"- {label}: {_fmt_num(v)}")
            lines.append("\n".join(seg))
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[ashare] tushare {table}失败 {ticker}: {e}")
        return None


def _financial_report(ticker, table, freq="quarterly", curr_date=None, periods=3):
    """三大报表: Tushare(优先) -> 新浪财经(回退)，返回最近 N 期关键科目文本"""
    reject_if_not_a_share(ticker)
    _, code, mkt = parse(ticker)
    import akshare as ak

    ts_result = _tushare_report(ticker, table, freq, curr_date, periods)
    if ts_result:
        return ts_result

    from .symbols import use_akshare
    if not use_akshare():
        return f"## {ticker} {table}暂不可用（Tushare 失败，AKShare 已禁用）"

    try:
        raw = ak.stock_financial_report_sina(stock=f"{mkt}{code}", symbol=table)
    except Exception as e:
        logger.warning(f"[ashare] {table}获取失败 {code}: {e}")
        return f"## {ticker} {table}暂不可用: {e}"
    if raw is None or raw.empty:
        return f"## {ticker} 无{table}数据"

    df = raw.copy()
    date_col = "报告日" if "报告日" in df.columns else df.columns[0]
    df[date_col] = df[date_col].astype(str)
    if str(freq).lower() == "annual":
        df = df[df[date_col].str.endswith("1231")]
    if curr_date:
        cut = str(curr_date).replace("-", "")
        df = df[df[date_col] <= cut]
    df = df.sort_values(date_col, ascending=False).head(periods)
    if df.empty:
        return f"## {ticker} 在指定日期前无{table}数据"

    keys = _KEY_ITEMS[table]
    lines = [f"## {ticker} {table} ({'年报' if str(freq).lower() == 'annual' else '季报'}, 最近{len(df)}期, 单位:元)"]
    for _, row in df.iterrows():
        period = row[date_col]
        seg = [f"\n### 报告期 {period[:4]}-{period[4:6]}-{period[6:]}"]
        used = set()
        for k in keys:
            for col in df.columns:
                if k in str(col) and col not in used:
                    used.add(col)
                    seg.append(f"- {col}: {_fmt_num(row[col])}")
                    break
        lines.append("\n".join(seg))
    return "\n".join(lines)


def get_balance_sheet(ticker, freq="quarterly", curr_date=None):
    return _financial_report(ticker, "资产负债表", freq, curr_date)


def get_income_statement(ticker, freq="quarterly", curr_date=None):
    return _financial_report(ticker, "利润表", freq, curr_date)


def get_cashflow(ticker, freq="quarterly", curr_date=None):
    return _financial_report(ticker, "现金流量表", freq, curr_date)
