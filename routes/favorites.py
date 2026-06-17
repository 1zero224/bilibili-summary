"""
Favorites Browser API routes.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pathlib import Path

from bilibili_api import video, user as bili_user, favorite_list

from routes.deps import (
    credential, ai_client, DATA_DIR, DEFAULT_MODEL,
    send_progress, clear_retry_count,
    process_single_video, run_batch,
    default_folder_output_subdir, resolve_folder_output_subdir,
    get_task_concurrency, register_task,
)
from summarize import sanitize_filename
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["favorites"])


class SummarizeBvidsRequest(BaseModel):
    bvids: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    output_subdir: str = "favorites"
    model: str = ""
    concurrency: int | None = Field(default=None, ge=1, le=20)
    modules: dict = Field(default_factory=dict)
    folder: str = ""


def _summary_status_for_title(title: str) -> dict:
    safe_title = sanitize_filename(title)
    summary_root = DATA_DIR / "summary"
    candidates: list[Path] = []
    if summary_root.exists():
        candidates.extend(summary_root.rglob(f"{safe_title}.md"))

    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name.endswith(".detailed.md") or path.name.endswith(".mindmap.md"):
            continue
        try:
            rel_path = str(path.relative_to(summary_root))
        except ValueError:
            continue
        status = "no_subtitle" if "no_subtitle" in path.parts else "done"
        return {
            "has_summary": True,
            "summary_status": status,
            "summary_path": rel_path,
        }

    return {"has_summary": False, "summary_status": "none", "summary_path": None}


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
            status = _summary_status_for_title(title)

            cover = m.get('cover', '') or ''
            if isinstance(cover, str) and cover.startswith('//'):
                cover = f'https:{cover}'

            videos.append({
                "bvid": bvid,
                "title": title,
                "cover": cover,
                "duration": m.get('duration', 0),
                "upper": m.get('upper', {}).get('name', ''),
                "upper_mid": m.get('upper', {}).get('mid', 0),
                "play_count": m.get('cnt_info', {}).get('play', 0),
                **status,
            })

        has_more = content.get('has_more', False)
        return {"videos": videos, "has_more": has_more, "page": page}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/favorites/summarize")
async def summarize_favorite_bvids(req: SummarizeBvidsRequest):
    """Summarize specific BVIDs from favorites."""
    import asyncio
    import time
    from routes.deps import credential, DEFAULT_MODEL

    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})
    if not req.bvids:
        return {"task_id": None, "message": "无需总结"}

    model = req.model or DEFAULT_MODEL
    task_id = f"fav-auto-{int(time.time()*1000)}"
    try:
        output_subdir, folder_name = resolve_folder_output_subdir(req.folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    register_task(
        task_id,
        "favorites",
        "收藏夹选中视频总结",
        total=len(req.bvids),
        meta={"bvids": req.bvids[:200], "folder": folder_name},
    )

    async def _run():
        await run_batch(req.bvids, model, get_task_concurrency(), output_subdir, task_id, modules=req.modules)

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


@router.post("/favorites/{fav_id}/video/{bvid}/restore")
async def restore_favorite_video(fav_id: int, bvid: str):
    """Restore a previously removed video to a favorite folder."""
    from routes.deps import credential
    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    try:
        v = video.Video(bvid=bvid, credential=credential)
        await v.set_favorite(add_media_ids=[fav_id])
        return {"success": True, "bvid": bvid, "fav_id": fav_id}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/retry/{bvid}")
async def retry_summarize(bvid: str, output_subdir: str = ""):
    """Force re-summarize a single video by deleting existing summary file.
    If output_subdir is not provided, auto-detect from existing files.
    """
    import asyncio
    import time
    from routes.deps import credential, ai_client, DEFAULT_MODEL

    if not ai_client:
        return JSONResponse(status_code=400, content={"error": "AI 未配置"})

    try:
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        title = info.get("title", bvid)
        safe_title = sanitize_filename(title)

        # Auto-detect output_subdir from existing summary files
        detected_subdir = output_subdir
        if not detected_subdir:
            summary_root = DATA_DIR / "summary"
            # Check normal summaries first, then no_subtitle
            for subdir in ["standalone", "favorites"]:
                if (summary_root / subdir / f"{safe_title}.md").exists():
                    detected_subdir = subdir
                    break
                if (summary_root / subdir / "no_subtitle" / f"{safe_title}.md").exists():
                    detected_subdir = subdir
                    break
            if not detected_subdir:
                users_dir = summary_root / "users"
                if users_dir.exists():
                    for uid_folder in users_dir.iterdir():
                        if uid_folder.is_dir():
                            if (uid_folder / f"{safe_title}.md").exists():
                                detected_subdir = f"users/{uid_folder.name}"
                                break
                            if (uid_folder / "no_subtitle" / f"{safe_title}.md").exists():
                                detected_subdir = f"users/{uid_folder.name}"
                                break
            if not detected_subdir:
                detected_subdir = default_folder_output_subdir()

        # Remove existing normal summary
        normal_path = DATA_DIR / "summary" / detected_subdir / f"{safe_title}.md"
        if normal_path.exists():
            normal_path.unlink()
            meta_json = normal_path.with_suffix(".meta.json")
            if meta_json.exists():
                meta_json.unlink(missing_ok=True)

        # Remove existing no_subtitle summary
        nosub_path = DATA_DIR / "summary" / detected_subdir / "no_subtitle" / f"{safe_title}.md"
        if nosub_path.exists():
            nosub_path.unlink()
            meta_json = nosub_path.with_suffix(".meta.json")
            if meta_json.exists():
                meta_json.unlink(missing_ok=True)

        clear_retry_count(detected_subdir, safe_title)

        task_id = f"retry-{bvid}-{int(time.time()*1000)}"

        async def _run():
            url = f"https://www.bilibili.com/video/{bvid}"
            await process_single_video(url, DEFAULT_MODEL, detected_subdir, task_id)
            await send_progress(task_id, "done", {"total": 1})

        asyncio.create_task(_run())
        return {"task_id": task_id, "title": title}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
