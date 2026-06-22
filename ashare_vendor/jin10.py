# -*- coding: utf-8 -*-
"""
金十数据 MCP 接入（新闻/快讯/财经日历）

金十提供标准 MCP over HTTP 服务，本模块用轻量 JSON-RPC 客户端直连:
initialize -> notifications/initialized -> tools/call

.env 配置:
  JIN10_API_KEY=sk-xxx
  JIN10_MCP_URL=https://mcp.jin10.com/mcp   # 默认值，一般不用改
"""

import json
import logging
import os
import threading

logger = logging.getLogger("ashare_vendor")

_LOCK = threading.Lock()
_SESSION = {"id": None}
_RPC_ID = [0]


def configured() -> bool:
    k = os.getenv("JIN10_API_KEY", "").strip()
    return bool(k) and not k.startswith("your_")


def _url():
    return os.getenv("JIN10_MCP_URL", "https://mcp.jin10.com/mcp").strip()


def _headers():
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream",
         "Authorization": f"Bearer {os.getenv('JIN10_API_KEY', '').strip()}"}
    if _SESSION["id"]:
        h["Mcp-Session-Id"] = _SESSION["id"]
    return h


def _parse_body(resp):
    """兼容 JSON 与 SSE 两种响应体"""
    # 服务端返回 UTF-8 但常缺 charset，requests 会按 latin-1 解码导致中文乱码 → 强制 UTF-8
    resp.encoding = "utf-8"
    text = resp.text or ""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
        # SSE: 单个事件可能跨多行 data:（需按 \n 拼接），事件间以空行分隔。
        # 旧实现只取最后一行 data: 会截断 JSON -> "Unterminated string"。
        events, cur = [], []
        for line in text.splitlines():
            if line.startswith("data:"):
                cur.append(line[5:].lstrip())
            elif line.strip() == "":
                if cur:
                    events.append("\n".join(cur))
                    cur = []
        if cur:
            events.append("\n".join(cur))
        for payload in reversed(events):
            if payload and payload != "[DONE]":
                try:
                    return json.loads(payload)
                except Exception:
                    continue
        return {}
    return json.loads(text) if text.strip() else {}


class _SessionExpired(RuntimeError):
    """会话失效/404，可清会话后重连重试。"""


def _post(payload, timeout=15):
    import requests
    r = requests.post(_url(), headers=_headers(),
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      timeout=timeout)
    if r.status_code in (401, 403):
        raise RuntimeError(f"金十鉴权失败 HTTP {r.status_code}（检查 JIN10_API_KEY）")
    if r.status_code == 404:
        # MCP 会话过期/失效（长任务里常见）→ 清会话，交由上层重连重试一次
        _SESSION["id"] = None
        raise _SessionExpired("金十会话失效(404)")
    r.raise_for_status()
    sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    if sid:
        _SESSION["id"] = sid
    return _parse_body(r)


def _rpc(method, params=None, notify=False):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        _RPC_ID[0] += 1
        msg["id"] = _RPC_ID[0]
    return _post(msg)


def _ensure_session():
    with _LOCK:
        if _SESSION["id"]:
            return
        out = _rpc("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "tradingagents-ashare", "version": "1.0"}})
        if "error" in out:
            raise RuntimeError(f"金十 initialize 失败: {out['error']}")
        try:
            _rpc("notifications/initialized", {}, notify=True)
        except Exception:
            pass  # 部分实现不要求该通知


def call_tool(name, arguments=None, timeout=15):
    """调用金十 MCP 工具，返回 structuredContent（优先）或解析后的 content 文本"""
    if not configured():
        raise RuntimeError("未配置 JIN10_API_KEY")
    try:
        _ensure_session()
        out = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    except _SessionExpired:
        # 会话过期：清掉后重连一次再调（_post 已置 _SESSION["id"]=None）
        _ensure_session()
        out = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    if "error" in out:
        raise RuntimeError(f"金十 {name} 协议错误: {out['error']}")
    result = out.get("result") or {}
    if result.get("isError"):
        raise RuntimeError(f"金十 {name} 业务错误: {result}")
    sc = result.get("structuredContent")
    if sc is not None:
        return sc
    for c in result.get("content") or []:
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except Exception:
                return {"text": c["text"]}
    return {}


# ---------------- 业务封装 ----------------

def _items(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return data.get("items") or []
    if isinstance(data, list):
        return data
    return payload.get("items") or [] if isinstance(payload, dict) else []


def flash_list(limit=15):
    """最新快讯 [{time, content}]"""
    items = _items(call_tool("list_flash", {}))
    out = []
    for it in items[:limit]:
        out.append({"time": it.get("time") or it.get("pub_time", ""),
                    "content": it.get("content") or it.get("title", "")})
    return out

def flash_search(keyword, limit=10):
    """按关键词搜索快讯"""
    items = _items(call_tool("search_flash", {"keyword": str(keyword)}))
    out = []
    for it in items[:limit]:
        out.append({"time": it.get("time") or it.get("pub_time", ""),
                    "content": it.get("content") or it.get("title", "")})
    return out


def calendar(limit=12):
    """财经日历 [{pub_time, star, title, previous, consensus, actual}]"""
    payload = call_tool("list_calendar", {})
    items = _items(payload)
    out = []
    for it in items[:limit]:
        out.append({k: it.get(k) for k in
                    ("pub_time", "star", "title", "previous", "consensus",
                     "actual", "affect_txt")})
    return out
