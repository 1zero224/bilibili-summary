"""
Favorites Browser API routes.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from bilibili_api import video, user as bili_user, favorite_list

from routes.deps import (
    credential, ai_client, DATA_DIR, DEFAULT_MODEL,
    send_progress, clear_retry_count,
    process_single_video, run_batch,
)
from summarize import sanitize_filename
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["favorites"])


class SummarizeBvidsRequest(BaseModel):
    bvids: list[str]
    output_subdir: str = "favorites"
    model: str = ""
    concurrency: int = 6


@router.get("/favorites/list")
async def list_favorites():
    """Return all favorite folders for the logged-in user."""
    from routes.deps import credential
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        me = await bili_user.get_self_info(credential)
        my_uid = me['mid']
        fav_data = await favorite_list.get_video_favorite_list(uid=my_uid, credential=credential)

        folders = []
        for f in fav_data.get('list', []):
            folders.append({
                "id": f['id'],
                "title": f['title'],
                "count": f.get('media_count', 0),
                "is_default": f.get('attr', 1) == 0,
            })
        return {"folders": folders}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/favorites/{fav_id}/videos")
async def list_favorite_videos(fav_id: int, page: int = 1):
    """Return videos in a favorite folder with cover images and summary status."""
    from routes.deps import credential
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        content = await favorite_list.get_video_favorite_list_content(
            media_id=fav_id, page=page, credential=credential
        )

        videos = []
        for m in content.get('medias', []) or []:
            bvid = m.get('bvid', '')
            title = m.get('title', '')
            safe_title = sanitize_filename(title)

            normal_path = DATA_DIR / "summary" / "favorites" / f"{safe_title}.md"
            nosub_path = DATA_DIR / "summary" / "favorites" / "no_subtitle" / f"{safe_title}.md"
            has_summary = normal_path.exists()
            has_nosub = nosub_path.exists()
            summary_path = None
            if has_summary:
                summary_path = f"favorites/{safe_title}.md"
            elif has_nosub:
                summary_path = f"favorites/no_subtitle/{safe_title}.md"

            videos.append({
                "bvid": bvid,
                "title": title,
                "cover": m.get('cover', ''),
                "duration": m.get('duration', 0),
                "upper": m.get('upper', {}).get('name', ''),
                "upper_mid": m.get('upper', {}).get('mid', 0),
                "play_count": m.get('cnt_info', {}).get('play', 0),
                "has_summary": has_summary or has_nosub,
                "summary_status": 'done' if has_summary else ('no_subtitle' if has_nosub else 'none'),
                "summary_path": summary_path,
            })

        has_more = content.get('has_more', False)
        return {"videos": videos, "has_more": has_more, "page": page}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/favorites/summarize")
async def summarize_favorite_bvids(req: SummarizeBvidsRequest):
    """Summarize specific BVIDs from favorites (auto-trigger from browse)."""
    import asyncio
    import time
    from routes.deps import credential, DEFAULT_MODEL

    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})
    if not req.bvids:
        return {"task_id": None, "message": "无需总结"}

    model = req.model or DEFAULT_MODEL
    task_id = f"fav-auto-{int(time.time()*1000)}"

    async def _run():
        await run_batch(req.bvids, model, req.concurrency, req.output_subdir, task_id)

    asyncio.create_task(_run())
    return {"task_id": task_id}


@router.delete("/favorites/{fav_id}/video/{bvid}")
async def unfavorite_video(fav_id: int, bvid: str):
    """Remove a video from a favorite folder."""
    from routes.deps import credential
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        aid = info.get("aid")
        if not aid:
            return JSONResponse(status_code=400, content={"error": "无法获取视频 AID"})

        await favorite_list.delete_video_favorite_list_content(
            media_id=fav_id, aids=[aid], credential=credential
        )
        return {"success": True, "bvid": bvid}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/retry/{bvid}")
async def retry_summarize(bvid: str, output_subdir: str = "favorites"):
    """Force re-summarize a single video by deleting existing no_subtitle file."""
    import asyncio
    import time
    from routes.deps import credential, DEFAULT_MODEL

    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        title = info.get("title", bvid)
        safe_title = sanitize_filename(title)

        nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
        if nosub_path.exists():
            nosub_path.unlink()

        clear_retry_count(output_subdir, safe_title)

        task_id = f"retry-{bvid}-{int(time.time()*1000)}"

        async def _run():
            await process_single_video(bvid, DEFAULT_MODEL, output_subdir, task_id)
            await send_progress(task_id, "done", {"total": 1})

        asyncio.create_task(_run())
        return {"task_id": task_id, "title": title}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
