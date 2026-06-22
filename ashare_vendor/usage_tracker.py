# -*- coding: utf-8 -*-
"""
Token 用量/成本跟踪 + 实时进度上报（借鉴 CN 版的成本可观测性）

实现为 LangChain 回调，通过官方 TradingAgentsGraph(callbacks=[tracker]) 挂载，
零侵入。LangGraph 会把节点名放进回调 metadata（langgraph_node），
因此能按智能体归集 token 并知道当前进行到哪个节点。

价格（人民币/百万token，按缓存未命中价计）可用环境变量覆盖，子串匹配模型名:
  DEEPSEEK_PRICING="v4-pro=3,6;v4-flash=1,2"
默认价格按 2026-06 官网: v4-pro 3/6, v4-flash(含 chat/reasoner 旧名) 1/2
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # 极旧版本
    from langchain.callbacks.base import BaseCallbackHandler


def _price_for(model: str):
    """返回 (输入价, 输出价) 元/百万token。
    优先 DEEPSEEK_PRICING="v4-pro=3,6;v4-flash=1,2"（按模型名子串匹配），
    否则用内置默认价（2026-06 官网价，缓存未命中）。"""
    m = (model or "").lower()
    raw = os.getenv("DEEPSEEK_PRICING", "")
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k and k in m:
            try:
                i, o = (float(x) for x in v.split(","))
                return i, o
            except Exception:
                pass
    # 内置默认: v4-pro 3/6; v4-flash 及旧名 chat/reasoner 1/2
    return (3.0, 6.0) if "pro" in m else (1.0, 2.0)


def _extract_usage(response):
    """兼容多种 LangChain 版本的 usage 字段位置"""
    # 1) llm_output.token_usage
    try:
        u = (response.llm_output or {}).get("token_usage") or {}
        if u.get("prompt_tokens") or u.get("completion_tokens"):
            return (u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                    (response.llm_output or {}).get("model_name", ""))
    except Exception:
        pass
    # 2) generations[0][0].message.usage_metadata
    try:
        msg = response.generations[0][0].message
        u = getattr(msg, "usage_metadata", None) or {}
        if u:
            model = (getattr(msg, "response_metadata", {}) or {}).get("model_name", "")
            return u.get("input_tokens", 0), u.get("output_tokens", 0), model
    except Exception:
        pass
    return 0, 0, ""


class UsageTracker(BaseCallbackHandler):
    """按 LangGraph 节点归集 token 用量；同时暴露当前节点供进度展示"""

    def __init__(self):
        self._lock = threading.Lock()
        self._run_meta = {}          # run_id -> (node, model_hint, start_ts)
        self.per_node = {}           # node -> {"input":, "output":, "calls":, "cost":}
        self.current_node = None     # 最近开始 LLM 调用的节点
        self.node_history = []       # [(ts, node)] 去重相邻
        self.started_at = time.time()

    # ---- LangChain 回调 ----

    def on_chat_model_start(self, serialized, messages, *, run_id,
                            metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node") or "unknown"
        model = ""
        try:
            model = (serialized or {}).get("kwargs", {}).get("model", "") or \
                    (serialized or {}).get("kwargs", {}).get("model_name", "")
        except Exception:
            pass
        with self._lock:
            self._run_meta[str(run_id)] = (node, model, time.time())
            self.current_node = node
            if not self.node_history or self.node_history[-1][1] != node:
                self.node_history.append((time.time(), node))

    # 兼容非 chat 模型路径
    def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs):
        self.on_chat_model_start(serialized, prompts, run_id=run_id,
                                 metadata=metadata, **kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        tin, tout, model = _extract_usage(response)
        with self._lock:
            node, model_hint, _ = self._run_meta.pop(str(run_id), ("unknown", "", 0))
            model = model or model_hint
            rec = self.per_node.setdefault(node, {"input": 0, "output": 0,
                                                  "calls": 0, "cost": 0.0,
                                                  "model": model})
            rec["input"] += int(tin or 0)
            rec["output"] += int(tout or 0)
            rec["calls"] += 1
            pi, po = _price_for(model)
            rec["cost"] += (tin or 0) / 1e6 * pi + (tout or 0) / 1e6 * po
            if model:
                rec["model"] = model

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self._lock:
            self._run_meta.pop(str(run_id), None)

    # ---- 查询接口 ----

    def totals(self):
        with self._lock:
            tin = sum(r["input"] for r in self.per_node.values())
            tout = sum(r["output"] for r in self.per_node.values())
            cost = sum(r["cost"] for r in self.per_node.values())
            calls = sum(r["calls"] for r in self.per_node.values())
        return {"input_tokens": tin, "output_tokens": tout,
                "calls": calls, "cost_cny": round(cost, 4),
                "elapsed_sec": round(time.time() - self.started_at)}

    def rows(self):
        """[(节点, 模型, 输入, 输出, 调用次数, 费用)] 按费用降序"""
        with self._lock:
            data = [(n, r["model"], r["input"], r["output"], r["calls"],
                     round(r["cost"], 4)) for n, r in self.per_node.items()]
        return sorted(data, key=lambda x: -x[5])

    def save(self, path, ticker="", trade_date=""):
        """追加一条 JSONL 记录，便于长期成本统计"""
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                     "ticker": ticker, "trade_date": trade_date,
                     **self.totals(),
                     "per_node": {n: {k: v for k, v in r.items()}
                                  for n, r in self.per_node.items()}}
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
