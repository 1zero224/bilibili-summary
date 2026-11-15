"""
/v2 任务：内存态任务表 + ASR 作业。

- 只保留最近 ``MAX_TASKS`` 条，进程重启即丢（历史以文件系统为准，任务只是瞬态）。
- 作业内部复用既有管线：``routes.v2.audio.prepare_audio`` → ``routes.whisper.transcribe_media``。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from routes.deps import DATA_DIR
from routes.v2.audio import download_full_media, prepare_audio
from routes.v2.store import normalize_segments, safe_name, save_subtitle

MAX_TASKS = 200
_MAX_AGE_SECONDS = 6 * 3600

_TASKS: dict[str, dict] = {}
_RUNNING: dict[str, asyncio.Task] = {}
_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = {}


def _snapshot(task: dict) -> dict:
    return {
        "task_id": task["task_id"],
        "kind": task.get("kind"),
        "title": task.get("title"),
        "status": task.get("status"),
        "step": task.get("step"),
        "progress": task.get("progress", 0),
        "count": task.get("count", 0),
        "error": task.get("error"),
        "updated_at": task.get("updated_at"),
    }


def _publish(task_id: str, payload: dict) -> None:
    for queue in list(_SUBSCRIBERS.get(task_id, ())):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def subscribe(task_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _SUBSCRIBERS.setdefault(task_id, set()).add(queue)
    return queue


def unsubscribe(task_id: str, queue: asyncio.Queue) -> None:
    subscribers = _SUBSCRIBERS.get(task_id)
    if not subscribers:
        return
    subscribers.discard(queue)
    if not subscribers:
        _SUBSCRIBERS.pop(task_id, None)


def new_task(kind: str, title: str, meta: dict | None = None) -> str:
    prune()
    task_id = uuid.uuid4().hex[:16]
    _TASKS[task_id] = {
        "task_id": task_id,
        "kind": kind,
        "title": title,
        "status": "queued",
        "step": "排队中",
        "progress": 0,
        "created_at": time.time(),
        "updated_at": time.time(),
        "segments": None,
        "count": 0,
        "error": None,
        "meta": meta or {},
    }
    return task_id


def update(task_id: str, **fields: Any) -> None:
    task = _TASKS.get(task_id)
    if not task:
        return
    task.update(fields)
    task["updated_at"] = time.time()
    try:
        _publish(task_id, _snapshot(task))
    except Exception:  # noqa: BLE001 - 推送失败不影响任务本身
        pass


def get(task_id: str) -> dict | None:
    return _TASKS.get(task_id)


def prune() -> None:
    now = time.time()
    stale = [tid for tid, t in _TASKS.items() if now - float(t.get("updated_at") or 0) > _MAX_AGE_SECONDS]
    for tid in stale:
        _TASKS.pop(tid, None)
    if len(_TASKS) > MAX_TASKS:
        ordered = sorted(_TASKS.items(), key=lambda kv: float(kv[1].get("updated_at") or 0))
        for tid, _ in ordered[: len(_TASKS) - MAX_TASKS]:
            _TASKS.pop(tid, None)


async def _run_asr(
    task_id: str,
    *,
    bvid: str,
    page: int,
    cid: int | None,
    title: str,
    duration: int,
    audio_url: str,
    source_url: str,
    subdir: str,
) -> None:
    from routes.whisper import transcribe_media  # 延迟导入：避免启动即拉 faster-whisper

    update(task_id, status="running", step="准备音频", progress=5)
    name = safe_name(title or bvid)
    media_path = DATA_DIR / "media" / f"{subdir}/{name}.m4a"

    try:
        try:
            audio_path, via = await prepare_audio(
                bvid=bvid,
                page_index=max(0, page - 1),
                audio_url=audio_url,
                out_path=media_path,
            )
        except Exception as audio_error:  # noqa: BLE001 - 纯音频失败 → 整段视频兜底
            update(task_id, step=f"纯音频失败，改下整段视频（{str(audio_error)[:80]}）", progress=12)
            video_path = DATA_DIR / "media" / f"{subdir}/{name}.mp4"
            audio_path = await download_full_media(bvid, max(0, page - 1), video_path, duration=duration)
            via = "server-full-media"
        update(task_id, step=f"音频就绪（{via}，{audio_path.stat().st_size // 1024} KB）", progress=20)

        async def on_progress(message: str) -> None:
            update(task_id, step=str(message)[:200], progress=45)

        transcript, raw_segments = await transcribe_media(
            audio_path,
            duration=duration,
            progress=on_progress,
            source_url=source_url,
            bvid=bvid,
            title=title or bvid,
            output_subdir=subdir,
            media_rel_path=f"{subdir}/{audio_path.name}",
        )
        segments = normalize_segments(raw_segments)
        if not segments:
            raise RuntimeError("转录结果为空")

        update(task_id, step="保存字幕", progress=90)
        cover, author = "", ""
        try:
            from bilibili_api import video as bili_video

            from routes.deps import credential

            info = await bili_video.Video(bvid=bvid, credential=credential).get_info()
            cover = str(info.get("pic") or "")
            author = str((info.get("owner") or {}).get("name") or "")
            title = title or str(info.get("title") or bvid)
            duration = duration or int(info.get("duration") or 0)
        except Exception:  # noqa: BLE001 - 拿不到封面不影响主流程
            pass

        await asyncio.to_thread(
            save_subtitle,
            bvid=bvid,
            page=page,
            cid=cid,
            source="asr",
            lang="",
            title=title or bvid,
            duration=duration,
            segments=segments,
            extra_subdir=subdir,
            cover=cover,
            author=author,
        )
        update(
            task_id,
            status="done",
            step=f"完成（{len(segments)} 条，{len(transcript or '')} 字）",
            progress=100,
            segments=segments,
            count=len(segments),
        )
    except Exception as exc:  # noqa: BLE001 - 任务失败要落状态而不是抛到事件循环
        update(task_id, status="failed", step="失败", error=str(exc)[:500], progress=100)
    finally:
        _RUNNING.pop(task_id, None)


def start_asr_job(**kwargs: Any) -> str:
    task_id = new_task(
        "asr",
        kwargs.get("title") or kwargs.get("bvid") or "asr",
        meta={"bvid": kwargs.get("bvid"), "page": kwargs.get("page")},
    )
    job = asyncio.create_task(_run_asr(task_id, **kwargs))
    _RUNNING[task_id] = job
    return task_id


# ---------------------------------------------------------------------------
# 总结任务
# ---------------------------------------------------------------------------
async def _run_summary(
    task_id: str,
    *,
    bvid: str,
    page: int,
    title: str,
    duration: int,
    subdir: str,
    model: str,
    kind: str,
) -> None:
    from routes.deps import ai_client, save_summary_asset, summary_asset_path  # 延迟导入
    from routes.v2.store import load_subtitle
    from summarize import (
        format_timestamped_transcript,
        generate_detailed_summary_with_claude,
        sanitize_filename,
        save_summary,
    )

    try:
        update(task_id, status="running", step="读取字幕", progress=10)
        record = load_subtitle(bvid, page)
        segments = (record or {}).get("segments") or []
        if not segments:
            raise RuntimeError("该视频还没有字幕，请先抓取或转写字幕")
        title = title or (record or {}).get("title") or bvid
        duration = duration or int((record or {}).get("duration") or 0)

        transcript = format_timestamped_transcript(segments)
        if not transcript:
            raise RuntimeError("字幕内容为空，无法生成总结")
        if not ai_client:
            raise RuntimeError("后端未配置 AI 客户端（请检查 .env.local）")

        update(task_id, step=f"调用模型生成{'详细总结' if kind == 'detailed' else '总结'}", progress=35)
        content, elapsed = await generate_detailed_summary_with_claude(
            transcript,
            title,
            ai_client,
            model=model or None,
        )
        if not content or not content.strip():
            raise RuntimeError("模型返回为空")

        update(task_id, step="保存总结文件", progress=90)
        url = f"https://www.bilibili.com/video/{bvid}" + (f"?p={page}" if page > 1 else "")

        def _save() -> str:
            save_summary(
                title=title,
                bvid=bvid,
                url=url,
                duration=duration,
                summary="本次仅生成详细总结，请在面板「总结」页或 Web UI 详情页查看。",
                output_subdir=subdir or "standalone",
            )
            md_path = DATA_DIR / "summary" / (subdir or "standalone") / f"{sanitize_filename(title)}.md"
            save_summary_asset(md_path, "detailed_summary", content)
            return str(summary_asset_path(md_path, "detailed_summary"))

        asset_path = await asyncio.to_thread(_save)
        update(
            task_id,
            status="done",
            step=f"完成（{len(content)} 字，{elapsed:.1f}s）",
            progress=100,
            summary=content,
            path=asset_path,
            count=len(segments),
        )
    except Exception as exc:  # noqa: BLE001
        update(task_id, status="failed", step="失败", error=str(exc)[:500], progress=100)
    finally:
        _RUNNING.pop(task_id, None)


def start_summary_job(**kwargs: Any) -> str:
    task_id = new_task(
        "summary",
        kwargs.get("title") or kwargs.get("bvid") or "summary",
        meta={"bvid": kwargs.get("bvid"), "page": kwargs.get("page")},
    )
    job = asyncio.create_task(_run_summary(task_id, **kwargs))
    _RUNNING[task_id] = job
    return task_id


# ---------------------------------------------------------------------------
# SSE 订阅：GET /api/v2/tasks/{id}/stream
# ---------------------------------------------------------------------------
async def task_stream(task_id: str, heartbeat: float = 15.0):
    """产出 SSE 文本片段（data/心跳），任务结束时发 final 事件后停止。"""

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    task = get(task_id)
    if not task:
        yield sse({"type": "error", "task_id": task_id, "error": "任务不存在或已过期"})
        return

    queue = subscribe(task_id)
    try:
        yield sse({"type": "state", **_snapshot(task)})
        if task["status"] in ("done", "failed"):
            yield sse({"type": "final", **_snapshot(task), "segments": task.get("segments"), "summary": task.get("summary")})
            return
        while True:
            current = get(task_id)
            if not current:
                yield sse({"type": "error", "task_id": task_id, "error": "任务已被清理"})
                return
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield sse({"type": "update", **payload})
            if payload.get("status") in ("done", "failed"):
                latest = get(task_id) or {}
                yield sse(
                    {
                        "type": "final",
                        **_snapshot(latest),
                        "segments": latest.get("segments"),
                        "summary": latest.get("summary"),
                    }
                )
                return
    finally:
        unsubscribe(task_id, queue)
