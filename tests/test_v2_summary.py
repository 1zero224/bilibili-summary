"""`routes.v2.summary` 与 `routes.v2.tasks.task_stream` 的单测（重依赖全部打桩）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import v2_stub_helper  # noqa: E402

v2_stub_helper.install()  # 必须在 import routes.v2.* 之前

from routes.v2 import summary as summary_mod  # noqa: E402
from routes.v2 import tasks as task_mod  # noqa: E402


@pytest.fixture()
def summary_dir(tmp_path, monkeypatch):
    root = tmp_path / "summary"
    root.mkdir()
    monkeypatch.setattr(summary_mod, "SUMMARY_DIR", root)
    return root


def _write_summary(root: Path, folder: str, title: str, bvid: str, detailed: str = "") -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    md = d / f"{title}.md"
    md.write_text(f"# {title}\n\n## 📝 摘要\n\n本次仅生成详细总结。\n", encoding="utf-8")
    (d / f"{title}.meta.json").write_text(
        json.dumps(
            {"title": title, "bvid": bvid, "duration": 300, "author_name": "UP", "cover_url": "", "url": ""},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if detailed:
        (d / f"{title}.detailed.md").write_text(detailed, encoding="utf-8")
    return md


def test_find_summary_meta_按_bvid_命中(summary_dir):
    _write_summary(summary_dir, "默认文件夹", "甲", "BV1aaa")
    _write_summary(summary_dir, "默认文件夹", "乙", "BV1bbb")

    found = summary_mod.find_summary_meta("BV1bbb")
    assert found is not None
    md_path, meta = found
    assert md_path.name == "乙.md"
    assert meta["duration"] == 300

    assert summary_mod.find_summary_meta("BV1nope") is None


def test_read_summary_带上详细总结(summary_dir):
    _write_summary(summary_dir, "子目录", "丙", "BV1ccc", detailed="## [[时间段: 1:02-3:40]] 开场\n\n正文")

    data = summary_mod.read_summary("BV1ccc")
    assert data is not None
    assert data["has_summary"] is True
    assert data["title"] == "丙"
    assert "开场" in data["detailed"]
    assert data["path"].endswith("丙.md")

    # 没有 detailed 资产时为空串而不是报错
    _write_summary(summary_dir, "子目录", "丁", "BV1ddd")
    data2 = summary_mod.read_summary("BV1ddd")
    assert data2["detailed"] == ""


def test_read_summary_不存在返回_None(summary_dir):
    assert summary_mod.read_summary("BV1zzz") is None


def test_task_stream_已结束任务直接发_final():
    task_id = task_mod.new_task("summary", "t")
    task_mod.update(task_id, status="done", step="完成", progress=100, summary="## 总结", count=3)

    async def collect() -> list[dict]:
        events = []
        async for chunk in task_mod.task_stream(task_id):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    events = asyncio.run(collect())
    assert [e["type"] for e in events] == ["state", "final"]
    assert events[-1]["summary"] == "## 总结"
    assert events[-1]["status"] == "done"


def test_task_stream_未知任务返回_error():
    async def collect() -> list[dict]:
        events = []
        async for chunk in task_mod.task_stream("nope"):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0]["type"] == "error"


def test_task_stream_运行中任务收到_update_与_final():
    task_id = task_mod.new_task("asr", "t")

    async def runner() -> list[dict]:
        async def producer() -> None:
            await asyncio.sleep(0.05)
            task_mod.update(task_id, status="running", step="转写中", progress=45)
            await asyncio.sleep(0.05)
            task_mod.update(task_id, status="done", step="完成", progress=100, segments=[{"start": 0, "end": 1, "text": "x"}])

        task = asyncio.create_task(producer())
        events: list[dict] = []
        async for chunk in task_mod.task_stream(task_id, heartbeat=0.2):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        await task
        return events

    events = asyncio.run(runner())
    types_seen = [e["type"] for e in events]
    assert types_seen[0] == "state"
    assert "update" in types_seen
    assert types_seen[-1] == "final"
    assert events[-1]["segments"][0]["text"] == "x"
