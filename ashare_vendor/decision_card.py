# -*- coding: utf-8 -*-
"""
结构化决策卡（借鉴 daily_stock_analysis 的 schema 约束思路）

分析结束后用快速模型把全部报告压成一张可执行的 JSON 决策卡:
操作建议/信心评分/买入区间/止损/目标位/仓位/理由/风险/推翻条件/操作清单。
关键数字只允许来自报告原文，推不出来就置 null —— 防止编造点位。
"""

import json
import logging
import os

logger = logging.getLogger("ashare_vendor")

_SCHEMA_HINT = """{
  "action": "买入/增持/持有/减持/卖出/观望 之一",
  "conviction_score": "0-100 整数，综合信心",
  "time_horizon": "如 1-2周 / 1-3月",
  "buy_zone": [低位数字或null, 高位数字或null],
  "stop_loss": "数字或null",
  "targets": [数字...] 或 [],
  "position_size_pct": "建议仓位百分比数字或null",
  "key_reasons": ["不超过3条核心理由"],
  "risk_alerts": ["不超过3条风险警报"],
  "invalidation": "什么情况发生则本结论作废",
  "checklist": ["执行前检查清单，不超过4条"]
}"""


def _loads_lenient(raw):
    """宽松解析模型 JSON：容忍字符串内裸控制字符(strict=False)与尾部截断。"""
    if not raw:
        return None
    try:
        return json.loads(raw, strict=False)
    except Exception:
        pass
    # 尾部被截断（如 max_tokens 用尽）→ 退到最后一个右括号再试
    i = raw.rfind("}")
    if i != -1:
        try:
            return json.loads(raw[:i + 1], strict=False)
        except Exception:
            pass
    return None


def _collect_text(state, decision):
    parts = [f"FINAL DECISION:\n{decision}"]
    for k in ("market_report", "news_report", "fundamentals_report",
              "sentiment_report", "trader_investment_plan",
              "final_trade_decision"):
        v = state.get(k) if isinstance(state, dict) else None
        if v:
            parts.append(f"--- {k} ---\n{str(v)[:4000]}")
    return "\n\n".join(parts)


def build_decision_card(ticker, trade_date, decision, state, model=None):
    """返回 dict 决策卡；失败返回 None（不影响主流程）"""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    model = model or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-flash")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key,
                        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        prompt = (
            "You are a strict execution-desk assistant. Read the multi-agent "
            "analysis reports below and produce ONE JSON object (no markdown) "
            "summarizing the actionable decision. Field values in Simplified "
            "Chinese. STRICT RULES: every price level (buy_zone, stop_loss, "
            "targets) must be derived from numbers explicitly present in the "
            "reports; if not derivable, use null/empty. Do not invent data.\n\n"
            f"JSON schema:\n{_SCHEMA_HINT}\n\n"
            f"Ticker: {ticker}  Trade date: {trade_date}\n\n"
            + _collect_text(state, decision))
        card = None
        for attempt in range(2):  # 偶发空响应 → 重试一次
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1, max_tokens=1500, timeout=90)
            raw = (resp.choices[0].message.content or "").strip().strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            card = _loads_lenient(raw)
            if isinstance(card, dict):
                return card
            logger.warning(f"[ashare] 决策卡响应无法解析（尝试 {attempt + 1}/2，"
                           f"raw 长度 {len(raw)}）")
        return None
    except Exception as e:
        logger.warning(f"[ashare] 决策卡生成失败 {ticker}: {e}")
        return None


def card_to_markdown(card) -> str:
    """决策卡 -> markdown 文本（用于报告导出）"""
    if not card:
        return ""

    def _fmt(v):
        if v is None or v == [] or v == [None, None]:
            return "—"
        if isinstance(v, list):
            return " / ".join("—" if x is None else str(x) for x in v)
        return str(v)

    lines = ["## 📋 结构化决策卡",
             "",
             f"| 操作 | 信心 | 周期 | 建议仓位 |",
             f"|---|---|---|---|",
             f"| **{_fmt(card.get('action'))}** | {_fmt(card.get('conviction_score'))}/100 "
             f"| {_fmt(card.get('time_horizon'))} | {_fmt(card.get('position_size_pct'))}% |",
             "",
             f"- **买入区间**: {_fmt(card.get('buy_zone'))}",
             f"- **止损位**: {_fmt(card.get('stop_loss'))}",
             f"- **目标位**: {_fmt(card.get('targets'))}",
             f"- **结论作废条件**: {_fmt(card.get('invalidation'))}",
             ""]
    for title, key in (("核心理由", "key_reasons"), ("风险警报", "risk_alerts"),
                       ("执行清单", "checklist")):
        items = card.get(key) or []
        if items:
            lines.append(f"**{title}:**")
            lines += [f"- {x}" for x in items]
            lines.append("")
    lines.append("> ⚠️ 点位仅由报告原文数字推导，null 表示报告未给出依据；AI 生成，不构成投资建议。")
    return "\n".join(lines)
