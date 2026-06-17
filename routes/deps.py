"""
Shared state and dependencies for all route modules.
"""

import os
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv, set_key
import anthropic
from bilibili_api import video
from bilibili_api.utils.network import Credential

from summarize import (
    extract_bvid, save_ass, save_summary,
    sanitize_filename, generate_detailed_summary_with_claude,
    format_timestamped_transcript,
)
from routes.whisper import download_bilibili_media, transcribe_media


# ---------------------------------------------------------------------------
# Path resolution (supports PyInstaller bundle)
# ---------------------------------------------------------------------------
BUNDLE_DIR = Path(os.environ.get('BILISUMMARY_BUNDLE_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_DIR = Path(os.environ.get('BILISUMMARY_DATA_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

load_dotenv(str(DATA_DIR / '.env.local'))


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
credential: Optional[Credential] = None
ai_client: Optional[anthropic.AsyncAnthropic] = None
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "mimo-v2.5-pro")
DEFAULT_GENERATION_MODULES = {
    "summary": False,
    "detailed_summary": True,
}
FOLDER_ROOT_SUBDIR = "folders"
DEFAULT_FOLDER_NAME = "默认文件夹"
TASK_LOG_LIMIT = 200
TASK_LOG_EVENT_LIMIT = 600
TASK_LOG_LIST_EVENT_LIMIT = 20


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


TASK_CONCURRENCY = _env_int("TASK_CONCURRENCY", 12, 1, 20)


def clean_env_value(value: Optional[str]) -> str:
    return (value or "").strip().strip("'\"")


def init_credential():
    global credential
    sessdata = clean_env_value(os.getenv('BILIBILI_SESSION_TOKEN'))
    bili_jct = clean_env_value(os.getenv('BILIBILI_BILI_JCT'))
    ac_time_value = clean_env_value(os.getenv('BILIBILI_AC_TIME_VALUE'))
    if sessdata and bili_jct:
        credential = Credential(sessdata=sessdata, bili_jct=bili_jct, ac_time_value=ac_time_value or "")
        return True
    return False


def init_ai_client():
    global ai_client
    base_url = clean_env_value(os.getenv('ANTHROPIC_BASE_URL'))
    auth_token = clean_env_value(os.getenv('ANTHROPIC_AUTH_TOKEN')) or clean_env_value(os.getenv('MIMO_API_KEY'))
    if 'xiaomimimo.com' in base_url:
        ai_client = anthropic.AsyncAnthropic(base_url=base_url, auth_token=auth_token)
    else:
        ai_client = anthropic.AsyncAnthropic(base_url=base_url, api_key=auth_token)


def get_task_concurrency() -> int:
    return max(1, min(20, int(TASK_CONCURRENCY or 12)))


def set_task_concurrency(value: int):
    global TASK_CONCURRENCY
    TASK_CONCURRENCY = max(1, min(20, int(value or 12)))


def normalize_generation_modules(value: dict | None = None) -> dict:
    modules = DEFAULT_GENERATION_MODULES.copy()
    if isinstance(value, dict):
        modules["detailed_summary"] = bool(value.get("detailed_summary", modules["detailed_summary"]))
    modules["summary"] = False
    if not modules["detailed_summary"]:
        modules["detailed_summary"] = True
    return modules


def normalize_folder_name(name: str) -> str:
    cleaned = sanitize_filename(str(name or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("文件夹名称不能为空")
    if cleaned in {".", "..", "no_subtitle"}:
        raise ValueError("文件夹名称无效")
    return cleaned[:80]


def folder_output_subdir(name: str) -> str:
    return f"{FOLDER_ROOT_SUBDIR}/{normalize_folder_name(name)}"


def default_folder_name() -> str:
    return DEFAULT_FOLDER_NAME


def default_folder_output_subdir() -> str:
    return folder_output_subdir(DEFAULT_FOLDER_NAME)


def resolve_folder_output_subdir(name: str = "") -> tuple[str, str]:
    folder_name = normalize_folder_name(name or DEFAULT_FOLDER_NAME)
    return folder_output_subdir(folder_name), folder_name


def summary_asset_path(summary_path: Path, asset: str) -> Path:
    suffixes = {
        "detailed_summary": ".detailed.md",
    }
    suffix = suffixes.get(asset)
    if not suffix:
        raise ValueError(f"未知资产类型: {asset}")
    return summary_path.with_name(f"{summary_path.stem}{suffix}")


def save_summary_asset(summary_path: Path, asset: str, content: str):
    path = summary_asset_path(summary_path, asset)
    path.write_text(content or "", encoding="utf-8")


_ASS_DIALOGUE_RE = re.compile(r"^Dialogue:\s*(.*)$")


def _strip_ass_text(text: str) -> str:
    cleaned = re.sub(r"\{[^}]*\}", "", text)
    cleaned = cleaned.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    return cleaned.strip()


def _ass_path_for_summary(summary_path: Path) -> Path:
    summary_root = (DATA_DIR / "summary").resolve()
    rel = summary_path.resolve(strict=False).relative_to(summary_root)
    parts = rel.parts
    if "no_subtitle" in parts:
        parts = tuple(part for part in parts if part != "no_subtitle")
    return (DATA_DIR / "ass" / Path(*parts)).with_suffix(".ass")


def _transcript_text_for_summary(summary_path: Path, with_timestamps: bool = False) -> str:
    ass_path = _ass_path_for_summary(summary_path)
    if ass_path.is_file():
        lines = []
        segments = []
        for line in ass_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = _ASS_DIALOGUE_RE.match(line)
            if not match:
                continue
            fields = match.group(1).split(",", 9)
            if len(fields) >= 10:
                text = _strip_ass_text(fields[9])
                if text:
                    lines.append(text)
                    segments.append({
                        "from": _parse_ass_time(fields[1]),
                        "to": _parse_ass_time(fields[2]),
                        "content": text,
                    })
        if with_timestamps:
            timestamped = format_timestamped_transcript(segments)
            if timestamped:
                return timestamped
        transcript = "\n".join(lines).strip()
        if transcript:
            return transcript
    return summary_path.read_text(encoding="utf-8", errors="ignore")


def _parse_ass_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        return 0
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# SSE Progress (event-history based, supports reconnection)
# ---------------------------------------------------------------------------
progress_tasks: dict[str, dict] = {}
task_logs: list[dict] = []
progress_subscribers: dict[str, set[asyncio.Queue]] = {}


def _task_log_path() -> Path:
    return DATA_DIR / "task_logs.json"


def _safe_int(value, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_safe(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _normalize_task_event(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    message = str(raw.get("message") or "").strip()
    event = str(raw.get("event") or "info").strip() or "info"
    if not message and not raw.get("data"):
        return None
    return {
        "event": event,
        "title": str(raw.get("title") or ""),
        "message": message,
        "time": _safe_float(raw.get("time"), time.time()) or time.time(),
        "data": _json_safe(raw.get("data", {})),
    }


def _normalize_task_log(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    task_id = str(raw.get("task_id") or "").strip()
    if not task_id:
        return None

    now = time.time()
    events = []
    for event in raw.get("events") or []:
        normalized = _normalize_task_event(event)
        if normalized:
            events.append(normalized)
    events = events[-TASK_LOG_EVENT_LIMIT:]

    return {
        "task_id": task_id,
        "type": str(raw.get("type") or ""),
        "title": str(raw.get("title") or task_id),
        "status": str(raw.get("status") or "queued"),
        "total": _safe_int(raw.get("total")),
        "completed": _safe_int(raw.get("completed")),
        "success": _safe_int(raw.get("success")),
        "skipped": _safe_int(raw.get("skipped")),
        "no_subtitle": _safe_int(raw.get("no_subtitle")),
        "errors": _safe_int(raw.get("errors")),
        "created_at": _safe_float(raw.get("created_at"), now) or now,
        "updated_at": _safe_float(raw.get("updated_at"), now) or now,
        "finished_at": _safe_float(raw.get("finished_at")),
        "events": events,
        "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    }


def _persist_task_logs():
    try:
        path = _task_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        payload = json.dumps(task_logs[:TASK_LOG_LIMIT], ensure_ascii=False, indent=2)
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        pass


def load_task_logs():
    global task_logs
    path = _task_log_path()
    if not path.exists():
        task_logs = []
        return

    try:
        raw_logs = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        task_logs = []
        return

    loaded = []
    for raw in raw_logs if isinstance(raw_logs, list) else []:
        normalized = _normalize_task_log(raw)
        if normalized:
            loaded.append(normalized)

    loaded.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    task_logs = loaded[:TASK_LOG_LIMIT]

    changed = False
    now = time.time()
    for log in task_logs:
        if log.get("status") in {"queued", "running"}:
            log["status"] = "failed"
            log["updated_at"] = now
            log["finished_at"] = log.get("finished_at") or now
            log["events"].append({
                "event": "error",
                "title": "",
                "message": "服务重启，任务未完成，已标记为失败",
                "time": now,
                "data": {},
            })
            log["events"] = log["events"][-TASK_LOG_EVENT_LIMIT:]
            changed = True

    if changed:
        _persist_task_logs()


def _ensure_task(task_id: str):
    if task_id not in progress_tasks:
        progress_tasks[task_id] = {
            "events": [],
            "notify": asyncio.Event(),
            "done": False,
        }


def register_task(task_id: str, task_type: str, title: str, total: int = 0, meta: dict | None = None):
    _ensure_task(task_id)
    now = time.time()
    existing = next((item for item in task_logs if item["task_id"] == task_id), None)
    payload = {
        "task_id": task_id,
        "type": task_type,
        "title": title,
        "status": "queued",
        "total": max(0, int(total or 0)),
        "completed": 0,
        "success": 0,
        "skipped": 0,
        "no_subtitle": 0,
        "errors": 0,
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "events": [],
        "meta": meta or {},
    }
    if existing:
        existing.update(payload)
    else:
        task_logs.insert(0, payload)
        del task_logs[TASK_LOG_LIMIT:]
    _persist_task_logs()


def _task_log_for(task_id: str) -> dict | None:
    return next((item for item in task_logs if item["task_id"] == task_id), None)


def _append_task_log_event(log: dict, event: str, data: dict):
    title = data.get("title") or data.get("bvid") or ""
    message = data.get("message") or data.get("step") or ""
    if event == "start":
        message = f"开始处理 {data.get('total', log.get('total', 0))} 个视频"
    elif event == "completed":
        message = f"完成: {title}"
    elif event == "skip":
        message = f"跳过: {title}"
    elif event == "done":
        message = "任务完成"
    elif event == "info":
        message = data.get("message", "")
    elif event == "processing":
        message = f"{title} - {message}".strip(" -")
    elif event == "error":
        message = f"{title} {data.get('message', '')}".strip()

    if not message:
        return

    log["events"].append({
        "event": event,
        "title": title,
        "message": message,
        "time": time.time(),
        "data": _json_safe(data),
    })
    if len(log["events"]) > TASK_LOG_EVENT_LIMIT:
        del log["events"][:len(log["events"]) - TASK_LOG_EVENT_LIMIT]


def _update_task_log(task_id: str, event: str, data: dict):
    log = _task_log_for(task_id)
    if not log:
        return

    now = time.time()
    log["updated_at"] = now

    if event == "start":
        log["status"] = "running"
        log["total"] = max(log.get("total", 0), int(data.get("total", 0) or 0))
        log["meta"].update({
            "concurrency": data.get("concurrency"),
            "model": data.get("model"),
            "modules": data.get("modules"),
        })
    elif event in {"completed", "skip", "error"}:
        log["completed"] = min(log.get("total", 0) or 10**9, log.get("completed", 0) + 1)
        if event == "completed":
            status = data.get("status")
            if status == "no_subtitle":
                log["no_subtitle"] += 1
            else:
                log["success"] += 1
        elif event == "skip":
            log["skipped"] += 1
        else:
            log["errors"] += 1
    elif event == "done":
        log["status"] = "failed" if data.get("errors", 0) and not data.get("success", 0) and not data.get("skipped", 0) else "done"
        log["finished_at"] = now
        for key in ("total", "success", "skipped", "no_subtitle", "errors"):
            if key in data:
                log[key] = data[key]
        log["completed"] = sum(int(log.get(key, 0) or 0) for key in ("success", "skipped", "no_subtitle", "errors"))

    _append_task_log_event(log, event, data)
    _persist_task_logs()


def _task_progress_percent(item: dict) -> int:
    total = _safe_int(item.get("total"))
    if total <= 0:
        return 100 if item.get("status") == "done" else 0
    completed = min(total, _safe_int(item.get("completed")))
    return max(0, min(100, round((completed / total) * 100)))


def _public_task_log(item: dict, event_limit: int | None = TASK_LOG_LIST_EVENT_LIMIT) -> dict:
    events = item.get("events", [])
    if event_limit is not None:
        events = events[-event_limit:]
    return {
        key: item.get(key)
        for key in (
            "task_id", "type", "title", "status", "total", "completed",
            "success", "skipped", "no_subtitle", "errors",
            "created_at", "updated_at", "finished_at", "meta",
        )
    } | {
        "events": events,
        "progress_percent": _task_progress_percent(item),
    }


def list_task_logs(task_type: str = "", page: int = 1, page_size: int = 10) -> dict:
    filtered = [item for item in task_logs if not task_type or item.get("type") == task_type]
    total = len(filtered)
    page_size = max(1, min(50, int(page_size or 10)))
    page = max(1, int(page or 1))
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": [_public_task_log(item) for item in filtered[start:end]],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": end < total,
    }


def get_task_log_detail(task_id: str) -> dict | None:
    log = _task_log_for(task_id)
    if not log:
        return None
    return _public_task_log(log, event_limit=None)


def subscribe_progress(task_id: str) -> asyncio.Queue:
    _ensure_task(task_id)
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    progress_subscribers.setdefault(task_id, set()).add(queue)
    return queue


def unsubscribe_progress(task_id: str, queue: asyncio.Queue):
    queues = progress_subscribers.get(task_id)
    if not queues:
        return
    queues.discard(queue)
    if not queues:
        progress_subscribers.pop(task_id, None)


async def send_progress(task_id: str, event: str, data: dict):
    _ensure_task(task_id)
    task = progress_tasks[task_id]
    task["events"].append({"event": event, "data": data})
    _update_task_log(task_id, event, data)
    for queue in list(progress_subscribers.get(task_id, set())):
        try:
            queue.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            pass
    if event == "done":
        task["done"] = True
        asyncio.get_event_loop().call_later(300, lambda: progress_tasks.pop(task_id, None))
    task["notify"].set()


async def progress_generator(task_id: str, last_id: int = -1):
    _ensure_task(task_id)
    cursor = last_id + 1

    while True:
        task = progress_tasks.get(task_id)
        if not task:
            break

        while cursor < len(task["events"]):
            msg = task["events"][cursor]
            yield f"id: {cursor}\nevent: {msg['event']}\ndata: {json.dumps(msg['data'], ensure_ascii=False)}\n\n"
            if msg["event"] == "done":
                return
            cursor += 1

        if task["done"]:
            break

        task["notify"].clear()
        try:
            await asyncio.wait_for(task["notify"].wait(), timeout=15)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"


def _retries_file(output_subdir: str) -> Path:
    return DATA_DIR / "summary" / output_subdir / "no_subtitle" / ".retries.json"


def clear_retry_count(output_subdir: str, safe_title: str):
    path = _retries_file(output_subdir)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        data.pop(safe_title, None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core processing (with progress callbacks)
# ---------------------------------------------------------------------------
def _page_index_from_url(url: str) -> int:
    try:
        p_values = parse_qs(urlparse(url).query).get("p")
        if not p_values:
            return 0
        return max(int(p_values[0]) - 1, 0)
    except (TypeError, ValueError):
        return 0


def _target_to_url(target: str) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        return target
    return f"https://www.bilibili.com/video/{target}"


async def process_single_video(
    url: str,
    model: str,
    output_subdir: str,
    task_id: str,
    modules: dict | None = None,
):
    """Process one video and send progress events."""
    modules = normalize_generation_modules(modules)
    bvid = extract_bvid(url)
    if not bvid:
        await send_progress(task_id, "error", {"message": f"无法提取 BV 号: {url}"})
        return None

    try:
        page_index = _page_index_from_url(url)
        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()
        base_title = info.get("title", bvid)
        title = base_title
        duration = info.get("duration", 0)
        cover_url = info.get("pic", "")
        owner = info.get("owner", {})
        author_name = owner.get("name", "")
        author_uid = owner.get("mid", 0)
        pages = await v.get_pages()
        if page_index >= len(pages):
            message = f"分P序号超出范围: P{page_index + 1}/{len(pages)}"
            await send_progress(task_id, "error", {
                "title": base_title, "bvid": bvid, "message": message
            })
            return {"title": base_title, "status": "error", "message": message}
        if len(pages) > 1:
            page = pages[page_index]
            part = (page.get("part") or "").strip()
            title = f"{base_title} - P{page_index + 1}"
            if part:
                title = f"{title} {part}"
            duration = page.get("duration") or duration
            url = f"https://www.bilibili.com/video/{bvid}?p={page_index + 1}"
        else:
            url = f"https://www.bilibili.com/video/{bvid}"

        safe_title = sanitize_filename(title)
        final_subdir = output_subdir
        normal_path = DATA_DIR / "summary" / output_subdir / f"{safe_title}.md"
        nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
        media_rel_path = f"{output_subdir}/{safe_title}.mp4"
        media_path = DATA_DIR / "media" / media_rel_path

        if normal_path.exists():
            missing_assets = []
            if modules["detailed_summary"] and not summary_asset_path(normal_path, "detailed_summary").exists():
                missing_assets.append("detailed_summary")

            if missing_assets:
                subtitle_text = _transcript_text_for_summary(normal_path, with_timestamps=True)
                if "detailed_summary" in missing_assets:
                    await send_progress(task_id, "processing", {"title": title, "bvid": bvid, "step": "AI 生成详细总结"})
                    detailed_summary, _ = await generate_detailed_summary_with_claude(
                        subtitle_text, title, ai_client, model=model
                    )
                    save_summary_asset(normal_path, "detailed_summary", detailed_summary)
                await send_progress(task_id, "completed", {
                    "title": title, "bvid": bvid,
                    "duration_sec": 0,
                    "status": "success",
                    "path": f"{output_subdir}/{safe_title}.md"
                })
                return {"title": title, "status": "success", "duration_sec": 0}

            await send_progress(task_id, "skip", {
                "title": title, "bvid": bvid,
                "path": f"{output_subdir}/{safe_title}.md"
            })
            return {"title": title, "status": "skipped"}

        async def report_transcription_step(step: str):
            await send_progress(task_id, "processing", {"title": title, "bvid": bvid, "step": step})

        await report_transcription_step("下载本地视频")
        await download_bilibili_media(
            url,
            media_path,
            expected_duration=duration,
            progress=report_transcription_step,
        )

        await report_transcription_step("语音识别")
        subtitle_text, subtitle_raw = await transcribe_media(
            media_path,
            duration=duration,
            progress=report_transcription_step,
            source_url=url,
            bvid=bvid,
            title=title,
            output_subdir=final_subdir,
            media_rel_path=media_rel_path,
        )

        if subtitle_raw:
            save_ass(title, subtitle_raw, output_subdir)

        duration_sec = 0.0
        summary = "本次任务仅生成详细总结，请在详情页查看“详细总结”。"

        if nosub_path.exists():
            nosub_path.unlink()
            meta_json = nosub_path.with_suffix(".meta.json")
            if meta_json.exists():
                meta_json.unlink(missing_ok=True)
            clear_retry_count(output_subdir, safe_title)

        save_summary(
            title, bvid, url, duration, summary, final_subdir,
            author_name=author_name, author_uid=author_uid, cover_url=cover_url,
            media_path=media_rel_path,
        )
        summary_path = DATA_DIR / "summary" / final_subdir / f"{safe_title}.md"

        if modules["detailed_summary"]:
            await send_progress(task_id, "processing", {"title": title, "bvid": bvid, "step": "AI 生成详细总结"})
            detailed_transcript = format_timestamped_transcript(subtitle_raw)
            detailed_summary, _ = await generate_detailed_summary_with_claude(
                detailed_transcript or subtitle_text, title, ai_client, model=model
            )
            save_summary_asset(summary_path, "detailed_summary", detailed_summary)

        status = "success"
        await send_progress(task_id, "completed", {
            "title": title, "bvid": bvid,
            "duration_sec": round(duration_sec, 2),
            "status": status,
            "path": f"{final_subdir}/{safe_title}.md"
        })
        return {"title": title, "status": status, "duration_sec": round(duration_sec, 2)}

    except Exception as e:
        await send_progress(task_id, "error", {"title": bvid, "message": str(e)})
        return {"title": bvid, "status": "error", "message": str(e)}


async def run_batch(
    bvids: list[str],
    model: str,
    concurrency: int,
    output_subdir: str,
    task_id: str,
    modules: dict | None = None,
):
    modules = normalize_generation_modules(modules)
    sem = asyncio.Semaphore(concurrency)
    results = []

    await send_progress(task_id, "start", {
        "total": len(bvids), "concurrency": concurrency, "model": model, "modules": modules
    })

    async def bounded(target):
        async with sem:
            url = _target_to_url(target)
            try:
                r = await process_single_video(url, model, output_subdir, task_id, modules=modules)
                results.append(r)
            except Exception as e:
                await send_progress(task_id, "error", {"title": target, "message": str(e)})
                results.append({"title": target, "status": "error", "message": str(e)})

    try:
        await asyncio.gather(*[bounded(bv) for bv in bvids])
    except Exception as e:
        await send_progress(task_id, "error", {"title": "", "message": f"批处理异常: {e}"})

    success = sum(1 for r in results if r and r.get("status") == "success")
    skipped = sum(1 for r in results if r and r.get("status") == "skipped")
    no_sub = sum(1 for r in results if r and r.get("status") == "no_subtitle")
    errors = sum(1 for r in results if r and r.get("status") == "error")

    await send_progress(task_id, "done", {
        "total": len(bvids), "success": success, "skipped": skipped,
        "no_subtitle": no_sub, "errors": errors
    })
    return results


def save_user_meta(uid: int, name: str):
    """Save .meta.json in user summary directory for display name resolution."""
    user_dir = DATA_DIR / "summary" / "users" / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    meta_file = user_dir / ".meta.json"
    meta_file.write_text(json.dumps({"uid": uid, "name": name}, ensure_ascii=False), encoding="utf-8")
