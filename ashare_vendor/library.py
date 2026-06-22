# -*- coding: utf-8 -*-
"""
本地资料夹注入（CLAUDE.md §11 设计定稿）

读取 ~/.tradingagents/library/ 下用户预置的个股/宏观资料（研报/年报/纪要/笔记，
txt/md/pdf/docx），用快速模型摘成结构化要点后注入 instrument_context；
文件未变走缓存（"下次不再读取"），多股行业报告只注入当前个股的切片。

目录约定:
  library/<code>/        个股强绑定资料（6位A股代码 或 美股ticker大写 作文件夹名）
  library/_macro/        宏观/政策资料，每次分析都注入
  library/*.*            根目录: 丢任意报告，按摘要时自动抽取的"提及个股"路由
  library/.cache/<sha1>.json   结构化摘要缓存（一份/文件版本）
  library/.index.json          path -> {size, mtime, sha1}（快路径判新旧）
  library/.name2code.json      A股简称->代码 反查表（tushare/akshare 全表，月级缓存）

.env 配置（均可省略）:
  LIBRARY_ENABLED=true            总开关
  LIBRARY_DIR=...                 覆盖默认目录
  LIBRARY_PER_FILE_TOKENS=400     单份资料注入上限
  LIBRARY_MAX_TOKENS=2500         单次分析注入总预算
  LIBRARY_SUMMARY_MODEL=...       摘要模型（默认随 TRADINGAGENTS_QUICK_THINK_LLM）

设计要点: 每步 fail-open——缺解析库/LLM 超时/文件损坏一律跳过该文件并记
warning，绝不阻塞分析；未配置/无资料返回空串，零额外 token。
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ashare_vendor")

_SUPPORTED = {".txt", ".md", ".pdf", ".docx"}
_NAME2CODE_TTL_DAYS = 30
_MAX_CHARS = 100_000   # 超长文档（年报）取 头80k+尾20k
_CHUNK_CHARS = 20_000  # 超过则分块先做要点再合并


# ---------------- 配置 ----------------

def _env_true(key, default="true"):
    return os.getenv(key, default).strip().lower() in ("true", "1", "yes", "on")


def _lib_dir() -> Path:
    return Path(os.getenv("LIBRARY_DIR") or Path.home() / ".tradingagents" / "library")


def _int_env(key, default):
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _est_tokens(s: str) -> int:
    """粗估 token: 中文≈1字/token，英文≈4字符/token"""
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    return cjk + (len(s) - cjk) // 4


# ---------------- JSON 读写（fail-open） ----------------

def _read_json(p: Path):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(p: Path, data):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        logger.debug(f"[library] 写入失败 {p}: {e}")


# ---------------- 正文抽取 ----------------

def _extract_text(path: Path, blob: bytes) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            for enc in ("utf-8", "gb18030"):
                try:
                    return blob.decode(enc)
                except UnicodeDecodeError:
                    continue
            return blob.decode("utf-8", errors="ignore")
        if ext == ".pdf":
            import io
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(blob))
            return "\n".join((pg.extract_text() or "") for pg in r.pages)
        if ext == ".docx":
            import io
            from docx import Document
            d = Document(io.BytesIO(blob))
            return "\n".join(p.text for p in d.paragraphs)
    except ImportError as e:
        logger.warning(f"[library] 缺解析库，跳过 {path.name}: {e}")
    except Exception as e:
        logger.warning(f"[library] 解析失败，跳过 {path.name}: {e}")
    return ""


# ---------------- LLM 摘要（复用 decision_card 的 DeepSeek flash 模式） ----------------

_SUM_SCHEMA = """{
  "theme": "one-line document theme",
  "native_date": "the document's own date YYYY-MM-DD, or null",
  "common_points": ["3-8 theme/industry-level decision-relevant bullets; EACH prefixed with exactly one tag of [基本面] [政策] [技术] [消息]; lead with specific numbers/facts"],
  "by_stock": {"<listed-company short name EXACTLY as written in the document>": ["1-4 bullets specific to that company, tagged the same way"]},
  "mentioned_names": ["every listed company short name mentioned in the document"]
}"""


def _llm(prompt: str, json_mode: bool, max_tokens: int) -> Optional[str]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.debug("[library] 未配置 DEEPSEEK_API_KEY，跳过摘要")
        return None
    model = (os.getenv("LIBRARY_SUMMARY_MODEL", "").strip()
             or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-flash"))
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key,
                        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=max_tokens, timeout=120, **kw)
        raw = (resp.choices[0].message.content or "").strip()
        if json_mode:
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        return raw or None
    except Exception as e:
        logger.warning(f"[library] LLM 摘要失败: {e}")
        return None


def _loads_lenient(raw):
    """宽松解析模型 JSON：容忍字符串内裸控制字符(strict=False)与尾部截断。"""
    if not raw:
        return None
    try:
        return json.loads(raw, strict=False)
    except Exception:
        pass
    i = raw.rfind("}")
    if i != -1:
        try:
            return json.loads(raw[:i + 1], strict=False)
        except Exception:
            pass
    return None


def _summarize_text(text: str, fname: str) -> Optional[dict]:
    """全文 -> 结构化摘要 dict；失败返回 None（不缓存，下次重试）"""
    text = text.strip()
    if len(text) > _MAX_CHARS:
        text = text[:80_000] + "\n...[middle truncated]...\n" + text[-20_000:]
    if len(text) > _CHUNK_CHARS:  # 分块先抽要点，再对要点做结构化合并
        notes = []
        for i in range(0, len(text), _CHUNK_CHARS):
            r = _llm(
                "Extract decision-relevant facts from this document section as "
                "concise English bullets (numbers, catalysts, risks, policies; "
                "keep company names in original Chinese). Max 12 bullets. "
                f"Section of {fname}:\n\n" + text[i:i + _CHUNK_CHARS],
                json_mode=False, max_tokens=600)
            if r:
                notes.append(r)
        if not notes:
            return None
        text = "\n".join(notes)

    budget = _int_env("LIBRARY_PER_FILE_TOKENS", 400)
    raw = _llm(
        "You are an equity-research assistant. Read the document and output ONE "
        "JSON object (no markdown), bullets in ENGLISH, with this exact schema:\n"
        f"{_SUM_SCHEMA}\n"
        "Rules: only decision-relevant facts (catalysts, key financials, risks, "
        "policy changes, theses). Every number must come from the document; do "
        f"not invent. Keep total output under {budget} tokens. "
        f"Document filename: {fname}\n\nDOCUMENT:\n{text}",
        json_mode=True, max_tokens=max(800, budget * 3))
    if not raw:
        return None
    d = _loads_lenient(raw)
    if d is None:
        logger.warning(f"[library] 摘要 JSON 解析失败 {fname}（raw 长度 {len(raw)}）")
        return None
    if not isinstance(d, dict):
        return None
    # 类型清洗（模型输出契约不完全可信）
    d["theme"] = str(d.get("theme") or "")
    d["common_points"] = [str(x) for x in (d.get("common_points") or []) if x]
    bs = d.get("by_stock")
    d["by_stock"] = ({str(k).strip(): [str(v) for v in (vs or []) if v]
                      for k, vs in bs.items() if k}
                     if isinstance(bs, dict) else {})
    d["mentioned_names"] = [str(x).strip() for x in (d.get("mentioned_names") or []) if x]
    nd = d.get("native_date")
    d["native_date"] = str(nd) if nd else None
    return d


# ---------------- A股简称 -> 代码 反查表 ----------------

def _name2code() -> dict:
    cache_f = _lib_dir() / ".name2code.json"
    data = _read_json(cache_f) or {}
    if data.get("map") and time.time() - data.get("built_at", 0) < _NAME2CODE_TTL_DAYS * 86400:
        return data["map"]
    m = {}
    try:
        from . import ts_relay
        if ts_relay.configured():
            df = ts_relay.pro().stock_basic(fields="ts_code,name")
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    m[str(r["name"]).strip()] = str(r["ts_code"])[:6]
    except Exception as e:
        logger.debug(f"[library] tushare 名称表失败: {e}")
    if not m:
        try:
            import akshare as ak
            tbl = ak.stock_info_a_code_name()
            for _, r in tbl.iterrows():
                m[str(r["name"]).strip()] = str(r["code"]).zfill(6)
        except Exception as e:
            logger.debug(f"[library] akshare 名称表失败: {e}")
    if m:
        _write_json(cache_f, {"built_at": time.time(), "map": m})
        return m
    return data.get("map") or {}  # 两源都失败时用过期表兜底


def _map_names(names) -> dict:
    """提及的公司简称 -> {简称: 6位代码}（仅精确命中；未命中留给注入时重试）"""
    table = _name2code()
    out = {}
    for n in names or []:
        c = table.get(str(n).strip())
        if c:
            out[str(n).strip()] = c
    return out


# ---------------- 文件扫描 / 摘要缓存 ----------------

def _scan(root: Path):
    """返回 [(path, bind_key)]；bind_key: None=根目录自动路由, "_macro", 或代码/ticker"""
    out = []
    for p in sorted(root.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file() and p.suffix.lower() in _SUPPORTED:
            out.append((p, None))
        elif p.is_dir():
            bind = "_macro" if p.name == "_macro" else p.name.upper()
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in _SUPPORTED:
                    out.append((f, bind))
    return out


def _get_summary(path: Path, index: dict) -> Optional[dict]:
    """三级判新: (size,mtime) 快路径不开文件 -> sha1 兜底(改名/touch) -> 真摘要"""
    root = _lib_dir()
    rel = str(path.relative_to(root))
    try:
        stt = path.stat()
    except OSError:
        return None
    cache_dir = root / ".cache"

    ent = index.get(rel)
    if ent and ent.get("size") == stt.st_size and ent.get("mtime") == int(stt.st_mtime):
        s = _read_json(cache_dir / f"{ent['sha1']}.json")
        if s:
            return s  # 快路径: 未变，零读盘零LLM

    try:
        blob = path.read_bytes()
    except OSError as e:
        logger.warning(f"[library] 读取失败，跳过 {rel}: {e}")
        return None
    sha1 = hashlib.sha1(blob).hexdigest()
    index[rel] = {"size": stt.st_size, "mtime": int(stt.st_mtime), "sha1": sha1}
    s = _read_json(cache_dir / f"{sha1}.json")
    if s:
        return s  # 改名/复制/touch 但内容没变: 复用摘要

    text = _extract_text(path, blob)
    if not text.strip():
        return None
    logger.info(f"[library] 摘要新资料: {rel}")
    s = _summarize_text(text, path.name)
    if not s:
        index.pop(rel, None)  # 失败不入 index，下次重试
        return None
    s["title"] = path.name
    s["sha1"] = sha1
    s["mentioned_codes"] = _map_names(s.get("mentioned_names"))
    if not s.get("native_date"):
        s["native_date"] = datetime.fromtimestamp(stt.st_mtime).strftime("%Y-%m-%d")
    s["summarized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_json(cache_dir / f"{sha1}.json", s)
    return s


def _refresh_codes(s: dict, cache_dir: Path) -> dict:
    """注入时对未命中的简称重试映射（名称表月级刷新后可能补中），并回写缓存"""
    names = s.get("mentioned_names") or []
    codes = s.get("mentioned_codes") or {}
    missing = [n for n in names if n not in codes]
    if missing:
        add = _map_names(missing)
        if add:
            codes.update(add)
            s["mentioned_codes"] = codes
            if s.get("sha1"):
                _write_json(cache_dir / f"{s['sha1']}.json", s)
    return codes


# ---------------- 切片与注入 ----------------

def _resolve_code(ticker) -> str:
    """A股 -> 6位代码；其他市场 -> 大写 ticker（靠绑定文件夹 + _macro 生效）"""
    try:
        from .symbols import parse
        ok, code, _ = parse(ticker)
        if ok:
            return code
    except Exception:
        pass
    return str(ticker).strip().upper()


def _slice(s: dict, code: str, bind: Optional[str]):
    """返回 (common_bullets, stock_bullets, hit)。多股报告只切当前个股。"""
    common = list(s.get("common_points") or [])
    by_stock = s.get("by_stock") or {}
    codes = s.get("mentioned_codes") or {}
    names = [n for n, c in codes.items() if c == code]
    stock = [f"({n}) {b}" for n in names for b in (by_stock.get(n) or [])]

    if bind == "_macro":
        return common, [], True
    if bind == code:  # 强绑定: 用户断言相关；没切到名字则给全部个股要点
        if not stock:
            stock = [f"({n}) {b}" for n, bs in by_stock.items() for b in bs]
        return common, stock, True
    if names:  # 根目录自动路由: 必须命中提及代码
        return common, stock, True
    return [], [], False


_HEADER = (
    "=== USER-PROVIDED LOCAL RESEARCH LIBRARY ===\n"
    "The following are pre-loaded reference materials the user placed in the local "
    "library folder. They may PREDATE the analysis date. If they conflict with live "
    "tool data, TRUST THE LIVE DATA, unless the material is explicitly newer or more "
    "authoritative (e.g., verbatim policy text)."
)


def build_library_context(ticker) -> str:
    """主入口（context_patch 调用）。无资料/未启用返回 ""，零开销。"""
    if not _env_true("LIBRARY_ENABLED"):
        return ""
    root = _lib_dir()
    if not root.is_dir():
        return ""
    code = _resolve_code(ticker)
    if not code:
        return ""

    index = _read_json(root / ".index.json") or {}
    cache_dir = root / ".cache"
    per_file_cap = _int_env("LIBRARY_PER_FILE_TOKENS", 400)

    blocks = []  # (priority, native_date, text)
    for path, bind in _scan(root):
        try:
            s = _get_summary(path, index)
            if not s:
                continue
            _refresh_codes(s, cache_dir)
            common, stock, hit = _slice(s, code, bind)
            if not hit or not (common or stock):
                continue
            # 单份资料预算: 超额先砍主题级要点，保个股专属要点
            while common or stock:
                body = "\n".join(f"- {b}" for b in common + stock)
                if _est_tokens(body) <= per_file_cap:
                    break
                (common if common else stock).pop()
            if not (common or stock):
                continue
            head = f"[{s['title']}] (dated {s.get('native_date') or 'unknown'})"
            theme = f" Theme: {s['theme']}" if s.get("theme") else ""
            text = head + theme + "\n" + "\n".join(f"- {b}" for b in common + stock)
            prio = 0 if bind == code else (2 if bind == "_macro" else 1)
            blocks.append((prio, s.get("native_date") or "", text))
        except Exception as e:
            logger.warning(f"[library] 处理失败，跳过 {path.name}: {e}")

    # 清掉已删除文件的 index 条目后落盘
    index = {k: v for k, v in index.items() if (root / k).exists()}
    _write_json(root / ".index.json", index)

    if not blocks:
        return ""
    # 排序: 强绑定 > 自动命中 > 宏观；同级新资料优先
    blocks.sort(key=lambda x: x[1], reverse=True)
    blocks.sort(key=lambda x: x[0])
    # 总预算: 贪心装入
    budget = _int_env("LIBRARY_MAX_TOKENS", 2500)
    used = _est_tokens(_HEADER)
    chosen = []
    for _, _, text in blocks:
        t = _est_tokens(text)
        if used + t > budget:
            continue
        chosen.append(text)
        used += t
    if not chosen:
        return ""
    logger.info(f"[ashare] 本地资料注入 {len(chosen)} 份（约 {used} tokens）: {code}")
    return _HEADER + "\n\n" + "\n\n".join(chosen)


# ---------------- 只读巡检（web 资料状态面板用） ----------------

def inspect_library(ticker) -> dict:
    """列出与 ticker 相关的 library 资料及其读取状态。**纯只读**：不摘要、不调 LLM、
    不写 index/缓存——新文件的真正读取发生在下次分析时。

    只返回与该代码相关的条目：根目录、_macro/、<该代码>/（其它个股的绑定文件夹跳过）。
    返回 {"dir": 根目录, "items": [...]}，每项：
      file         相对路径
      bind         None=根目录自动路由 / "_macro" / 代码
      status       "cached"=已读取(摘要已缓存) / "new"=新发现 / "changed"=已修改(将重读)
      will_inject  True/False=本次分析是否会注入该股；None=待读取后才能判定
      native_date / theme  已读取资料的元信息（未读取为空）
    """
    root = _lib_dir()
    out = {"dir": str(root), "items": []}
    if not root.is_dir():
        return out
    code = _resolve_code(ticker)
    index = _read_json(root / ".index.json") or {}
    cache_dir = root / ".cache"

    for path, bind in _scan(root):
        if bind not in (None, "_macro") and bind != code:
            continue  # 其它个股的绑定资料，与本股无关
        rel = str(path.relative_to(root))
        try:
            stt = path.stat()
        except OSError:
            continue
        ent = index.get(rel)
        s, status = None, "new"
        if ent and ent.get("size") == stt.st_size and ent.get("mtime") == int(stt.st_mtime):
            s = _read_json(cache_dir / f"{ent['sha1']}.json")
            status = "cached" if s else "new"
        elif ent:
            status = "changed"
        item = {"file": rel, "bind": bind, "status": status,
                "will_inject": None, "native_date": None, "theme": ""}
        if s:
            item["native_date"] = s.get("native_date")
            item["theme"] = s.get("theme", "")
            common, stock, hit = _slice(s, code, bind)
            item["will_inject"] = bool(hit and (common or stock))
        out["items"].append(item)
    return out


# ---------------- #9 (ima/共享知识库) 预留接口 ----------------

@dataclass
class Material:
    """统一资料单元。外部源（ima/WeKnora 检索结果）构造 Material 后调
    summarize_material()，复用 摘要->缓存->切片->预算 同一管线。"""
    source: str                       # "local" / "ima" / "weknora"
    id: str                           # local: 相对路径；外部: 文档 id
    title: str
    text: str
    native_date: Optional[str] = None
    bind_key: Optional[str] = None    # None=自动路由 / "_macro" / 个股代码


def summarize_material(mat: Material) -> Optional[dict]:
    """外部 Material 进同一缓存管线（按文本 sha1 去重）"""
    sha1 = hashlib.sha1(mat.text.encode("utf-8")).hexdigest()
    cache_f = _lib_dir() / ".cache" / f"{sha1}.json"
    s = _read_json(cache_f)
    if s:
        return s
    s = _summarize_text(mat.text, mat.title)
    if not s:
        return None
    s["title"] = mat.title
    s["sha1"] = sha1
    s["source"] = mat.source
    s["mentioned_codes"] = _map_names(s.get("mentioned_names"))
    if not s.get("native_date"):
        s["native_date"] = mat.native_date
    s["summarized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_json(cache_f, s)
    return s
