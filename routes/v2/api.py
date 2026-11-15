"""/api/v2 接口：字幕导入/读取、ASR 任务、历史索引、健康检查。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.deps import DATA_DIR
from routes.v2 import tasks as task_store
from routes.v2.auth import require_token
from routes.v2.store import (
    build_history,
    load_subtitle,
    normalize_segments,
    save_subtitle,
)

VERSION = "0.1.0"

# health 不要求 token（便于 curl / 探活）；其余全部要求
public_router = APIRouter(prefix="/api/v2", tags=["v2"])
router = APIRouter(prefix="/api/v2", tags=["v2"], dependencies=[Depends(require_token)])

ALLOWED_SOURCES = {"native-ai", "native-cc", "asr", "manual"}


class ImportRequest(BaseModel):
    p: int = Field(default=1, ge=1)
    cid: int | None = None
    source: str = "native-ai"
    lang: str = ""
    title: str = ""
    duration: int = 0
    segments: list[dict] = Field(default_factory=list)
    subdir: str = ""


class AsrRequest(BaseModel):
    p: int = Field(default=1, ge=1)
    cid: int | None = None
    title: str = ""
    duration: int = 0
    source_url: str = ""
    audio_url: str = ""
    audio_only: bool = True
    subdir: str = ""


@public_router.get("/health")
async def health():
    from routes.deps import ai_client, credential
    from routes.whisper import get_asr_mode, get_asr_mode_label

    mode = ""
    label = ""
    try:
        mode = get_asr_mode()
        label = get_asr_mode_label()
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "version": VERSION,
        "data_dir": str(DATA_DIR),
        "features": {
            "asr_mode": mode,
            "asr_label": label,
            "bilibili_logged_in": bool(credential),
            "ai_configured": bool(ai_client),
            "auth_required": True,
        },
    }


async def _video_info(bvid: str, page: int) -> dict:
    """优先用本地字幕记录，其次问 B 站（需要 credential）。"""
    record = load_subtitle(bvid, page)
    if record:
        return {
            "bvid": bvid,
            "page": page,
            "title": record.get("title") or bvid,
            "duration": int(record.get("duration") or 0),
            "cid": record.get("cid"),
            "cover": "",
            "author": "",
        }
    try:
        from bilibili_api import video as bili_video

        from routes.deps import credential

        v = bili_video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        pages = await v.get_pages()
        target = pages[page - 1] if len(pages) >= page else {}
        return {
            "bvid": bvid,
            "page": page,
            "title": str(info.get("title") or bvid),
            "duration": int(target.get("duration") or info.get("duration") or 0),
            "cid": target.get("cid") or info.get("cid"),
            "cover": str(info.get("pic") or ""),
            "author": str((info.get("owner") or {}).get("name") or ""),
        }
    except Exception:  # noqa: BLE001 - 未登录/网络失败时降级
        return {"bvid": bvid, "page": page, "title": bvid, "duration": 0, "cid": None, "cover": "", "author": ""}


@router.get("/videos/{bvid}")
async def get_video(bvid: str, p: int = Query(default=1, ge=1)):
    info = await _video_info(bvid, p)
    record = load_subtitle(bvid, p)
    info.update(
        {
            "has_subtitle": bool(record),
            "subtitle_source": (record or {}).get("source", ""),
            "subtitle_count": int((record or {}).get("count") or 0),
            "subtitle_updated_at": (record or {}).get("updated_at"),
        }
    )
    return info


def _legacy_record_from_ass(bvid: str, page: int | None) -> dict | None:
    """旧流程只落了 .ass（没有 subtitles/*.json）：按 md 路径推出 ass 路径再解析。"""
    try:
        from routes.deps import _ass_path_for_summary
        from routes.v2.store import parse_ass_file
        from routes.v2.summary import find_summary_meta

        found = find_summary_meta(bvid, page or 1)
        if not found:
            return None
        md_path, meta = found
        segments = parse_ass_file(_ass_path_for_summary(md_path))
        if not segments:
            return None
        return {
            "bvid": bvid,
            "page": int(page or 1),
            "cid": None,
            "source": "legacy-ass",
            "lang": "",
            "title": str(meta.get("title") or bvid),
            "duration": int(meta.get("duration") or 0),
            "cover": str(meta.get("cover_url") or ""),
            "author": str(meta.get("author_name") or ""),
            "segments": segments,
            "count": len(segments),
            "updated_at": md_path.stat().st_mtime if md_path.exists() else 0,
        }
    except Exception:  # noqa: BLE001 - 兼容路径失败就当没有
        return None


@router.get("/subtitles/{bvid}")
async def get_subtitle(bvid: str, p: int | None = Query(default=None, ge=1)):
    record = load_subtitle(bvid, p) or _legacy_record_from_ass(bvid, p)
    if not record:
        raise HTTPException(status_code=404, detail="没有该视频的字幕记录")
    return record


@router.post("/subtitles/{bvid}/import")
async def import_subtitle(bvid: str, body: ImportRequest):
    source = body.source if body.source in ALLOWED_SOURCES else "manual"
    segments = normalize_segments(body.segments)
    if not segments:
        raise HTTPException(status_code=422, detail="segments 为空或格式不合法")

    title = body.title
    duration = body.duration
    cid = body.cid
    if not title or not duration:
        info = await _video_info(bvid, body.p)
        title = title or info["title"]
        duration = duration or int(info["duration"] or 0)
        cid = cid or info.get("cid")

    path = await asyncio.to_thread(
        save_subtitle,
        bvid=bvid,
        page=body.p,
        cid=cid,
        source=source,
        lang=body.lang,
        title=title or bvid,
        duration=duration,
        segments=segments,
        extra_subdir=body.subdir,
    )
    return {
        "stored": True,
        "path": str(path),
        "count": len(segments),
        "source": source,
        "page": body.p,
    }


@router.post("/subtitles/{bvid}/asr")
async def start_asr(bvid: str, body: AsrRequest):
    info = await _video_info(bvid, body.p)
    title = body.title or info["title"]
    duration = body.duration or int(info["duration"] or 0)
    cid = body.cid or info.get("cid")
    source_url = body.source_url or f"https://www.bilibili.com/video/{bvid}"
    if body.p > 1:
        source_url = f"{source_url}?p={body.p}"

    task_id = task_store.start_asr_job(
        bvid=bvid,
        page=body.p,
        cid=cid,
        title=title,
        duration=duration,
        audio_url=body.audio_url,
        source_url=source_url,
        subdir=body.subdir or "standalone",
    )
    return {"task_id": task_id, "status": "queued", "title": title, "duration": duration}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return task


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    """SSE 进度：`state` → 若干 `update`（含心跳 `: ping`）→ `final`。"""
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        task_store.task_stream(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
async def history(
    q: str = "",
    limit: int = Query(default=60, ge=1, le=200),
    cursor: str = "",
    source: str = "",
):
    """`source`：`asr` 仅 ASR；`processed` 总结+ASR（历史页默认）；留空为全部。"""
    return build_history(query=q, limit=limit, cursor=cursor, source=source)
