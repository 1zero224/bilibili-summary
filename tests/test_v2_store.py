"""`routes.v2.store` 的单元测试（不依赖 fastapi / bilibili_api 等重依赖）。

做法：先给 `routes.deps` 装一个只含 DATA_DIR 的桩模块，再导入 store，
然后把 store 的 SUBTITLE_DIR / SUMMARY_DIR 指向每个用例自己的临时目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import v2_stub_helper  # noqa: E402

v2_stub_helper.install()  # 必须在 import routes.v2.* 之前

from routes.v2 import store  # noqa: E402


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    subs = tmp_path / "subtitles"
    sums = tmp_path / "summary"
    subs.mkdir()
    sums.mkdir()
    monkeypatch.setattr(store, "SUBTITLE_DIR", subs)
    monkeypatch.setattr(store, "SUMMARY_DIR", sums)
    return subs, sums


def test_normalize_segments_两种写法与过滤():
    segs = store.normalize_segments(
        [
            {"start": 2.0, "end": 3.5, "text": "  第二句 "},
            {"from": 0.5, "to": 1.2, "content": "第一句"},
            {"start": 5, "end": 5, "text": "零长度"},
            {"start": 6, "end": 7, "text": "   "},
            {"start": "bad", "end": 8, "text": "坏时间"},
            {"start": 9, "end": 10, "content": "第三句"},
        ]
    )
    assert [s["text"] for s in segs] == ["第一句", "第二句", "第三句"]
    assert segs[0] == {"start": 0.5, "end": 1.2, "text": "第一句"}


def test_normalize_segments_非列表返回空():
    assert store.normalize_segments(None) == []
    assert store.normalize_segments({"start": 1}) == []


def test_safe_name(dirs):
    assert store.safe_name('a/b:c*d?"e"') == "a_b_c_d__e_"
    assert store.safe_name("") == "untitled"
    assert len(store.safe_name("x" * 200, limit=20)) == 20


def test_save_and_load_subtitle(dirs):
    subs, _ = dirs
    segs = [
        {"start": 0.5, "end": 1.2, "text": "第一句"},
        {"start": 1.2, "end": 2.0, "text": "第二句"},
    ]
    path = store.save_subtitle(
        bvid="BV1xx411c7mD",
        page=2,
        cid=123456,
        source="native-ai",
        lang="ai-zh",
        title="测试标题",
        duration=600,
        segments=segs,
    )
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert payload["page"] == 2
    assert payload["segments"][0]["text"] == "第一句"

    # 没有 .ass 依赖（summarize 未安装时 save_subtitle 内部会静默跳过）
    assert not (Path(str(path)).parent / ".." / "ass").exists()

    loaded = store.load_subtitle("BV1xx411c7mD", 2)
    assert loaded and loaded["source"] == "native-ai"

    # page 不匹配时取不到
    assert store.load_subtitle("BV1xx411c7mD", 1) is None
    # bvid 不存在
    assert store.load_subtitle("BV1nope0000000", 1) is None


def test_load_subtitle_来源优先级(dirs):
    subs, _ = dirs
    base = dict(bvid="BV1aa411c7mD", page=1, cid=1, title="t", duration=10, segments=[{"start": 0, "end": 1, "text": "x"}])
    store.save_subtitle(source="asr", **base)
    store.save_subtitle(source="native-ai", **base)
    store.save_subtitle(source="native-cc", **base)
    loaded = store.load_subtitle("BV1aa411c7mD", 1)
    assert loaded["source"] == "native-cc"


def test_build_history_合并总结与字幕(dirs):
    subs, sums = dirs

    folder = sums / "默认文件夹"
    folder.mkdir(parents=True)
    md = folder / "有总结的视频.md"
    md.write_text("## 摘要\n", encoding="utf-8")
    (folder / "有总结的视频.meta.json").write_text(
        json.dumps(
            {
                "title": "有总结的视频",
                "bvid": "BV1sum000001",
                "duration": 300,
                "author_name": "UP主",
                "cover_url": "",
                "url": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store.save_subtitle(
        bvid="BV1sub000001",
        page=1,
        cid=2,
        source="asr",
        lang="",
        title="只有字幕的视频",
        duration=120,
        segments=[{"start": 0, "end": 1, "text": "hi"}],
    )
    # 同一 bvid 既有总结又有字幕
    store.save_subtitle(
        bvid="BV1sum000001",
        page=1,
        cid=3,
        source="native-ai",
        lang="ai-zh",
        title="有总结的视频",
        duration=300,
        segments=[{"start": 0, "end": 1, "text": "yo"}],
    )

    data = store.build_history()
    assert data["total"] == 2
    by_bvid = {item["bvid"]: item for item in data["items"]}

    both = by_bvid["BV1sum000001"]
    assert both["has_summary"] is True
    assert both["has_subtitle"] is True
    assert both["subtitle_source"] == "native-ai"
    assert both["folder"] == "默认文件夹"

    only_sub = by_bvid["BV1sub000001"]
    assert only_sub["has_summary"] is False
    assert only_sub["has_subtitle"] is True

    # 搜索
    assert store.build_history(query="只有字幕")["total"] == 1
    assert store.build_history(query="BV1sum")["total"] == 1
    assert store.build_history(query="UP主")["total"] == 1
    assert store.build_history(query="不存在的关键词")["total"] == 0

    # 分页
    page1 = store.build_history(limit=1)
    assert len(page1["items"]) == 1
    assert page1["next_cursor"] == "1"
    page2 = store.build_history(limit=1, cursor=page1["next_cursor"])
    assert len(page2["items"]) == 1
    assert page2["items"][0]["bvid"] != page1["items"][0]["bvid"]
    assert page2["next_cursor"] == ""


def test_build_history_按_source_过滤(dirs):
    """历史页只要 ASR 记录：不能混入原生字幕与非 asr 的总结记录。"""
    subs, sums = dirs

    folder = sums / "默认文件夹"
    folder.mkdir(parents=True)
    (folder / "只有总结.md").write_text("## 摘要", encoding="utf-8")
    (folder / "只有总结.meta.json").write_text(
        json.dumps({"title": "只有总结", "bvid": "BV1sum999", "duration": 60}, ensure_ascii=False),
        encoding="utf-8",
    )

    store.save_subtitle(
        bvid="BV1native1", page=1, cid=1, source="native-ai", lang="ai-zh",
        title="原生字幕", duration=30, segments=[{"start": 0, "end": 1, "text": "a"}],
    )
    store.save_subtitle(
        bvid="BV1asr0001", page=1, cid=2, source="asr", lang="",
        title="ASR 视频", duration=45, segments=[{"start": 0, "end": 1, "text": "b"}],
        cover="http://i0.hdslb.com/x.jpg", author="某UP",
    )

    all_items = store.build_history()
    assert all_items["total"] == 3

    asr_only = store.build_history(source="asr")
    assert asr_only["total"] == 1
    item = asr_only["items"][0]
    assert item["bvid"] == "BV1asr0001"
    assert item["subtitle_source"] == "asr"
    assert item["cover"] == "http://i0.hdslb.com/x.jpg"
    assert item["author"] == "某UP"


def test_build_history_processed_模式(dirs):
    """processed = 总结 + ASR；纯原生字幕（浏览过但没跑 ASR/没总结）不出现。"""
    subs, sums = dirs

    folder = sums / "默认文件夹"
    folder.mkdir(parents=True)
    (folder / "有总结.md").write_text("# 有总结", encoding="utf-8")
    (folder / "有总结.meta.json").write_text(
        json.dumps({"title": "有总结", "bvid": "BV1sum0001", "duration": 60}, ensure_ascii=False),
        encoding="utf-8",
    )

    store.save_subtitle(
        bvid="BV1native1", page=1, cid=1, source="native-ai", lang="ai-zh",
        title="只浏览过", duration=30, segments=[{"start": 0, "end": 1, "text": "a"}],
    )
    store.save_subtitle(
        bvid="BV1asr0001", page=1, cid=2, source="asr", lang="",
        title="跑过 ASR", duration=45, segments=[{"start": 0, "end": 1, "text": "b"}],
    )
    # 同一 bvid 既有总结又有原生字幕：应合并为一条，且保留总结标记
    store.save_subtitle(
        bvid="BV1sum0001", page=1, cid=3, source="native-ai", lang="ai-zh",
        title="有总结", duration=60, segments=[{"start": 0, "end": 1, "text": "c"}],
    )

    processed = store.build_history(source="processed")
    bvids = {item["bvid"] for item in processed["items"]}
    assert bvids == {"BV1sum0001", "BV1asr0001"}
    assert "BV1native1" not in bvids

    merged = next(i for i in processed["items"] if i["bvid"] == "BV1sum0001")
    assert merged["has_summary"] is True and merged["has_subtitle"] is True

    all_items = store.build_history()
    assert {i["bvid"] for i in all_items["items"]} == {"BV1sum0001", "BV1asr0001", "BV1native1"}


def test_parse_ass_file(dirs, tmp_path):
    bs = chr(92)  # 反斜杠，避免源码里的 \N 被当成 unicode 转义
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Script Info]\nTitle: t\n\n[Events]\n"
        f"Dialogue: 0,0:00:01.20,0:00:03.45,Default,,0,0,0,,{{{bs}i1}}第一句{bs}N换行\n"
        "Dialogue: 0,0:00:03.45,0:00:05.00,Default,,0,0,0,,第二句\n"
        "Dialogue: 0,0:00:05.00,0:00:05.00,Default,,0,0,0,,零长度\n",
        encoding="utf-8",
    )
    segs = store.parse_ass_file(ass)
    assert len(segs) == 2
    assert segs[0]["start"] == 1.2 and segs[0]["end"] == 3.45
    assert segs[0]["text"] == "第一句\n换行"
    assert segs[1]["text"] == "第二句"
    assert store.parse_ass_file(tmp_path / "missing.ass") == []
