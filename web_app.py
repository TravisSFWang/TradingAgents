# -*- coding: utf-8 -*-
"""
TradingAgents A股本地 Web 界面 v2 (Streamlit)
- 中英对照标签（避免浏览器自动翻译损坏术语；建议对 localhost 关闭自动翻译）
- 实时进度: 显示当前运行到哪个智能体
- Token 用量与费用统计 (DeepSeek 计价)

放在官方 TradingAgents 仓库根目录运行:
    python -m streamlit run web_app.py
"""

import os
import sys
import threading
import time
import traceback
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

st.set_page_config(page_title="TradingAgents A股 / A-Share", page_icon="📈", layout="wide")

# ---------------- 流水线定义（节点名 -> 中英标签 + 阶段序号） ----------------

PIPELINE = [
    ("Market Analyst",       "技术面分析师 Market Analyst",        0),
    ("Sentiment Analyst",    "情绪分析师 Sentiment Analyst",       0),
    ("News Analyst",         "新闻分析师 News Analyst",            0),
    ("Fundamentals Analyst", "基本面分析师 Fundamentals Analyst",  0),
    ("Bull Researcher",      "看多研究员 Bull Researcher",         1),
    ("Bear Researcher",      "看空研究员 Bear Researcher",         1),
    ("Research Manager",     "研究经理 Research Manager",          2),
    ("Trader",               "交易员 Trader",                      3),
    ("Aggressive Analyst",   "激进风控 Aggressive",                4),
    ("Neutral Analyst",      "中性风控 Neutral",                   4),
    ("Conservative Analyst", "保守风控 Conservative",              4),
    ("Portfolio Manager",    "组合经理 Portfolio Manager",         5),
]
NODE_LABEL = {n: l for n, l, _ in PIPELINE}
NODE_STAGE = {n: s for n, _, s in PIPELINE}
STAGE_NAMES = ["① 分析师收集数据 Analysts", "② 多空辩论 Debate", "③ 研究结论 Research Manager",
               "④ 交易计划 Trader", "⑤ 风控评估 Risk", "⑥ 最终决策 Portfolio Manager"]

REPORT_SECTIONS = [
    ("market_report",       "📊 技术面 Market"),
    ("sentiment_report",    "💬 情绪面 Sentiment"),
    ("news_report",         "📰 新闻面 News"),
    ("fundamentals_report", "📚 基本面 Fundamentals"),
]

ANALYST_OPTS = {"market": "技术面 Market", "social": "社交情绪 Social",
                "news": "新闻 News", "fundamentals": "基本面 Fundamentals"}

# 基准指数选项（标签 -> benchmark_ticker 或 None=自动）
BENCHMARK_OPTS = {
    "自动 Auto":          None,
    "上证综指 000001.SS": "000001.SS",
    "深证成指 399001.SZ": "399001.SZ",
    "沪深300 000300.SH":  "000300.SH",
    "创业板指 399006.SZ": "399006.SZ",
    "科创50  000688.SH":  "000688.SH",
    "标普500 SPY":        "SPY",
}


@st.cache_data(show_spinner=False)
def resolve_stock_name(code: str) -> str:
    """代码 -> 中文简称（A股）；非A股或解析失败返回空串。带缓存，复用身份解析链。"""
    code = (code or "").strip()
    if not code:
        return ""
    try:
        from ashare_vendor.symbols import parse
        ok, _, _ = parse(code)
        if not ok:
            return ""
        from ashare_vendor.context_patch import _resolve_ashare_identity
        return str((_resolve_ashare_identity(code) or {}).get("company_name") or "")
    except Exception:
        return ""


def code_with_name(code: str) -> str:
    """'600172' -> '600172 黄河旋风'；无名称时回退原代码。"""
    name = resolve_stock_name(code)
    return f"{code} {name}" if name else str(code)


def resolve_benchmark_for_ui(ticker: str, label: str) -> "str | None":
    """将侧边栏选择映射为 benchmark_ticker 字符串（或 None 表示让官方逻辑处理）。

    "自动 Auto" 时按 A 股板块规则选最匹配的基准指数：
      科创板(688/689) → 科创50(000688.SH)
      创业板(300/301) → 创业板指(399006.SZ)
      深交所主板/中小板(0开头) → 深证成指(399001.SZ)
      上交所主板(6开头) → 上证综指(000001.SS)
    非 A 股返回 None，由 `_resolve_benchmark` 的后缀映射处理（US → SPY 等）。
    定义在侧边栏之前——侧边栏在模块加载时即调用它（caption 展示用）。
    """
    explicit = BENCHMARK_OPTS.get(label)
    if explicit is not None:  # 用户手动选了具体指数
        return explicit
    # "自动"：尝试 A 股板块感知
    try:
        from ashare_vendor.symbols import parse
        ok, code, mkt = parse(ticker)
        if ok:
            if code.startswith(("688", "689")):
                return "000688.SH"   # 科创50
            if code.startswith(("300", "301")):
                return "399006.SZ"  # 创业板指
            if mkt == "sz":
                return "399001.SZ"  # 深证成指
            return "000001.SS"      # 上证综指（SH 主板兜底）
    except Exception:
        pass
    return None  # 非 A 股：留给官方后缀映射

# ---------------- 侧边栏 ----------------

with st.sidebar:
    st.title("📈 TradingAgents A股")
    ticker = st.text_input("股票代码 / Ticker", value="600519",
                           help="A股6位代码(600519)；也支持美股(AAPL)")
    trade_date = st.date_input("分析日期 / Trade Date", value=date.today(),
                               max_value=date.today())
    st.divider()
    _env_deep = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", "deepseek-v4-pro")
    _env_quick = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-flash")
    _known = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner", "deepseek-chat"]
    _deep_opts = [_env_deep] + [m for m in _known if m != _env_deep]
    _quick_opts = [_env_quick] + [m for m in _known if m != _env_quick]
    deep_model = st.selectbox("深度思考模型 / Deep-think LLM", _deep_opts, index=0)
    quick_model = st.selectbox("快速思考模型 / Quick-think LLM", _quick_opts, index=0)
    debate_rounds = st.slider("辩论轮数 / Debate Rounds", 1, 3, 1)
    analysts = st.multiselect("启用的分析师 / Analysts",
                              options=list(ANALYST_OPTS),
                              default=["market", "news", "fundamentals"],
                              format_func=lambda x: ANALYST_OPTS[x],
                              help="A股建议关闭社交情绪（Reddit等以美股为主）")
    benchmark_label = st.selectbox(
        "结算基准 / Benchmark",
        options=list(BENCHMARK_OPTS),
        index=0,
        help=("自动: A股按板块选指数（科创→科创50, 创业板→创业板指, SH→上证, SZ→深证）；"
              "美股默认 SPY。可手动覆盖。"),
    )
    st.divider()
    key_ok = bool(os.getenv("DEEPSEEK_API_KEY"))
    st.caption("✅ DeepSeek API Key 已配置" if key_ok
               else "❌ 未检测到 DEEPSEEK_API_KEY（请配置 .env）")
    st.caption(f"📂 通达信 TDX: {os.getenv('TDX_VIPDOC_PATH', '') or '未配置'}")
    if os.getenv("TUSHARE_TOKEN", "").strip() and not os.getenv("TUSHARE_TOKEN", "").startswith("your_"):
        st.caption("✅ Tushare Token 已配置")
    run_btn = st.button("🚀 开始分析 / Run", type="primary",
                        width="stretch", disabled=not key_ok)
    # 展示实际将使用的基准（"自动"时按板块解析后显示）
    _effective_bm = resolve_benchmark_for_ui(ticker.strip(), benchmark_label)
    st.caption(f"📐 结算基准: {_effective_bm or '由后缀映射决定'}")
    st.caption("提示: 若页面术语显示异常，请关闭浏览器对 localhost 的自动翻译"
               "（地址栏右侧翻译图标 → 永不翻译此网站）。")

# ---------------- K线图 ----------------

CHART_PERIOD_OPTS = {
    "日线 Daily": "daily",
    "周线 Weekly": "weekly",
    "月线 Monthly": "monthly",
    "60分钟 60min": "60min",
    "30分钟 30min": "30min",
    "15分钟 15min": "15min",
}

CHART_INDICATOR_OPTS = {
    "均线 MA(5/10/20/30/60/120/240/360)": "ma",
    "布林带 BOLL(20,2)": "boll",
    "量价均线 VWMA(20)": "vwma",
    "MACD(12,26,9)": "macd",
    "RSI(14)": "rsi",
}

_MA_COLORS = ["#888888", "#1f77b4", "#ff7f0e", "#2ca02c",
             "#d62728", "#9467bd", "#8c564b", "#17becf"]


@st.cache_data(ttl=600, show_spinner=False)
def load_period_kline(symbol: str, period: str, end: str):
    from ashare_vendor.chart_data import get_period_df
    return get_period_df(symbol, period, end)


def render_kline(symbol: str, end: str):
    c1, c2 = st.columns([1, 2])
    with c1:
        period_label = st.selectbox("周期 / Period", list(CHART_PERIOD_OPTS),
                                    key="kline_period_select")
    with c2:
        indicator_labels = st.multiselect("叠加指标 / Indicators",
                                          list(CHART_INDICATOR_OPTS),
                                          default=["均线 MA(5/10/20/30/60/120/240/360)"],
                                          key="kline_indicator_select")
    period = CHART_PERIOD_OPTS[period_label]
    selected = {CHART_INDICATOR_OPTS[l] for l in indicator_labels}

    try:
        from ashare_vendor.chart_data import (compute_indicators, daily_rangebreaks,
                                              minute_rangebreaks)

        df, source = load_period_kline(symbol, period, end)
        if df is None or df.empty:
            msg = ("暂无行情数据 / No price data" if period == "daily" else
                  "该周期暂无数据(可能为非A股或本地无分钟线) / No data for this period")
            st.info(msg)
            return

        is_minute = period in ("15min", "30min", "60min")
        x = df["datetime"] if is_minute else df["date"]
        ind = compute_indicators(df, selected)
        show_macd, show_rsi = "macd" in selected, "rsi" in selected

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        rows = 1 + int(show_macd) + int(show_rsi)
        row_heights = [0.6] + [0.4 / (rows - 1)] * (rows - 1) if rows > 1 else [1.0]
        fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                            vertical_spacing=0.03, row_heights=row_heights)

        fig.add_trace(go.Candlestick(
            x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            increasing_line_color="#ef232a", decreasing_line_color="#14b143", name="K线"),
            row=1, col=1)

        ma_keys = [k for k in ind if k.startswith("sma")]
        ma_keys.sort(key=lambda k: int(k[3:]))  # sma5, sma10, sma20, ... 按周期升序
        for color, key in zip(_MA_COLORS, ma_keys):
            fig.add_trace(go.Scatter(x=x, y=ind[key], name=key.replace("sma", "MA"),
                                     line=dict(width=1, color=color)), row=1, col=1)
        if "boll_mid" in ind:
            fig.add_trace(go.Scatter(x=x, y=ind["boll_ub"], name="BOLL upper",
                                     line=dict(width=1, dash="dot")), row=1, col=1)
            fig.add_trace(go.Scatter(x=x, y=ind["boll_mid"], name="BOLL mid",
                                     line=dict(width=1, dash="dot")), row=1, col=1)
            fig.add_trace(go.Scatter(x=x, y=ind["boll_lb"], name="BOLL lower",
                                     line=dict(width=1, dash="dot")), row=1, col=1)
        if "vwma20" in ind:
            fig.add_trace(go.Scatter(x=x, y=ind["vwma20"], name="VWMA20",
                                     line=dict(width=1, dash="dash")), row=1, col=1)

        next_row = 2
        if show_macd:
            fig.add_trace(go.Bar(x=x, y=ind["macd_hist"], name="MACD hist"), row=next_row, col=1)
            fig.add_trace(go.Scatter(x=x, y=ind["macd"], name="MACD"), row=next_row, col=1)
            fig.add_trace(go.Scatter(x=x, y=ind["macd_signal"], name="Signal"), row=next_row, col=1)
            next_row += 1
        if show_rsi:
            fig.add_trace(go.Scatter(x=x, y=ind["rsi14"], name="RSI14"), row=next_row, col=1)
            fig.add_hline(y=70, line_dash="dot", line_color="gray", row=next_row, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color="gray", row=next_row, col=1)

        rangebreaks = (minute_rangebreaks() if is_minute else
                      daily_rangebreaks(df["date"]) if period == "daily" else [])
        if rangebreaks:
            fig.update_xaxes(rangebreaks=rangebreaks)

        st.caption(f"{code_with_name(symbol)} {period_label} (来源 source: {source})")
        fig.update_layout(
            height=400 + 130 * (rows - 1), xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=120, t=20, b=10),
            legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02))
        st.plotly_chart(fig, width="stretch")
    except Exception as e:
        st.warning(f"K线绘制失败 / Chart failed: {e}")

# ---------------- 分析执行 ----------------

def run_analysis(symbol, day, analysts_sel, deep, quick, rounds, benchmark, tracker, holder):
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        config.update({
            "llm_provider": "deepseek",
            "deep_think_llm": deep,
            "quick_think_llm": quick,
            "max_debate_rounds": rounds,
            "max_risk_discuss_rounds": rounds,
            "output_language": "Chinese (Simplified) 简体中文",
        })
        if benchmark is not None:
            config["benchmark_ticker"] = benchmark
        ta = TradingAgentsGraph(selected_analysts=analysts_sel, debug=False,
                                config=config, callbacks=[tracker])
        final_state, decision = ta.propagate(symbol, day)
        holder["state"] = final_state
        holder["decision"] = decision
        tracker.save(ROOT / "usage_log.jsonl", ticker=symbol, trade_date=day)
    except Exception:
        holder["error"] = traceback.format_exc()


def render_progress(tracker, container):
    """根据 tracker 当前节点渲染流水线进度"""
    cur = tracker.current_node
    cur_stage = NODE_STAGE.get(cur, -1)
    lines = []
    for i, name in enumerate(STAGE_NAMES):
        if cur_stage > i or (cur_stage == -1 and tracker.node_history):
            icon = "✅" if cur_stage > i else "⬜"
        elif cur_stage == i:
            icon = "🔄"
        else:
            icon = "⬜"
        suffix = ""
        if cur_stage == i and cur in NODE_LABEL:
            suffix = f" ← **{NODE_LABEL[cur]}**"
        lines.append(f"{icon} {name}{suffix}")
    container.markdown("\n\n".join(lines))


def render_usage(tracker):
    t = tracker.totals()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("输入 Input tokens", f"{t['input_tokens']:,}")
    c2.metric("输出 Output tokens", f"{t['output_tokens']:,}")
    c3.metric("LLM 调用 Calls", t["calls"])
    c4.metric("费用 Cost (¥)", f"{t['cost_cny']:.4f}")
    rows = tracker.rows()
    if rows:
        import pandas as pd
        df = pd.DataFrame(rows, columns=["节点 Node", "模型 Model", "输入 In",
                                         "输出 Out", "次数 Calls", "费用 ¥"])
        df["节点 Node"] = df["节点 Node"].map(lambda n: NODE_LABEL.get(n, n))
        with st.expander("分节点用量明细 / Per-node breakdown"):
            st.dataframe(df, width="stretch", hide_index=True)


def build_full_markdown(symbol, day, decision, card_md, state, tracker):
    """拼接全篇报告（决策卡+决策+各分析师+辩论+风控+用量）供下载/打印"""
    sec = [f"# {symbol} 多智能体分析报告 — {day}", ""]
    if card_md:
        sec += [card_md, "", "---", ""]
    sec += ["## 🎯 最终交易决策 / Final Decision", "", str(decision), "", "---", ""]
    for key, label in REPORT_SECTIONS:
        v = state.get(key) if isinstance(state, dict) else None
        if v:
            sec += [f"## {label}", "", str(v), "", "---", ""]
    d = state.get("investment_debate_state", {}) or {}
    if d.get("judge_decision"):
        sec += ["## ⚖️ 研究经理结论 / Research Manager", "", str(d["judge_decision"]), ""]
    if d.get("history"):
        sec += ["<details><summary>多空辩论全过程</summary>", "", "```",
                str(d["history"]), "```", "</details>", ""]
    if state.get("trader_investment_plan"):
        sec += ["## 💼 交易员计划 / Trader Plan", "", str(state["trader_investment_plan"]), ""]
    r = state.get("risk_debate_state", {}) or {}
    if state.get("final_trade_decision"):
        sec += ["## 🛡️ 组合经理最终决议 / Portfolio Manager", "",
                str(state["final_trade_decision"]), ""]
    if r.get("history"):
        sec += ["<details><summary>风控辩论全过程</summary>", "", "```",
                str(r["history"]), "```", "</details>", ""]
    t = tracker.totals()
    sec += ["---", f"> 用量: {t['input_tokens']:,} in / {t['output_tokens']:,} out "
                   f"tokens · {t['calls']} calls · ¥{t['cost_cny']:.4f} · "
                   f"{t['elapsed_sec']}s · AI 生成，不构成投资建议"]
    return "\n".join(sec)


def md_to_print_html(md_text, title):
    """生成可直接 Ctrl+P 打印的 HTML"""
    try:
        import markdown as _md
        body = _md.markdown(md_text, extensions=["tables", "fenced_code"])
    except ImportError:
        import html as _h
        body = f"<pre style='white-space:pre-wrap'>{_h.escape(md_text)}</pre>"
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>"
            "<style>body{font-family:'Microsoft YaHei',sans-serif;max-width:900px;"
            "margin:24px auto;padding:0 16px;line-height:1.6}table{border-collapse:"
            "collapse}td,th{border:1px solid #999;padding:4px 10px}@media print"
            "{details{display:none}}</style></head><body>"
            f"{body}</body></html>")


def render_download(symbol, day, decision, card_md, state, tracker):
    full_md = build_full_markdown(symbol, day, decision, card_md, state, tracker)
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ 下载完整报告 (.md)", full_md,
                       file_name=f"{symbol}_{day}_report.md",
                       mime="text/markdown", width="stretch")
    c2.download_button("🖨️ 下载打印版 (.html，打开后 Ctrl+P)",
                       md_to_print_html(full_md, f"{symbol} {day}"),
                       file_name=f"{symbol}_{day}_report.html",
                       mime="text/html", width="stretch")


def render_results(state, decision):
    st.subheader("🎯 最终交易决策 / Final Decision")
    st.markdown(str(decision))
    tabs = st.tabs([label for _, label in REPORT_SECTIONS]
                   + ["⚖️ 辩论 Debate", "💼 交易计划 Plan", "🛡️ 风控 Risk"])
    for i, (key, _) in enumerate(REPORT_SECTIONS):
        with tabs[i]:
            st.markdown((state.get(key) if isinstance(state, dict) else None)
                        or "_该分析师未启用或无输出 / Not enabled_")
    with tabs[len(REPORT_SECTIONS)]:
        d = state.get("investment_debate_state", {}) or {}
        st.markdown("**研究经理结论 / Research Manager:**\n\n" + str(d.get("judge_decision", "_无_")))
        with st.expander("辩论全过程 / Full debate"):
            st.text(str(d.get("history", "")))
    with tabs[len(REPORT_SECTIONS) + 1]:
        st.markdown(str(state.get("trader_investment_plan") or "_无_"))
    with tabs[len(REPORT_SECTIONS) + 2]:
        r = state.get("risk_debate_state", {}) or {}
        st.markdown("**组合经理最终决议 / Portfolio Manager:**\n\n"
                    + str(state.get("final_trade_decision") or "_无_"))
        with st.expander("风控辩论全过程 / Full risk debate"):
            st.text(str(r.get("history", "")))

# ---------------- 历史记录 ----------------

@st.cache_data(ttl=30, show_spinner=False)
def load_history_entries():
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.agents.utils.memory import TradingMemoryLog
        log = TradingMemoryLog(DEFAULT_CONFIG)
        return log.load_entries()
    except Exception:
        return []


def load_snapshot(ticker: str, trade_date_str: str) -> dict:
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.dataflows.utils import safe_ticker_component
        safe = safe_ticker_component(ticker)
        p = (Path(DEFAULT_CONFIG["results_dir"]) / safe
             / "TradingAgentsStrategy_logs"
             / f"full_states_log_{trade_date_str}.json")
        if p.exists():
            import json as _json
            with open(p, encoding="utf-8") as f:
                return _json.load(f)
    except Exception:
        pass
    return {}


def render_history_tab():
    entries = load_history_entries()
    col_refresh, col_filter = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 刷新 Refresh", width="stretch"):
            load_history_entries.clear()
            st.rerun()
    with col_filter:
        tickers_in_log = sorted({e["ticker"] for e in entries}) if entries else []
        filter_ticker = st.selectbox(
            "筛选代码 Filter ticker",
            options=["全部 All"] + tickers_in_log,
            format_func=lambda c: c if c == "全部 All" else code_with_name(c),
            label_visibility="collapsed",
        )

    if not entries:
        st.info("暂无历史记录。完成一次分析后将在此显示结果。\n"
                "No history yet — run an analysis first.")
        return

    visible = entries if filter_ticker == "全部 All" else [
        e for e in entries if e["ticker"] == filter_ticker
    ]
    visible = list(reversed(visible))  # 最新在前

    # ── 统计摘要 ──
    settled = [e for e in visible if not e["pending"]]
    pending = [e for e in visible if e["pending"]]

    def _pct(s):
        try:
            return float(s.strip("%+")) / 100
        except Exception:
            return None

    alphas = [_pct(e["alpha"]) for e in settled if _pct(e["alpha"]) is not None]
    raws   = [_pct(e["raw"])   for e in settled if _pct(e["raw"])   is not None]
    wins   = sum(1 for a in alphas if a > 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总记录 Total",     len(visible))
    c2.metric("已结算 Settled",   len(settled))
    c3.metric("待结算 Pending",   len(pending))
    c4.metric("胜率 Win Rate",
              f"{wins/len(alphas):.0%}" if alphas else "—",
              help="Alpha > 0 计为胜 / alpha-positive count")
    c5.metric("平均超额 Avg α",
              f"{sum(alphas)/len(alphas):+.1%}" if alphas else "—")

    if settled and raws:
        st.caption(f"平均原始收益 Avg raw return: "
                   f"{sum(raws)/len(raws):+.1%}，"
                   f"基于 {len(settled)} 条已结算记录")
    st.divider()

    # ── 逐条展示 ──
    for e in visible:
        rating = e.get("rating") or "N/A"
        if e["pending"]:
            status = "⏳ 待结算"
            badge = ""
        else:
            raw_v = _pct(e["raw"])
            alpha_v = _pct(e["alpha"])
            raw_s = f"{raw_v:+.1%}" if raw_v is not None else e["raw"] or "—"
            alpha_s = f"{alpha_v:+.1%}" if alpha_v is not None else e["alpha"] or "—"
            color = "🟢" if (alpha_v or 0) > 0 else ("🔴" if (alpha_v or 0) < 0 else "⚪")
            holding = e.get("holding") or "—"
            status = f"{color} 原始 {raw_s} · 超额α {alpha_s} · {holding}"
            badge = f"  `{e['raw']}` raw"

        _nm = resolve_stock_name(e["ticker"])
        _code_disp = f"`{e['ticker']}` {_nm}" if _nm else f"`{e['ticker']}`"
        label = (f"**{e['date']}** &nbsp;|&nbsp; {_code_disp} &nbsp;|&nbsp; "
                 f"_{rating}_ &nbsp;|&nbsp; {status}")
        with st.expander(label, expanded=False):
            # 决策摘要
            decision_text = e.get("decision") or ""
            st.markdown("**决策摘要 Decision summary:**")
            st.markdown(decision_text[:600] + ("…" if len(decision_text) > 600 else ""))

            # 反思
            reflection_text = e.get("reflection") or ""
            if reflection_text:
                st.markdown("**反思 Reflection:**")
                st.markdown(reflection_text[:500] + ("…" if len(reflection_text) > 500 else ""))
            elif e["pending"]:
                st.caption("_结算后将显示反思内容（下次运行同代码时触发）_")

            # 完整报告（从 snapshot 加载）
            snap_key = f"snap_{e['ticker']}_{e['date']}"
            if st.button("📄 查看完整报告 Full report",
                         key=f"btn_{snap_key}", width="content"):
                st.session_state[snap_key] = load_snapshot(e["ticker"], e["date"])
            snap = st.session_state.get(snap_key)
            if snap:
                if not snap:
                    st.warning("找不到快照文件。快照保存在 results_dir，"
                               "请确认该次分析已完整运行。")
                else:
                    st.divider()
                    render_results(snap, snap.get("final_trade_decision") or "_无_")


# ---------------- 主区域 ----------------

tab_analysis, tab_history = st.tabs(["📊 分析 Analysis", "📋 历史记录 History"])

def render_library_status(ticker_in: str):
    """本地资料库状态面板：列出与当前代码相关的 library 资料，
    区分 已读取(摘要已缓存) / 新发现(下次分析时读取) / 已修改(将重读)。纯只读。"""
    try:
        from ashare_vendor.library import inspect_library
        info = inspect_library(ticker_in)
    except Exception as e:
        st.caption(f"library 状态读取失败: {e}")
        return
    items = info["items"]
    cached = [i for i in items if i["status"] == "cached"]
    fresh = [i for i in items if i["status"] != "cached"]
    label = (f"📚 本地资料库 Library — ✅ 已读取 {len(cached)} · 🆕 新发现 {len(fresh)}"
             if items else "📚 本地资料库 Library（暂无资料）")

    with st.expander(label, expanded=bool(fresh)):
        st.caption(f"目录: `{info['dir']}`　把研报/纪要(pdf/docx/md/txt)丢进去即可，"
                   "**无需重启**——下次点「开始分析」自动摘要并注入。"
                   "`<代码>\\`=个股绑定，`_macro\\`=宏观恒注入，根目录=按提及个股自动路由。")
        if not items:
            return

        STATUS_LABEL = {"cached": "✅ 已读取", "new": "🆕 新发现（待读取）",
                        "changed": "🔄 已修改（将重读）"}

        def _bind_label(b):
            if b == "_macro":
                return "宏观 _macro"
            return f"个股绑定 {b}" if b else "根目录(自动路由)"

        def _inject_label(i):
            if i["status"] != "cached":
                return "待读取后判定"
            return "✅ 会注入" if i["will_inject"] else "—（未提及本股）"

        import pandas as pd
        rows = [{
            "文件": i["file"],
            "位置": _bind_label(i["bind"]),
            "状态": STATUS_LABEL.get(i["status"], i["status"]),
            "对本股": _inject_label(i),
            "资料日期": i["native_date"] or "",
            "主题": (i["theme"] or "")[:40],
        } for i in items]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


with tab_analysis:
    st.header(f"{code_with_name(ticker.strip())} · {trade_date.isoformat()}")
    render_kline(ticker.strip(), trade_date.isoformat())
    render_library_status(ticker.strip())

    if run_btn:
        st.session_state.pop("results", None)  # 清掉上一轮结果，避免新分析失败时还显示旧结果
        from ashare_vendor.usage_tracker import UsageTracker
        tracker = UsageTracker()
        holder = {}
        _benchmark = resolve_benchmark_for_ui(ticker.strip(), benchmark_label)
        worker = threading.Thread(
            target=run_analysis,
            args=(ticker.strip(), trade_date.isoformat(), analysts, deep_model,
                  quick_model, debate_rounds, _benchmark, tracker, holder),
            daemon=True)
        worker.start()

        t0 = time.time()
        with st.status("多智能体分析进行中 / Running...", expanded=True) as status:
            prog = st.empty()
            meta = st.empty()
            while worker.is_alive():
                render_progress(tracker, prog)
                t = tracker.totals()
                meta.caption(f"已运行 {time.time() - t0:.0f}s · "
                             f"tokens {t['input_tokens'] + t['output_tokens']:,} · "
                             f"约 ¥{t['cost_cny']:.3f}")
                time.sleep(2)
            render_progress(tracker, prog)
            if "error" in holder:
                status.update(label="分析失败 / Failed", state="error")
                st.error("运行出错 / Error:")
                st.code(holder["error"])
            else:
                status.update(label=f"完成 Done · {time.time() - t0:.0f}s", state="complete")

        if "state" in holder:
            # 结构化决策卡（点位只从报告原文推导）
            card_md = ""
            with st.spinner("生成结构化决策卡 / Building decision card..."):
                try:
                    from ashare_vendor.decision_card import build_decision_card, card_to_markdown
                    card = build_decision_card(ticker.strip(), trade_date.isoformat(),
                                               holder["decision"], holder["state"])
                    card_md = card_to_markdown(card)
                except Exception as e:
                    st.caption(f"决策卡生成失败: {e}")
            # 存入 session_state：点击下载按钮会触发整页 rerun，结果须持久化才不丢失
            st.session_state["results"] = {
                "ticker": ticker.strip(), "day": trade_date.isoformat(),
                "decision": holder["decision"], "card_md": card_md,
                "state": holder["state"], "tracker": tracker,
            }
            load_history_entries.clear()  # 新分析完成后使历史缓存失效

    # 渲染已保存的分析结果（与 run_btn 解耦，下载按钮 rerun 后依然显示）
    _res = st.session_state.get("results")
    if _res:
        if _res["card_md"]:
            st.markdown(_res["card_md"])
        render_download(_res["ticker"], _res["day"], _res["decision"],
                        _res["card_md"], _res["state"], _res["tracker"])
        render_usage(_res["tracker"])
        render_results(_res["state"], _res["decision"])
        st.caption("⚠️ AI 生成内容，仅供研究参考，不构成投资建议。"
                   "AI-generated, for research only, not investment advice.")
    elif not run_btn:
        st.info("在左侧配置参数后点击「开始分析」。流程: 分析师收集数据 → 多空辩论 → "
                "交易员计划 → 风控评估 → 组合经理决策。运行中会实时显示当前所在环节与费用。")

with tab_history:
    render_history_tab()
