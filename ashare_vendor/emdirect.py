# -*- coding: utf-8 -*-
"""
东方财富直连兜底（绕过 AKShare，借鉴 TradingAgents-CN 的做法）

AKShare 的接口随网站改版变动频繁（如 stock_news_em 正则报错、
stock_a_indicator_lg 被移除、stock_individual_info_em 返回空），
本模块直接调用东方财富公开 API 作为回退:
- quote(): 行情快照（名称/行业/PE/PB/市值）
- stock_news(): 个股新闻搜索

优先用 curl_cffi 模拟浏览器指纹（官方依赖里没有则自动退回 requests）。
"""

import json
import logging
import time

from .symbols import parse

logger = logging.getLogger("ashare_vendor")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _http_get(url, params, timeout=10):
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, params=params, timeout=timeout, impersonate="chrome120")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.text
    except ImportError:
        import requests
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": _UA, "Referer": "https://www.eastmoney.com/"})
        r.raise_for_status()
        return r.text


def quote(symbol) -> dict:
    """行情快照。返回可能含: name, industry, pe_ttm, pe, pb, total_mv, float_mv, price"""
    _, code, mkt = parse(symbol)
    secid = f"{1 if mkt == 'sh' else 0}.{code}"
    # f58名称 f127行业 f162PE(动) f164PE(TTM) f167PB f116总市值 f117流通市值 f43最新价
    text = _http_get("https://push2.eastmoney.com/api/qt/stock/get",
                     {"secid": secid, "invt": "2", "fltt": "2",
                      "fields": "f43,f57,f58,f116,f117,f127,f162,f164,f167"})
    data = (json.loads(text) or {}).get("data") or {}
    out = {}
    mapping = {"f58": "name", "f127": "industry", "f162": "pe",
               "f164": "pe_ttm", "f167": "pb", "f116": "total_mv",
               "f117": "float_mv", "f43": "price"}
    for k, name in mapping.items():
        v = data.get(k)
        if v not in (None, "-", ""):
            out[name] = v
    return out


def stock_news(symbol, limit=20) -> list:
    """个股新闻（东财搜索API）。返回 [{title, content, time, source}]，新→旧"""
    _, code, _ = parse(symbol)
    param = {"uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
             "client": "web", "clientType": "web", "clientVersion": "curr",
             "param": {"cmsArticleWebOld": {
                 "searchScope": "default", "sort": "default",
                 "pageIndex": 1, "pageSize": int(limit),
                 "preTag": "", "postTag": ""}}}
    ts = int(time.time() * 1000)
    text = _http_get("https://search-api-web.eastmoney.com/search/jsonp",
                     {"cb": f"jQuery{ts}", "param": json.dumps(param, ensure_ascii=False),
                      "_": str(ts)})
    if text.startswith("jQuery"):
        text = text[text.find("(") + 1: text.rfind(")")]
    arts = ((json.loads(text) or {}).get("result") or {}).get("cmsArticleWebOld") or []
    news = []
    for a in arts:
        news.append({"title": (a.get("title") or "").replace("<em>", "").replace("</em>", ""),
                     "content": (a.get("content") or "").replace("<em>", "").replace("</em>", ""),
                     "time": a.get("date", ""),
                     "source": a.get("mediaName") or a.get("source") or "东方财富网"})
    return news
