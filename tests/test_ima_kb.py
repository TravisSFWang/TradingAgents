# -*- coding: utf-8 -*-
"""ima 知识库发现层（ashare_vendor/ima_kb.py，CLAUDE.md §12 B方案）离线测试。

全部打桩 _post，不需网络/密钥。覆盖：configured 开关、跨库跨词去重合并、
搜索游标翻页与 max_items 截断、write_to_library 落盘格式与目录路由。
"""

import os

import pytest

from ashare_vendor import ima_kb


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("IMA_OPENAPI_CLIENTID", "cid")
    monkeypatch.setenv("IMA_OPENAPI_APIKEY", "key")
    monkeypatch.delenv("IMA_KB_IDS", raising=False)


def test_configured_toggle(monkeypatch):
    assert ima_kb.configured() is True
    monkeypatch.setenv("IMA_OPENAPI_APIKEY", "your_key_here")
    assert ima_kb.configured() is False
    monkeypatch.setenv("IMA_OPENAPI_APIKEY", "")
    assert ima_kb.configured() is False


def test_search_paging_and_truncation(monkeypatch):
    pages = {
        "": {"info_list": [{"media_id": f"m{i}", "title": f"T{i}",
                            "highlight_content": f"h{i}"} for i in range(5)],
             "is_end": False, "next_cursor": "c1"},
        "c1": {"info_list": [{"media_id": f"m{i}", "title": f"T{i}",
                              "highlight_content": f"h{i}"} for i in range(5, 12)],
               "is_end": True},
    }
    monkeypatch.setattr(ima_kb, "_post",
                        lambda ep, body, **k: pages.get(body.get("cursor", "")))
    out = ima_kb.search_knowledge("光模块", "kb1", max_items=8)
    assert len(out) == 8                       # 跨页累计后按 max_items 截断
    assert out[0]["media_id"] == "m0"
    assert out[7]["media_id"] == "m7"


def test_discover_dedup_across_queries_and_kbs(monkeypatch):
    # 同一 media_id 被两个 query 命中 -> 合并 matched_query，保留更长 highlight
    def fake_search(query, kb_id, max_items=8):
        if kb_id == "kbA" and query == "黄河旋风":
            return [{"media_id": "m1", "title": "金刚石散热", "highlight_content": "短"},
                    {"media_id": "m2", "title": "其它", "highlight_content": "x"}]
        if kb_id == "kbA" and query == "金刚石":
            return [{"media_id": "m1", "title": "金刚石散热",
                     "highlight_content": "更长的命中片段内容"}]
        if kb_id == "kbB" and query == "黄河旋风":
            return [{"media_id": "m3", "title": "B库命中", "highlight_content": "y"}]
        return []

    monkeypatch.setattr(ima_kb, "search_knowledge", fake_search)
    kbs = [{"id": "kbA", "name": "库A"}, {"id": "kbB", "name": "库B"}]
    out = ima_kb.discover(["黄河旋风", "金刚石"], kb_list=kbs)

    by_id = {r["media_id"]: r for r in out}
    assert set(by_id) == {"m1", "m2", "m3"}             # 跨库+去重
    assert sorted(by_id["m1"]["matched_query"]) == ["金刚石", "黄河旋风"]  # 合并命中词
    assert by_id["m1"]["highlight_content"] == "更长的命中片段内容"      # 保留更长片段
    assert by_id["m1"]["kb_name"] == "库A"
    assert by_id["m3"]["kb_name"] == "库B"


def test_discover_empty_queries(monkeypatch):
    monkeypatch.setattr(ima_kb, "search_knowledge", lambda *a, **k: [])
    assert ima_kb.discover([], kb_list=[{"id": "k", "name": "n"}]) == []
    assert ima_kb.discover(["  "], kb_list=[{"id": "k", "name": "n"}]) == []


def test_resolve_kb_ids_from_env(monkeypatch):
    monkeypatch.setenv("IMA_KB_IDS", "kb1, kb2")
    monkeypatch.setattr(ima_kb, "_post",
                        lambda ep, body, **k: {"infos": {"kb1": {"name": "库一"}}}
                        if ep == "get_knowledge_base" else None)
    out = ima_kb.resolve_kb_ids()
    assert out == [{"id": "kb1", "name": "库一"}, {"id": "kb2", "name": ""}]


def test_write_to_library(tmp_path):
    results = [
        {"title": "金刚石散热报告", "media_type": 1, "highlight_content": "",
         "kb_name": "财经资讯", "matched_query": ["黄河旋风", "金刚石"]},
        {"title": "带片段条目", "media_type": 9, "highlight_content": "命中片段X",
         "kb_name": "财经资讯", "matched_query": ["金刚石"]},
    ]
    path = ima_kb.write_to_library("600172", results, lib_dir=str(tmp_path))
    assert path
    p = tmp_path / "600172"
    files = list(p.glob("ima_titles_*.md"))      # 文件名已改为 ima_titles_*
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "金刚石散热报告" in text
    assert "[PDF]" in text and "[图片]" in text   # media_type 标签
    assert "非内容本身" in text                    # 边界声明在（标题线索，非内容）
    assert "命中词 黄河旋风/金刚石" in text
    assert "片段: 命中片段X" in text               # 有 highlight 时也带上


def test_write_to_library_empty(tmp_path):
    assert ima_kb.write_to_library("600172", [], lib_dir=str(tmp_path)) == ""


def test_post_failopen_when_unconfigured(monkeypatch):
    monkeypatch.setenv("IMA_OPENAPI_APIKEY", "")
    assert ima_kb._post("search_knowledge", {"query": "x"}) is None


# ---- 真实响应字段解析（之前漏测、导致 bug 的层）：直接打桩 requests.post ----

class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _patch_requests(monkeypatch, payload):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(payload))


def test_post_parses_code_not_retcode(monkeypatch):
    # 实测响应是 {code, msg}，非手册的 {retcode, errmsg}
    _patch_requests(monkeypatch, {"code": 0, "msg": "success", "data": {"x": 1}})
    assert ima_kb._post("any", {}) == {"x": 1}
    # code 非 0 -> 失败返回 None（旧代码会因检查 retcode 而误当成功）
    _patch_requests(monkeypatch, {"code": 51, "msg": "invalid", "data": {}})
    assert ima_kb._post("any", {}) is None


def test_list_kb_real_fields_and_limit_clamp(monkeypatch):
    captured = {}

    def fake_post(url, json=None, **k):
        captured["limit"] = json.get("limit")
        return _FakeResp({"code": 0, "data": {"info_list": [
            {"kb_id": "ID1", "kb_name": "共享库", "base_type": "共享知识库"},
            {"id": "ID2", "name": "老字段兜底"},
        ], "is_end": True}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = ima_kb.list_knowledge_bases(limit=50)   # 传 50 应被夹到 20
    assert captured["limit"] == 20
    assert out[0] == {"id": "ID1", "name": "共享库", "base_type": "共享知识库"}
    assert out[1]["id"] == "ID2" and out[1]["name"] == "老字段兜底"


def test_search_filters_folders_and_keeps_media_type(monkeypatch):
    _patch_requests(monkeypatch, {"code": 0, "data": {"info_list": [
        {"media_id": "folder_1", "title": "某文件夹", "media_type": 99},
        {"media_id": "pdf_1", "title": "研报A", "media_type": 1, "highlight_content": ""},
    ]}})
    out = ima_kb.search_knowledge("金刚石", "kb1", max_items=10)
    assert len(out) == 1                          # 文件夹(99)被过滤
    assert out[0]["title"] == "研报A"
    assert out[0]["media_type"] == 1
