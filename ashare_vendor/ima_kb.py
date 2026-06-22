# -*- coding: utf-8 -*-
"""
腾讯 ima 共享知识库接入（B 方案：发现层）

能力边界（详见 CLAUDE.md §12，已核对官方 api.md/SKILL.md）：
ima OpenAPI **没有全文获取接口**——9 个接口只有 写入/浏览/检索 三类，唯一带正文
的字段是 `search_knowledge` 的 `highlight_content`（关键词命中处的高亮片段，非全文）。
因此本模块只做「发现」：按关键词在用户已加入的共享知识库里搜，返回 标题清单 +
命中片段，供 web 端预览、勾选后写入本地 library/（再由 library.py 注入分析）。
高价值篇目的全文仍需用户在 ima 客户端「提取文字」后手动落地。

.env 配置（均可空 = 功能休眠）：
  IMA_OPENAPI_CLIENTID=...     # https://ima.qq.com/agent-interface 获取
  IMA_OPENAPI_APIKEY=...
  IMA_KB_IDS=kb1,kb2           # 要检索的知识库 id（逗号分隔）；留空则自动列出全部可见库
"""

import logging
import os

logger = logging.getLogger("ashare_vendor")

_BASE = "https://ima.qq.com/openapi/wiki/v1"
_TIMEOUT = 30


def configured() -> bool:
    cid = os.getenv("IMA_OPENAPI_CLIENTID", "").strip()
    key = os.getenv("IMA_OPENAPI_APIKEY", "").strip()
    return (bool(cid) and not cid.startswith("your_")
            and bool(key) and not key.startswith("your_"))


def configured_kb_ids() -> list:
    raw = os.getenv("IMA_KB_IDS", "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "ima-openapi-clientid": os.getenv("IMA_OPENAPI_CLIENTID", "").strip(),
        "ima-openapi-apikey": os.getenv("IMA_OPENAPI_APIKEY", "").strip(),
    }


def _post(endpoint: str, body: dict, timeout: int = _TIMEOUT):
    """POST 一个接口。返回 data dict；失败 fail-open 返回 None 并记 warning。"""
    if not configured():
        return None
    try:
        import requests
        # 国内腾讯端点：若本机开了代理，强制直连避免 ProxyError
        resp = requests.post(
            f"{_BASE}/{endpoint}", json=body, headers=_headers(),
            timeout=timeout, proxies={"http": None, "https": None})
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning(f"[ima] {endpoint} 请求失败: {e}")
        return None
    # ⚠️ 实测响应为 {code, msg, data}，非官方手册写的 {retcode, errmsg}；两套都兼容。
    code = payload.get("code", payload.get("retcode"))
    if code not in (0, None):
        msg = payload.get("msg") or payload.get("errmsg")
        logger.warning(f"[ima] {endpoint} 业务错误 code={code}: {msg}")
        return None
    return payload.get("data") or {}


# ---------------- 知识库列表 ----------------

def list_knowledge_bases(limit: int = 20) -> list:
    """列出当前 key 可见的知识库（含已加入的共享库）[{id, name, base_type}]。游标翻页取全部。
    ⚠️ search_knowledge_base 的 limit 实测上限 20（手册写 50 会报错）；
       条目字段实测为 kb_id/kb_name（非手册的 id/name）。"""
    limit = min(max(int(limit), 1), 20)
    out, cursor = [], ""
    for _ in range(20):  # 翻页上限保护
        data = _post("search_knowledge_base", {"query": "", "cursor": cursor, "limit": limit})
        if not data:
            break
        for kb in data.get("info_list") or []:
            kid = kb.get("kb_id") or kb.get("id")
            if kid:
                out.append({"id": kid, "name": kb.get("kb_name") or kb.get("name", ""),
                            "base_type": kb.get("base_type", "")})
        if data.get("is_end", True):
            break
        cursor = data.get("next_cursor") or ""
        if not cursor:
            break
    return out


def resolve_kb_ids() -> list:
    """要检索的库：优先 .env 的 IMA_KB_IDS；为空则取全部可见库。
    返回 [{id, name}]（IMA_KB_IDS 指定时 name 可能为空，用 get_knowledge_base 补全）。"""
    ids = configured_kb_ids()
    if not ids:
        return list_knowledge_bases()
    names = {}
    data = _post("get_knowledge_base", {"ids": ids[:20]})
    if data:
        for kid, info in (data.get("infos") or {}).items():
            names[kid] = info.get("name", "")
    return [{"id": i, "name": names.get(i, "")} for i in ids]


# ---------------- 检索 ----------------

def search_knowledge(query: str, kb_id: str, max_items: int = 10) -> list:
    """在单个知识库里按关键词搜，返回前 max_items 条命中。
    每条 {media_id, title, parent_folder_id, highlight_content}。fail-open 返 []。"""
    if not query.strip() or not kb_id:
        return []
    out, cursor = [], ""
    for _ in range(10):
        data = _post("search_knowledge",
                     {"query": query.strip(), "knowledge_base_id": kb_id, "cursor": cursor})
        if not data:
            break
        for it in data.get("info_list") or []:
            if it.get("media_type") == 99:   # 文件夹条目，跳过
                continue
            out.append({
                "media_id": it.get("media_id", ""),
                "title": it.get("title", ""),
                "parent_folder_id": it.get("parent_folder_id", ""),
                # ⚠️ 共享库（普通成员）下 highlight_content 实测恒为空，只有标题可用
                "highlight_content": it.get("highlight_content", ""),
                "media_type": it.get("media_type"),
            })
            if len(out) >= max_items:
                return out
        if data.get("is_end", True):
            break
        cursor = data.get("next_cursor") or ""
        if not cursor:
            break
    return out


def discover(queries, kb_list=None, max_per_query: int = 8) -> list:
    """跨库、跨多个关键词检索并去重。

    queries: 关键词字符串或列表（如 [公司简称, 行业, 主题词]）。
    kb_list: [{id, name}]，默认 resolve_kb_ids()。
    返回去重后的命中列表，每条附 kb_id/kb_name/matched_query。按 media_id 去重，
    同一条被多个 query 命中时合并 matched_query。
    """
    if isinstance(queries, str):
        queries = [queries]
    queries = [q.strip() for q in queries if q and q.strip()]
    if not queries:
        return []
    if kb_list is None:
        kb_list = resolve_kb_ids()
    if not kb_list:
        logger.warning("[ima] 无可检索的知识库（检查 IMA_KB_IDS 或 key 权限）")
        return []

    merged = {}  # dedup_key -> result
    for kb in kb_list:
        kid, kname = kb.get("id"), kb.get("name", "")
        if not kid:
            continue
        for q in queries:
            for hit in search_knowledge(q, kid, max_items=max_per_query):
                key = hit.get("media_id") or f"{kid}:{hit.get('title')}"
                if key in merged:
                    if q not in merged[key]["matched_query"]:
                        merged[key]["matched_query"].append(q)
                    # 保留更长的 highlight
                    if len(hit.get("highlight_content", "")) > len(merged[key]["highlight_content"]):
                        merged[key]["highlight_content"] = hit["highlight_content"]
                else:
                    hit["kb_id"] = kid
                    hit["kb_name"] = kname
                    hit["matched_query"] = [q]
                    merged[key] = hit
    return list(merged.values())


# ---------------- 写入 library ----------------

_MEDIA_LABEL = {1: "PDF", 2: "网页/笔记", 3: "Word", 4: "PPT", 5: "Excel",
                6: "公众号", 7: "MD", 9: "图片", 11: "笔记", 13: "TXT"}


def write_to_library(ticker_code: str, results: list, lib_dir=None) -> str:
    """把勾选的命中**标题**写成一份 markdown，落进 library/<code>/，供 library.py 注入。

    返回写入的文件路径（字符串）；results 为空或写入失败返回 ""。
    ⚠️ ima 共享库 API 只返回标题（highlight_content 恒空、无全文）。本文件因此是
    **标题线索清单**，不是内容摘录——下游摘要/agent 不可当作完整资料，仅作"该主题下
    ima 库里有这些研报"的方向性信号。全文需用户在 ima 客户端打开标题用「提取文字」另存。
    """
    if not results:
        return ""
    from datetime import datetime
    from pathlib import Path
    base = Path(lib_dir) if lib_dir else (
        Path(os.getenv("LIBRARY_DIR") or Path.home() / ".tradingagents" / "library"))
    target = base / str(ticker_code)
    try:
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = target / f"ima_titles_{stamp}.md"
        lines = [
            f"# ima 共享知识库 · 相关研报标题线索 — {ticker_code} — "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "> ⚠️ 以下仅为 ima 共享库按关键词命中的**研报标题**（API 不返回正文/片段）。",
            "> 这是「该主题下有哪些资料」的方向信号，**非内容本身**；全文请在 ima 客户端打开。",
            "",
        ]
        for r in results:
            tlabel = _MEDIA_LABEL.get(r.get("media_type"), "")
            lines.append(f"- **{r.get('title') or '(无标题)'}**"
                         + (f"  [{tlabel}]" if tlabel else ""))
            sub = []
            if r.get("matched_query"):
                sub.append(f"命中词 {'/'.join(r['matched_query'])}")
            if r.get("kb_name"):
                sub.append(r["kb_name"])
            hl = (r.get("highlight_content") or "").strip()
            if hl:
                sub.append(f"片段: {hl}")
            if sub:
                lines.append(f"  - _{' · '.join(sub)}_")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"[ima] 已写入 {len(results)} 条标题线索到 {path}")
        return str(path)
    except Exception as e:
        logger.warning(f"[ima] 写入 library 失败: {e}")
        return ""


def resolve_search_terms(ticker: str) -> dict:
    """根据代码解析默认检索词：复用 context_patch 的身份解析拿 公司简称 + 行业。
    返回 {"name": ..., "industry": ..., "code": ...}（解析失败字段为空字符串）。"""
    out = {"name": "", "industry": "", "code": str(ticker).strip()}
    try:
        from .symbols import parse
        ok, code, _ = parse(ticker)
        if ok:
            out["code"] = code
            from .context_patch import _resolve_ashare_identity
            ident = _resolve_ashare_identity(code)
            out["name"] = ident.get("company_name", "") or ""
            out["industry"] = ident.get("industry", "") or ""
    except Exception as e:
        logger.debug(f"[ima] 检索词解析失败 {ticker}: {e}")
    return out
