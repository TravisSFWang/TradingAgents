# -*- coding: utf-8 -*-
"""
Tushare 接入（支持第三方中转站）

.env 配置:
  TUSHARE_TOKEN=你的token
  TUSHARE_HTTP_URL=https://ts.gyzcloud.top/api   # 中转站地址；用官方则留空

配置了 TUSHARE_HTTP_URL（中转）后，行情/估值/公司信息/三大报表/宏观快讯/
增减持会全部优先走 Tushare，失败时自动回退原有链路（通达信/AKShare/东财直连）。
"""

import logging
import os

from .symbols import parse

logger = logging.getLogger("ashare_vendor")

_PRO = None


def token() -> str:
    t = os.getenv("TUSHARE_TOKEN", "").strip()
    return "" if (not t or t.startswith("your_")) else t


def relay_url() -> str:
    return os.getenv("TUSHARE_HTTP_URL", "").strip()


def configured() -> bool:
    """是否配置了 token（不管官方还是中转）"""
    return bool(token())


def preferred() -> bool:
    """配置了中转站则把 Tushare 提为最高优先级数据源"""
    return configured() and bool(relay_url())


def pro():
    """返回（缓存的）tushare pro api 实例，自动应用中转地址"""
    global _PRO
    if _PRO is None:
        import tushare as ts
        tok = token()
        ts.set_token(tok)
        _PRO = ts.pro_api(tok)
        url = relay_url()
        if url:
            _PRO._DataApi__http_url = url
            _patch_query(_PRO)
            logger.info(f"[ashare] Tushare 使用中转站: {url}")
    return _PRO


def _patch_query(api):
    """绕开 tushare SDK 给中转站注入的 ts_type_name 参数。

    tushare 1.4.x 的 DataApi.query() 会 kwargs.setdefault('ts_type_name', http_url)，
    给每次请求多塞一个参数。gyzcloud 中转站对行情类接口忽略它，但对宏观接口
    (cn_cpi/cn_gdp/cn_pmi/shibor) 会因此返回 0 行。这里用一个不注入该参数的
    干净实现替换实例方法（p.xxx() 经 __getattr__ 走 self.query）。
    """
    import json
    import types

    import pandas as pd
    import requests

    def _clean_query(self, api_name, fields="", **kwargs):
        kwargs.pop("ts_type_name", None)
        req = {"api_name": api_name, "token": self._DataApi__token,
               "params": kwargs, "fields": fields}
        res = requests.post(f"{self._DataApi__http_url}/{api_name}",
                            json=req, timeout=self._DataApi__timeout)
        if not res:
            return pd.DataFrame()
        result = json.loads(res.text)
        if result["code"] != 0:
            raise Exception(result["msg"])
        data = result["data"]
        return pd.DataFrame(data["items"], columns=data["fields"])

    api.query = types.MethodType(_clean_query, api)


def ts_code(symbol) -> str:
    """'600519' -> '600519.SH'"""
    _, code, mkt = parse(symbol)
    return f"{code}.{ {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}[mkt] }"


def stock_basic(symbol) -> dict:
    """公司基础信息: name, industry, market, list_date"""
    df = pro().stock_basic(ts_code=ts_code(symbol),
                           fields="ts_code,name,industry,market,list_date")
    if df is None or df.empty:
        return {}
    r = df.iloc[0]
    return {k: r[k] for k in ("name", "industry", "market", "list_date")
            if k in df.columns and r[k]}


def daily_basic(symbol, curr_date=None) -> dict:
    """估值指标（支持历史日期，适合回测）: pe, pe_ttm, pb, dv_ttm, total_mv(万元)"""
    kw = {"ts_code": ts_code(symbol),
          "fields": "trade_date,pe,pe_ttm,pb,dv_ttm,total_mv,circ_mv,turnover_rate"}
    if curr_date:
        end = str(curr_date).replace("-", "")
        import datetime as _dt
        start = (_dt.datetime.strptime(end, "%Y%m%d")
                 - _dt.timedelta(days=15)).strftime("%Y%m%d")
        kw["start_date"], kw["end_date"] = start, end
    df = pro().daily_basic(**kw)
    if df is None or df.empty:
        return {}
    # 中转站偶尔把 trade_date 混成 int/str，统一转字符串再排序，避免 TypeError
    df["trade_date"] = df["trade_date"].astype(str)
    r = df.sort_values("trade_date").iloc[-1]
    out = {c: r[c] for c in df.columns if r[c] is not None}
    # total_mv/circ_mv 单位万元 -> 元
    for k in ("total_mv", "circ_mv"):
        if out.get(k):
            out[k] = float(out[k]) * 1e4
    return out
