"""
Whisper-based summarization routes.
"""

import json
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from bilibili_api import video

from routes.deps import (
    DATA_DIR,
    default_folder_output_subdir,
    sanitize_filename,
    save_summary_asset,
)
from routes.whisper import download_bilibili_media, get_asr_mode_label, transcribe_media
from summarize import (
    format_timestamped_transcript,
    generate_detailed_summary_with_claude,
    save_ass,
    save_summary,
)

router = APIRouter(prefix="/api", tags=["asr"])


@router.post("/asr-summarize/{bvid}")
async def asr_summarize(bvid: str, output_subdir: str = ""):
    """Download audio -> Whisper transcription -> LLM summary via SSE."""
    from routes.deps import credential, ai_client, DEFAULT_MODEL

    if not credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})
    if not ai_client:
        return JSONResponse(status_code=400, content={"error": "未配置 AI API"})

    async def event_stream():
        try:
            # Step 1: Get video info
            yield f"data: {json.dumps({'step': 'info', 'message': '获取视频信息...'})}\n\n"
            v = video.Video(bvid=bvid, credential=credential)
            info = await v.get_info()
            title = info.get("title", bvid)
            duration = info.get("duration", 0)
            cover_url = info.get("pic", "")
            owner = info.get("owner", {})
            author_name = owner.get("name", "")
            author_uid = owner.get("mid", 0)
            safe_title = sanitize_filename(title)
            url = f"https://www.bilibili.com/video/{bvid}"

            # Auto-detect output_subdir if not provided
            nonlocal output_subdir
            if not output_subdir:
                summary_root = DATA_DIR / "summary"
                for subdir in ["standalone", "favorites"]:
                    if (summary_root / subdir / f"{safe_title}.md").exists() or \
                       (summary_root / subdir / "no_subtitle" / f"{safe_title}.md").exists():
                        output_subdir = subdir
                        break
                if not output_subdir:
                    users_dir = summary_root / "users"
                    if users_dir.exists():
                        for uid_folder in users_dir.iterdir():
                            if uid_folder.is_dir():
                                if (uid_folder / f"{safe_title}.md").exists() or \
                                   (uid_folder / "no_subtitle" / f"{safe_title}.md").exists():
                                    output_subdir = f"users/{uid_folder.name}"
                                    break
                if not output_subdir:
                    output_subdir = default_folder_output_subdir()

            progress_queue: asyncio.Queue[str] = asyncio.Queue()

            async def collect_progress(message: str):
                await progress_queue.put(message)

            yield f"data: {json.dumps({'step': 'asr', 'message': f'{get_asr_mode_label()} 转录准备中'})}\n\n"
            media_rel_path = f"{output_subdir}/{safe_title}.mp4"
            media_path = DATA_DIR / "media" / media_rel_path

            await download_bilibili_media(
                bvid,
                media_path,
                expected_duration=duration,
                progress=collect_progress,
            )
            transcribe_task = asyncio.create_task(transcribe_media(
                media_path,
                duration=duration,
                progress=collect_progress,
                source_url=url,
                bvid=bvid,
                title=title,
                output_subdir=output_subdir,
                media_rel_path=media_rel_path,
            ))
            while not transcribe_task.done():
                try:
                    message = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                    yield f"data: {json.dumps({'step': 'asr', 'message': message}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    continue

            transcript, subtitle_segments = await transcribe_task
            while not progress_queue.empty():
                message = await progress_queue.get()
                yield f"data: {json.dumps({'step': 'asr', 'message': message}, ensure_ascii=False)}\n\n"

            transcript_len = len(transcript)
            yield f"data: {json.dumps({'step': 'transcribed', 'message': f'转录完成 ({transcript_len} 字)'})}\n\n"

            # Step 5: LLM detailed summarization
            yield f"data: {json.dumps({'step': 'summarize', 'message': '生成详细总结中...'})}\n\n"
            detailed_summary, llm_time = await generate_detailed_summary_with_claude(
                transcript=format_timestamped_transcript(subtitle_segments) or transcript,
                title=title,
                client=ai_client,
                model=DEFAULT_MODEL,
            )

            # Step 6: Save result
            nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
            if nosub_path.exists():
                nosub_path.unlink()

            save_ass(title, subtitle_segments, output_subdir)
            save_summary(
                title=title, bvid=bvid, url=url, duration=duration,
                summary="本次任务仅生成详细总结，请在详情页查看“详细总结”。",
                output_subdir=output_subdir,
                author_name=author_name, author_uid=author_uid,
                cover_url=cover_url,
                media_path=media_rel_path,
            )
            summary_path = DATA_DIR / "summary" / output_subdir / f"{safe_title}.md"
            save_summary_asset(summary_path, "detailed_summary", detailed_summary)

            new_path = f"{output_subdir}/{safe_title}.md"
            yield f"data: {json.dumps({'step': 'done', 'message': '详细总结完成!', 'path': new_path, 'llm_time': round(llm_time, 1)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
