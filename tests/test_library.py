# -*- coding: utf-8 -*-
"""本地资料夹注入（ashare_vendor/library.py，CLAUDE.md §11）离线测试。

覆盖 §11 的三个"错了不显眼"校验点:
  ① 简称未命中名称表 -> 静默不注入（根目录），但强绑定文件夹仍注入
  ② 缓存失效 mtime/sha1 双层（touch/改名不重摘要，内容变才重摘要）
  ③ 多股报告切片只注入当前个股

LLM 摘要与名称表均打桩，不需要网络/密钥。
"""

import os
import time

import pytest

from ashare_vendor import library


SUMMARY = {
    "theme": "Diamond cooling for AI chips",
    "native_date": "2026-06-11",
    "common_points": ["[基本面] Diamond cooling market to reach $5.4B by 2030"],
    "by_stock": {
        "黄河旋风": ["[基本面] 300 MPCVD units in 3 years, 150k wafers/yr"],
        "四方达": ["[基本面] Small-batch supply to overseas client started"],
        "神秘公司": ["[消息] Name absent from listing table"],
    },
    "mentioned_names": ["黄河旋风", "四方达", "神秘公司"],
}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRARY_DIR", str(tmp_path))
    monkeypatch.delenv("LIBRARY_ENABLED", raising=False)
    monkeypatch.setattr(
        library, "_name2code",
        lambda: {"黄河旋风": "600172", "四方达": "300179"})
    calls = {"n": 0}

    def fake_summarize(text, fname):
        calls["n"] += 1
        return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in SUMMARY.items()}

    monkeypatch.setattr(library, "_summarize_text", fake_summarize)
    return tmp_path, calls


def test_slice_only_current_stock(lib):
    """校验点③: 多股行业报告只注入当前个股的要点"""
    root, _ = lib
    (root / "report.md").write_text("dummy", encoding="utf-8")

    ctx = library.build_library_context("600172")
    assert "USER-PROVIDED LOCAL RESEARCH LIBRARY" in ctx
    assert "$5.4B" in ctx                       # 主题级要点注入
    assert "300 MPCVD" in ctx                   # 当前个股(黄河旋风)要点注入
    assert "Small-batch supply" not in ctx      # 其他个股(四方达)要点不串味


def test_cache_fast_path_and_sha1_fallback(lib):
    """校验点②: 未变不重摘要; touch/改名走 sha1 复用; 内容变才重摘要"""
    root, calls = lib
    f = root / "report.md"
    f.write_text("dummy", encoding="utf-8")

    library.build_library_context("600172")
    assert calls["n"] == 1
    library.build_library_context("600172")
    assert calls["n"] == 1                      # (size,mtime) 快路径

    t = time.time() + 60
    os.utime(f, (t, t))                         # touch: mtime 变、内容没变
    library.build_library_context("600172")
    assert calls["n"] == 1                      # sha1 兜底复用

    f.rename(root / "renamed.md")               # 改名: index 键变、内容没变
    library.build_library_context("600172")
    assert calls["n"] == 1                      # sha1 兜底复用

    (root / "renamed.md").write_text("dummy changed", encoding="utf-8")
    library.build_library_context("600172")
    assert calls["n"] == 2                      # 内容真变 -> 重摘要


def test_unmatched_name_silent_and_bound_folder_rescues(lib):
    """校验点①: 未命中代码的票根目录不注入; 强绑定文件夹仍注入"""
    root, _ = lib
    (root / "report.md").write_text("dummy", encoding="utf-8")

    # 000001(平安银行) 未被报告提及 -> 根目录资料不注入
    assert library.build_library_context("000001") == ""

    # 同样内容放进 000001/ 强绑定文件夹 -> 注入（含全部个股要点兜底）
    bound = root / "000001"
    bound.mkdir()
    (bound / "report2.md").write_text("dummy2", encoding="utf-8")
    ctx = library.build_library_context("000001")
    assert "$5.4B" in ctx
    assert "300 MPCVD" in ctx                   # 强绑定切不到名字时给全量

    # "神秘公司"不在名称表 -> 永远静默, 不报错（fail-open 行为本身即通过）


def test_macro_always_injected_common_only(lib):
    """_macro 对任何代码注入, 且只注入主题级要点"""
    root, _ = lib
    macro = root / "_macro"
    macro.mkdir()
    (macro / "policy.md").write_text("dummy", encoding="utf-8")

    ctx = library.build_library_context("000001")   # 与报告无关的票
    assert "$5.4B" in ctx                            # common_points 注入
    assert "300 MPCVD" not in ctx                    # by_stock 不注入


def test_us_ticker_bound_folder(lib):
    """非A股: 大写 ticker 文件夹强绑定生效"""
    root, _ = lib
    d = root / "AAPL"
    d.mkdir()
    (d / "note.md").write_text("dummy", encoding="utf-8")
    ctx = library.build_library_context("aapl")
    assert "$5.4B" in ctx


def test_disabled_and_missing_dir(lib, monkeypatch):
    root, _ = lib
    (root / "report.md").write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("LIBRARY_ENABLED", "false")
    assert library.build_library_context("600172") == ""

    monkeypatch.setenv("LIBRARY_ENABLED", "true")
    monkeypatch.setenv("LIBRARY_DIR", str(root / "nonexistent"))
    assert library.build_library_context("600172") == ""


def test_inspect_library_statuses(lib):
    """只读巡检：新发现/已读取/已修改 三态 + will_inject 判定 + 其它股文件夹排除。"""
    root, calls = lib
    f = root / "report.md"
    f.write_text("dummy", encoding="utf-8")
    macro = root / "_macro"
    macro.mkdir()
    (macro / "policy.md").write_text("m", encoding="utf-8")
    other = root / "300750"
    other.mkdir()
    (other / "other.md").write_text("o", encoding="utf-8")

    # 读取前：全是 new，不触发摘要（calls 不增）
    info = library.inspect_library("600172")
    assert {i["file"] for i in info["items"]} == {"report.md", str(Path("_macro") / "policy.md")} \
        or {i["file"] for i in info["items"]} == {"report.md", "_macro\\policy.md"}
    assert all(i["status"] == "new" and i["will_inject"] is None for i in info["items"])
    assert calls["n"] == 0                       # 纯只读，没触发 LLM

    # 跑一次分析注入 -> 变 cached，will_inject 按切片判定
    library.build_library_context("600172")
    info = library.inspect_library("600172")
    by_file = {i["file"]: i for i in info["items"]}
    rep = by_file["report.md"]
    assert rep["status"] == "cached" and rep["will_inject"] is True
    assert rep["theme"]                          # 元信息带出
    # 对未被提及的股票：cached 但不注入
    info_other = library.inspect_library("000001")
    rep2 = {i["file"]: i for i in info_other["items"]}["report.md"]
    assert rep2["status"] == "cached" and rep2["will_inject"] is False

    # 修改文件 -> changed
    f.write_text("dummy changed", encoding="utf-8")
    info = library.inspect_library("600172")
    assert {i["file"]: i for i in info["items"]}["report.md"]["status"] == "changed"


from pathlib import Path


def test_summarize_failure_not_cached(lib, monkeypatch):
    """LLM 失败 -> 不写缓存不写 index, 下次重试"""
    root, calls = lib
    (root / "report.md").write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(library, "_summarize_text", lambda t, f: None)
    assert library.build_library_context("600172") == ""

    # 恢复正常摘要 -> 这次成功并注入
    def ok_summarize(text, fname):
        calls["n"] += 1
        return dict(SUMMARY)
    monkeypatch.setattr(library, "_summarize_text", ok_summarize)
    ctx = library.build_library_context("600172")
    assert "300 MPCVD" in ctx
    assert calls["n"] == 1
