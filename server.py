#!/usr/bin/env python3
"""
FastAPI 后端服务器
提供 REST API + SSE 实时进度推送
"""

import os
import asyncio
import json
import time
import re
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from bilibili_api import channel_series as bili_channel_series, user as bili_user, video as bili_video

from summarize import (
    extract_bvid,
    get_uid_by_name,
    get_user_videos,
    get_favorite_videos,
    sanitize_filename,
    generate_detailed_summary_with_claude,
    format_timestamped_transcript,
)

import routes.deps as deps
from routes.deps import (
    BUNDLE_DIR, DATA_DIR,
    init_credential, init_ai_client,
    send_progress, progress_generator,
    process_single_video, run_batch, save_user_meta,
    summary_asset_path, save_summary_asset,
    normalize_folder_name,
    default_folder_name, default_folder_output_subdir, resolve_folder_output_subdir,
    get_task_concurrency, register_task, list_task_logs, get_task_log_detail, load_task_logs,
)
from routes.favorites import router as favorites_router
from routes.asr import router as asr_router
from routes.settings import router as settings_router
from routes.auth import router as auth_router
from routes.telegram_bot import telegram_bot_service


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_credential()
    init_ai_client()
    load_task_logs()
    await telegram_bot_service.reload_from_env()
    try:
        yield
    finally:
        await telegram_bot_service.shutdown()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Bilibili 视频总结器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BUNDLE_DIR / "static")), name="static")

# Include route modules
app.include_router(favorites_router)
app.include_router(asr_router)
app.include_router(settings_router)
app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class GenerationModulesRequest(BaseModel):
    summary: bool = False
    detailed_summary: bool = True


def _request_model_dict(value: BaseModel) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


class SummarizeURLRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    model: str = ""
    concurrency: int | None = Field(default=None, ge=1, le=20)
    modules: GenerationModulesRequest = Field(default_factory=GenerationModulesRequest)
    folder: str = ""


class SummarizeUserRequest(BaseModel):
    user: str  # UID or name
    count: int = Field(default=50, ge=1, le=200)
    model: str = ""
    concurrency: int | None = Field(default=None, ge=1, le=20)
    modules: GenerationModulesRequest = Field(default_factory=GenerationModulesRequest)
    folder: str = ""


class SummarizeUserSelectedRequest(BaseModel):
    user: str = ""
    uid: int | None = None
    bvids: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    model: str = ""
    modules: GenerationModulesRequest = Field(default_factory=GenerationModulesRequest)
    folder: str = ""


class SummarizeFavRequest(BaseModel):
    count: int = Field(default=20, ge=1, le=200)
    model: str = ""
    concurrency: int | None = Field(default=None, ge=1, le=20)
    modules: GenerationModulesRequest = Field(default_factory=GenerationModulesRequest)


class DeleteSummariesRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, min_length=1, max_length=200)


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MoveSummariesRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, min_length=1, max_length=200)
    folder: str = Field(min_length=1, max_length=80)


def _resolve_summary_file(path: str) -> Path | None:
    """Resolve a summary file path safely under DATA_DIR/summary."""
    summary_root = (DATA_DIR / "summary").resolve()
    try:
        target = (summary_root / path).resolve(strict=False)
    except (RuntimeError, ValueError):
        return None

    if summary_root not in target.parents:
        return None
    if not target.is_file():
        return None
    if target.suffix.lower() != ".md":
        return None
    if _is_summary_sidecar(target):
        return None
    return target


def _resolve_media_file(path: str) -> Path | None:
    media_root = (DATA_DIR / "media").resolve()
    try:
        target = (media_root / path).resolve(strict=False)
    except (RuntimeError, ValueError):
        return None

    if media_root not in target.parents:
        return None
    if not target.is_file():
        return None
    if target.suffix.lower() not in {".mp4", ".m4v", ".webm", ".mov"}:
        return None
    return target


_BVID_RE = re.compile(r"\*\*BV号\*\*:\s*(BV[0-9A-Za-z]+)")
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SUMMARY_SECTION_RE = re.compile(r"(## 📝 摘要\s*\n\n).*", re.DOTALL)
_ASS_DIALOGUE_RE = re.compile(r"^Dialogue:\s*(.*)$")
_SUMMARY_SIDECAR_SUFFIXES = (".detailed.md",)
_cover_cache: dict[str, str] = {}
_MAX_COVER_LOOKUPS_PER_REQUEST = 40


def _dedupe_targets(targets: list[str]) -> list[str]:
    seen = set()
    result = []
    for target in targets:
        if target and target not in seen:
            seen.add(target)
            result.append(target)
    return result


def _page_url(bvid: str, page_index: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={page_index + 1}"


def _extract_bvids_from_payload(payload) -> list[str]:
    bvids = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() == "bvid" and isinstance(value, str) and value.startswith("BV"):
                bvids.append(value)
            else:
                bvids.extend(_extract_bvids_from_payload(value))
    elif isinstance(payload, list):
        for item in payload:
            bvids.extend(_extract_bvids_from_payload(item))
    return _dedupe_targets(bvids)


def _int_query_param(query: dict[str, list[str]], *names: str) -> int | None:
    for name in names:
        values = query.get(name)
        if values:
            try:
                return int(values[0])
            except (TypeError, ValueError):
                return None
    return None


async def _fetch_channel_bvids(channel_id: int, type_: bili_channel_series.ChannelSeriesType) -> list[str]:
    channel = bili_channel_series.ChannelSeries(type_=type_, id_=channel_id, credential=deps.credential)
    bvids = []

    for page in range(1, 51):
        data = await channel.get_videos(pn=page, ps=100)
        page_bvids = _extract_bvids_from_payload(data)
        before = len(bvids)
        bvids = _dedupe_targets(bvids + page_bvids)
        if not page_bvids or len(bvids) == before:
            break
        if len(page_bvids) < 100:
            break

    return bvids


async def _expand_bvid_url(raw_url: str, bvid: str) -> list[str]:
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    explicit_page = _int_query_param(query, "p")
    if explicit_page is not None:
        return [_page_url(bvid, max(explicit_page - 1, 0))]

    try:
        pages = await bili_video.Video(bvid=bvid, credential=deps.credential).get_pages()
    except Exception:
        return [bvid]

    if len(pages) > 1:
        return [_page_url(bvid, 0)]
    return [bvid]


async def _resolve_input_targets(raw_url: str) -> list[str]:
    raw_url = raw_url.strip()
    if not raw_url:
        return []

    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    path = parsed.path.lower()

    season_id = _int_query_param(query, "season_id", "seasonid")
    series_id = _int_query_param(query, "series_id", "seriesid")
    sid = _int_query_param(query, "sid")

    if season_id is not None:
        return await _fetch_channel_bvids(season_id, bili_channel_series.ChannelSeriesType.SEASON)
    if series_id is not None:
        return await _fetch_channel_bvids(series_id, bili_channel_series.ChannelSeriesType.SERIES)
    if sid is not None and "collectiondetail" in path:
        return await _fetch_channel_bvids(sid, bili_channel_series.ChannelSeriesType.SEASON)
    if sid is not None and "seriesdetail" in path:
        return await _fetch_channel_bvids(sid, bili_channel_series.ChannelSeriesType.SERIES)

    bvid = extract_bvid(raw_url)
    return await _expand_bvid_url(raw_url, bvid)


async def _resolve_input_targets_many(urls: list[str]) -> list[str]:
    targets = []
    for url in urls:
        try:
            targets.extend(await _resolve_input_targets(url))
        except ValueError:
            continue
    return _dedupe_targets(targets)


def _is_summary_sidecar(path: Path) -> bool:
    return any(path.name.endswith(suffix) for suffix in _SUMMARY_SIDECAR_SUFFIXES)


def _parse_duration(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return 0
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return 0


def _normalize_cover_url(value: str) -> str:
    cover = value or ""
    if isinstance(cover, str) and cover.startswith("//"):
        return f"https:{cover}"
    return cover


def _parse_ass_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) != 3:
        return 0

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return 0

    return hours * 3600 + minutes * 60 + seconds


def _strip_ass_text(text: str) -> str:
    cleaned = re.sub(r"\{[^}]*\}", "", text)
    cleaned = cleaned.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    return cleaned.strip()


def _parse_ass_file(ass_path: Path) -> list[dict]:
    segments: list[dict] = []
    if not ass_path.is_file():
        return segments

    for line in ass_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _ASS_DIALOGUE_RE.match(line)
        if not match:
            continue

        fields = match.group(1).split(",", 9)
        if len(fields) < 10:
            continue

        text = _strip_ass_text(fields[9])
        if not text:
            continue

        segments.append({
            "start": _parse_ass_timestamp(fields[1]),
            "end": _parse_ass_timestamp(fields[2]),
            "text": text,
        })

    return segments


def _read_summary_asset(md_path: Path, asset: str) -> str:
    try:
        path = summary_asset_path(md_path, asset)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _unlink_if_file(path: Path, deleted_files: list[str]):
    try:
        if path.is_file():
            path.unlink()
            deleted_files.append(str(path))
    except OSError:
        pass


def _summary_root() -> Path:
    return DATA_DIR / "summary"


def _folders_root() -> Path:
    return _summary_root() / "folders"


def _ensure_default_folder() -> Path:
    folder_dir = _folders_root() / default_folder_name()
    folder_dir.mkdir(parents=True, exist_ok=True)
    return folder_dir


def _folder_info(folder_dir: Path, summary_root: Path | None = None, items: list[dict] | None = None) -> dict:
    root = summary_root or _summary_root()
    name = folder_dir.name
    return {
        "name": name,
        "display_name": name,
        "path": str(folder_dir.relative_to(root)),
        "count": len(items or []),
        "items": items or [],
    }


def _list_folder_infos() -> list[dict]:
    _ensure_default_folder()
    folders_root = _folders_root()
    summary_root = _summary_root()
    if not folders_root.exists():
        return []
    return [
        _folder_info(folder_dir, summary_root)
        for folder_dir in sorted(folders_root.iterdir(), key=lambda p: p.name.lower())
        if folder_dir.is_dir()
    ]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法生成不冲突的文件名")


def _move_if_file(src: Path, dst: Path, moved_files: list[dict]) -> Path | None:
    if not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_path(dst)
    shutil.move(str(src), str(target))
    moved_files.append({"from": str(src), "to": str(target)})
    return target


def _delete_summary_record(md_path: Path) -> list[str]:
    deleted_files: list[str] = []
    meta = _summary_meta(md_path)

    _unlink_if_file(md_path, deleted_files)
    _unlink_if_file(md_path.with_suffix(".meta.json"), deleted_files)
    _unlink_if_file(summary_asset_path(md_path, "detailed_summary"), deleted_files)
    _unlink_if_file(md_path.with_name(f"{md_path.stem}.mindmap.json"), deleted_files)
    _unlink_if_file(_ass_path_for_summary(md_path), deleted_files)

    media_path = meta.get("media_path", "")
    media_file = _resolve_media_file(media_path) if media_path else None
    if media_file:
        _unlink_if_file(media_file, deleted_files)

    return deleted_files


def _remove_empty_dirs(path: Path, stop_at: Path):
    current = path
    stop = stop_at.resolve(strict=False)
    while current.resolve(strict=False) != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _delete_folder_record(folder_name: str) -> dict:
    folder = normalize_folder_name(folder_name)
    if folder == default_folder_name():
        raise ValueError("默认文件夹不能删除")

    folders_root = _folders_root().resolve()
    folder_dir = (_folders_root() / folder).resolve(strict=False)
    if folder_dir == folders_root or folders_root not in folder_dir.parents:
        raise ValueError("文件夹路径无效")
    if not folder_dir.exists() or not folder_dir.is_dir():
        raise FileNotFoundError("文件夹不存在")

    deleted_files: list[str] = []
    deleted_records: list[str] = []
    errors: list[dict] = []
    summary_root = _summary_root().resolve()

    for md_path in sorted(folder_dir.rglob("*.md")):
        if _is_summary_sidecar(md_path):
            continue
        try:
            deleted_files.extend(_delete_summary_record(md_path))
            deleted_records.append(str(md_path.relative_to(summary_root)))
        except Exception as exc:
            errors.append({"path": str(md_path.relative_to(summary_root)), "error": str(exc)})

    if errors:
        return {
            "folder": folder,
            "deleted": deleted_records,
            "deleted_files": deleted_files,
            "errors": errors,
        }

    if folder_dir.exists():
        shutil.rmtree(folder_dir)
        deleted_files.append(str(folder_dir))

    ass_folder = (DATA_DIR / "ass" / "folders" / folder).resolve(strict=False)
    if ass_folder.exists() and ass_folder.is_dir():
        shutil.rmtree(ass_folder)
        deleted_files.append(str(ass_folder))
        _remove_empty_dirs(ass_folder.parent, (DATA_DIR / "ass").resolve(strict=False))

    media_folder = (DATA_DIR / "media" / "folders" / folder).resolve(strict=False)
    if media_folder.exists() and media_folder.is_dir():
        shutil.rmtree(media_folder)
        deleted_files.append(str(media_folder))
        _remove_empty_dirs(media_folder.parent, (DATA_DIR / "media").resolve(strict=False))

    return {
        "folder": folder,
        "deleted": deleted_records,
        "deleted_files": deleted_files,
        "errors": errors,
    }


def _move_summary_record(md_path: Path, target_folder: str) -> dict:
    summary_root = _summary_root().resolve()
    media_root = (DATA_DIR / "media").resolve()
    folder_name = normalize_folder_name(target_folder)
    rel = md_path.resolve(strict=False).relative_to(summary_root)
    target_subdir = Path("folders") / folder_name
    if "no_subtitle" in rel.parts:
        target_subdir = target_subdir / "no_subtitle"

    target_dir = summary_root / target_subdir
    if md_path.parent.resolve(strict=False) == target_dir.resolve(strict=False):
        rel_path = str(md_path.relative_to(summary_root))
        return {"from": rel_path, "to": rel_path, "files": []}

    meta = _summary_meta(md_path)
    target_md = _unique_path(target_dir / md_path.name)
    moved_files: list[dict] = []

    old_meta = md_path.with_suffix(".meta.json")
    old_detailed = summary_asset_path(md_path, "detailed_summary")
    old_mindmap = md_path.with_name(f"{md_path.stem}.mindmap.json")
    old_ass = _ass_path_for_summary(md_path)
    old_media = _resolve_media_file(meta.get("media_path", ""))

    target_dir.mkdir(parents=True, exist_ok=True)
    old_rel = str(md_path.relative_to(summary_root))
    shutil.move(str(md_path), str(target_md))
    moved_files.append({"from": str(md_path), "to": str(target_md)})

    target_meta = target_md.with_suffix(".meta.json")
    _move_if_file(old_meta, target_meta, moved_files)
    _move_if_file(old_detailed, summary_asset_path(target_md, "detailed_summary"), moved_files)
    _move_if_file(old_mindmap, target_md.with_name(f"{target_md.stem}.mindmap.json"), moved_files)
    _move_if_file(old_ass, _ass_path_for_summary(target_md), moved_files)

    if old_media:
        media_ext = old_media.suffix or ".mp4"
        target_media = media_root / target_subdir / f"{target_md.stem}{media_ext}"
        moved_media = _move_if_file(old_media, target_media, moved_files)
        if moved_media:
            meta["media_path"] = str(moved_media.relative_to(media_root))

    if target_meta.exists():
        try:
            saved_meta = json.loads(target_meta.read_text(encoding="utf-8"))
        except Exception:
            saved_meta = {}
        saved_meta.update({key: value for key, value in meta.items() if key in {
            "title", "bvid", "url", "duration", "author_name", "author_uid", "cover_url", "media_path", "generated_at"
        }})
        target_meta.write_text(json.dumps(saved_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    elif meta:
        target_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"from": old_rel, "to": str(target_md.relative_to(summary_root)), "files": moved_files}


def _transcript_text_for_summary(md_path: Path, with_timestamps: bool = False) -> str:
    segments = _parse_ass_file(_ass_path_for_summary(md_path))
    if with_timestamps:
        timestamped = format_timestamped_transcript(segments)
        if timestamped:
            return timestamped
    transcript = "\n".join(segment["text"] for segment in segments if segment.get("text")).strip()
    if transcript:
        return transcript
    return md_path.read_text(encoding="utf-8", errors="ignore")


def _write_summary_section(md_path: Path, summary: str) -> str:
    content = md_path.read_text(encoding="utf-8", errors="ignore")
    cleaned_summary = (summary or "").strip()
    if _SUMMARY_SECTION_RE.search(content):
        updated = _SUMMARY_SECTION_RE.sub(lambda match: f"{match.group(1)}{cleaned_summary}\n", content)
    else:
        updated = f"{content.rstrip()}\n\n---\n\n## 📝 摘要\n\n{cleaned_summary}\n"
    md_path.write_text(updated, encoding="utf-8")
    return updated


def _summary_meta(md_path: Path) -> dict:
    meta_path = md_path.with_suffix(".meta.json")
    if not meta_path.exists():
        bvid, title = _extract_summary_info(md_path)
        return {
            "title": title or md_path.stem,
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "duration": 0,
            "author_name": "",
            "author_uid": 0,
            "cover_url": "",
            "media_path": "",
        }

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        bvid, title = _extract_summary_info(md_path)
        return {
            "title": title or md_path.stem,
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "duration": 0,
            "author_name": "",
            "author_uid": 0,
            "cover_url": "",
            "media_path": "",
        }

    bvid = meta.get("bvid", "") or ""
    cover_url = meta.get("cover_url", "") or ""
    if isinstance(cover_url, str) and cover_url.startswith("//"):
        cover_url = f"https:{cover_url}"

    return {
        "title": meta.get("title", "") or md_path.stem,
        "bvid": bvid,
        "url": meta.get("url", "") or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""),
        "duration": meta.get("duration", 0) or 0,
        "author_name": meta.get("author_name", "") or "",
        "author_uid": meta.get("author_uid", 0) or 0,
        "cover_url": cover_url,
        "media_path": meta.get("media_path", "") or "",
        "generated_at": meta.get("generated_at", "") or "",
    }


def _ass_path_for_summary(md_path: Path) -> Path:
    summary_root = (DATA_DIR / "summary").resolve()
    ass_root = DATA_DIR / "ass"
    rel = md_path.resolve(strict=False).relative_to(summary_root)
    parts = rel.parts
    if "no_subtitle" in parts:
        parts = tuple(part for part in parts if part != "no_subtitle")
    return (ass_root / Path(*parts)).with_suffix(".ass")


def _extract_summary_info(md_path: Path) -> tuple[str, str]:
    """Extract (bvid, title) from summary markdown content."""
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", ""

    bvid_match = _BVID_RE.search(text)
    title_match = _TITLE_RE.search(text)
    bvid = bvid_match.group(1) if bvid_match else ""
    title = title_match.group(1).strip() if title_match else ""
    return bvid, title


def _build_summary_item(md_path: Path, summary_root: Path) -> dict:
    rel = md_path.relative_to(summary_root)
    parts = rel.parts
    item = {
        "name": md_path.stem,
        "path": str(rel),
        "no_subtitle": "no_subtitle" in str(rel),
        "source": parts[0] if parts else "",
        "folder": parts[1] if len(parts) > 1 and parts[0] == "folders" else "",
        "bvid": "",
        "cover": "",
        "duration": 0,
        "author_name": "",
    }

    meta_path = md_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            item["name"] = meta.get("title", "") or item["name"]
            item["bvid"] = meta.get("bvid", "") or ""
            item["cover"] = meta.get("cover_url", "") or ""
            item["duration"] = meta.get("duration", 0) or 0
            item["author_name"] = meta.get("author_name", "") or ""
            if isinstance(item["cover"], str) and item["cover"].startswith("//"):
                item["cover"] = "https:" + item["cover"]
        except Exception:
            pass

    if not item["bvid"]:
        md_bvid, md_title = _extract_summary_info(md_path)
        item["bvid"] = md_bvid
        if md_title:
            item["name"] = md_title

    if item["bvid"] and item["bvid"] in _cover_cache and not item["cover"]:
        item["cover"] = _cover_cache[item["bvid"]]

    return item


def _summary_status_for_video(title: str, output_subdir: str) -> dict:
    safe_title = sanitize_filename(title)
    normal_path = DATA_DIR / "summary" / output_subdir / f"{safe_title}.md"
    nosub_path = DATA_DIR / "summary" / output_subdir / "no_subtitle" / f"{safe_title}.md"
    if normal_path.exists():
        return {
            "has_summary": True,
            "summary_status": "done",
            "summary_path": f"{output_subdir}/{safe_title}.md",
        }
    if nosub_path.exists():
        return {
            "has_summary": True,
            "summary_status": "no_subtitle",
            "summary_path": f"{output_subdir}/no_subtitle/{safe_title}.md",
        }
    return {"has_summary": False, "summary_status": "none", "summary_path": None}


def _summary_status_for_video_anywhere(title: str, preferred_output_subdir: str = "") -> dict:
    if preferred_output_subdir:
        preferred_status = _summary_status_for_video(title, preferred_output_subdir)
        if preferred_status["has_summary"]:
            return preferred_status

    safe_title = sanitize_filename(title)
    summary_root = DATA_DIR / "summary"
    candidates: list[Path] = []
    if summary_root.exists():
        candidates.extend(summary_root.rglob(f"{safe_title}.md"))

    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        if _is_summary_sidecar(path):
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


async def _resolve_user_identity(user_value: str = "", uid_value: int | None = None) -> tuple[int | None, str]:
    uid = uid_value
    username = None
    raw_user = (user_value or "").strip()
    if uid is None:
        if raw_user.isdigit():
            uid = int(raw_user)
        elif raw_user:
            username = raw_user
            uid = await get_uid_by_name(raw_user)

    if not uid:
        return None, username or raw_user

    try:
        u = bili_user.User(uid=uid, credential=deps.credential)
        user_info = await u.get_user_info()
        return uid, user_info.get("name", username or str(uid))
    except Exception:
        return uid, username or raw_user or str(uid)


async def _fetch_cover_by_bvid(bvid: str) -> str:
    if not bvid:
        return ""
    if bvid in _cover_cache:
        return _cover_cache[bvid]

    try:
        v = bili_video.Video(bvid=bvid, credential=deps.credential)
        info = await v.get_info()
        cover = info.get("pic", "") or ""
        if isinstance(cover, str) and cover.startswith("//"):
            cover = "https:" + cover
        _cover_cache[bvid] = cover
        return cover
    except Exception:
        _cover_cache[bvid] = ""
        return ""


async def _fill_missing_covers(items: list[dict]):
    candidates: list[str] = []
    seen = set()
    for item in items:
        bvid = item.get("bvid", "")
        if bvid and not item.get("cover") and bvid not in seen:
            seen.add(bvid)
            candidates.append(bvid)

    if not candidates:
        return

    sem = asyncio.Semaphore(6)
    targets = candidates[:_MAX_COVER_LOOKUPS_PER_REQUEST]

    async def bounded_fetch(bvid: str):
        async with sem:
            await _fetch_cover_by_bvid(bvid)

    await asyncio.gather(*[bounded_fetch(bv) for bv in targets])

    for item in items:
        bvid = item.get("bvid", "")
        if bvid and not item.get("cover"):
            item["cover"] = _cover_cache.get(bvid, "")


# ---------------------------------------------------------------------------
# Core API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return (BUNDLE_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    return {"logged_in": deps.credential is not None, "ai_configured": deps.ai_client is not None}


@app.get("/api/summaries")
async def list_summaries():
    """List all generated summaries, structured by category."""
    _ensure_default_folder()
    summary_root = DATA_DIR / "summary"

    all_items: list[dict] = []

    def collect_items(root_dir: Path) -> list[dict]:
        items = []
        if not root_dir.exists():
            return items
        for md in sorted(root_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            if _is_summary_sidecar(md):
                continue
            item = _build_summary_item(md, summary_root)
            items.append(item)
            all_items.append(item)
        return items

    collect_items(summary_root / "standalone")
    collect_items(summary_root / "favorites")

    users_dir = summary_root / "users"
    if users_dir.exists():
        for uid_folder in sorted(users_dir.iterdir()):
            if not uid_folder.is_dir():
                continue
            collect_items(uid_folder)

    folder_groups = []
    folders_root = summary_root / "folders"
    if folders_root.exists():
        for folder_dir in sorted(folders_root.iterdir(), key=lambda p: p.name.lower()):
            if not folder_dir.is_dir():
                continue
            items = collect_items(folder_dir)
            folder_groups.append(_folder_info(folder_dir, summary_root, items))

    await _fill_missing_covers(all_items)
    def item_mtime(item: dict) -> float:
        path = _resolve_summary_file(item["path"])
        return path.stat().st_mtime if path else 0

    all_items.sort(key=item_mtime, reverse=True)
    categories = [
        {"type": "all", "label": "所有视频", "icon": "library", "count": len(all_items), "items": all_items},
        *[
            {
                "type": "folder",
                "label": group["display_name"],
                "icon": "folder",
                "count": group["count"],
                "folder": group["name"],
                "items": group["items"],
            }
            for group in folder_groups
        ],
    ]
    return {"categories": categories, "folders": _list_folder_infos(), "all_items": all_items}


@app.get("/api/summary/{path:path}")
async def read_summary(path: str):
    filepath = _resolve_summary_file(path)
    if not filepath:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return {"content": filepath.read_text(encoding="utf-8"), "path": path}


@app.get("/api/summary-detail/{path:path}")
async def read_summary_detail(path: str):
    filepath = _resolve_summary_file(path)
    if not filepath:
        return JSONResponse(status_code=404, content={"error": "Not found"})

    ass_path = _ass_path_for_summary(filepath)
    meta = _summary_meta(filepath)
    media_path = meta.get("media_path", "")
    media_url = f"/api/media/{media_path}" if media_path and _resolve_media_file(media_path) else ""
    return {
        "content": filepath.read_text(encoding="utf-8"),
        "path": path,
        "meta": meta,
        "media_url": media_url,
        "subtitles": _parse_ass_file(ass_path),
        "detailed_summary": _read_summary_asset(filepath, "detailed_summary"),
    }


@app.post("/api/summary-asset/{asset}/{path:path}")
async def generate_summary_asset(asset: str, path: str):
    filepath = _resolve_summary_file(path)
    if not filepath:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if not deps.ai_client:
        return JSONResponse(status_code=400, content={"error": "AI 未配置"})

    meta = _summary_meta(filepath)
    title = meta.get("title") or filepath.stem
    transcript = _transcript_text_for_summary(filepath)
    model = deps.DEFAULT_MODEL

    if asset == "detailed-summary":
        transcript = _transcript_text_for_summary(filepath, with_timestamps=True)
        detailed_summary, duration_sec = await generate_detailed_summary_with_claude(
            transcript, title, deps.ai_client, model=model
        )
        save_summary_asset(filepath, "detailed_summary", detailed_summary)
        return {"detailed_summary": detailed_summary, "duration_sec": round(duration_sec, 2)}

    return JSONResponse(status_code=400, content={"error": "未知生成模块"})


@app.post("/api/summaries/delete")
async def delete_summaries(req: DeleteSummariesRequest):
    summary_root = (DATA_DIR / "summary").resolve()
    deleted: list[str] = []
    deleted_files: list[str] = []
    errors: list[dict] = []

    for raw_path in req.paths:
        filepath = _resolve_summary_file(raw_path)
        if not filepath:
            errors.append({"path": raw_path, "error": "记录不存在或路径无效"})
            continue

        try:
            rel_path = str(filepath.relative_to(summary_root))
            deleted_files.extend(_delete_summary_record(filepath))
            deleted.append(rel_path)
        except Exception as exc:
            errors.append({"path": raw_path, "error": str(exc)})

    return {"deleted": deleted, "deleted_files": deleted_files, "errors": errors}


@app.get("/api/folders")
async def list_local_folders():
    return {"folders": _list_folder_infos()}


@app.post("/api/folders")
async def create_local_folder(req: CreateFolderRequest):
    try:
        name = normalize_folder_name(req.name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    folder_dir = _folders_root() / name
    folder_dir.mkdir(parents=True, exist_ok=True)
    return {"folder": _folder_info(folder_dir)}


@app.delete("/api/folders/{name}")
async def delete_local_folder(name: str):
    try:
        return _delete_folder_record(name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/summaries/move")
async def move_summaries(req: MoveSummariesRequest):
    moved: list[dict] = []
    errors: list[dict] = []

    try:
        folder_name = normalize_folder_name(req.folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    (_folders_root() / folder_name).mkdir(parents=True, exist_ok=True)
    for raw_path in req.paths:
        filepath = _resolve_summary_file(raw_path)
        if not filepath:
            errors.append({"path": raw_path, "error": "记录不存在或路径无效"})
            continue
        try:
            moved.append(_move_summary_record(filepath, folder_name))
        except Exception as exc:
            errors.append({"path": raw_path, "error": str(exc)})

    return {"moved": moved, "errors": errors, "folder": folder_name}


@app.get("/api/media/{path:path}")
async def read_media(path: str):
    filepath = _resolve_media_file(path)
    if not filepath:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return FileResponse(filepath, media_type="video/mp4", filename=filepath.name)


@app.get("/api/tasks")
async def list_tasks(type: str = "", page: int = 1, page_size: int = 10):
    return list_task_logs(task_type=type, page=page, page_size=page_size)


@app.get("/api/tasks/{task_id}")
async def read_task_detail(task_id: str):
    detail = get_task_log_detail(task_id)
    if not detail:
        return JSONResponse(status_code=404, content={"error": "任务不存在"})
    return detail


@app.get("/api/user/videos")
async def list_user_videos(user: str, page: int = 1, page_size: int = 20, folder: str = ""):
    raw_user = (user or "").strip()
    if not raw_user:
        return JSONResponse(status_code=400, content={"error": "请输入 UP 主名字或 UID"})

    uid, resolved_name = await _resolve_user_identity(raw_user)
    if not uid:
        return JSONResponse(status_code=404, content={"error": f"未找到 UP 主: {raw_user}"})

    page = max(1, int(page or 1))
    page_size = max(1, min(50, int(page_size or 20)))

    try:
        output_subdir, _ = resolve_folder_output_subdir(folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    try:
        u = bili_user.User(uid=uid, credential=deps.credential)
        videos_data = await u.get_videos(ps=page_size, pn=page)
        data_list = videos_data.get("list", {}) or {}
        vlist = data_list.get("vlist", []) or []
        total_count = int(data_list.get("count", 0) or 0)

        videos = []
        for item in vlist:
            bvid = item.get("bvid", "")
            title = item.get("title", "") or bvid
            cover = _normalize_cover_url(item.get("pic", "") or item.get("cover", ""))
            status = _summary_status_for_video_anywhere(title, output_subdir)
            videos.append({
                "bvid": bvid,
                "title": title,
                "cover": cover,
                "duration": _parse_duration(item.get("length", item.get("duration", 0))),
                "created": item.get("created", 0) or item.get("created_at", 0),
                "play_count": item.get("play", 0) or item.get("play_count", 0),
                "comment_count": item.get("comment", 0),
                "upper": resolved_name,
                "upper_mid": uid,
                **status,
            })

        has_more = bool(vlist) and (page * page_size < total_count if total_count else len(vlist) >= page_size)
        save_user_meta(uid, resolved_name)
        return {
            "uid": uid,
            "name": resolved_name,
            "videos": videos,
            "page": page,
            "page_size": page_size,
            "total": total_count,
            "has_more": has_more,
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/summarize/url")
async def summarize_urls(req: SummarizeURLRequest):
    task_id = f"url-{int(time.time()*1000)}"
    try:
        targets = await _resolve_input_targets_many(req.urls)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"解析链接失败: {exc}"})
    if not targets:
        return JSONResponse(status_code=400, content={"error": "无法解析任何 BV 号"})

    model = req.model or deps.DEFAULT_MODEL
    try:
        output_subdir, folder_name = resolve_folder_output_subdir(req.folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    register_task(
        task_id,
        "url",
        "URL 视频总结",
        total=len(targets),
        meta={"folder": folder_name, "bvids": targets[:200]},
    )
    asyncio.create_task(run_batch(
        targets,
        model,
        get_task_concurrency(),
        output_subdir,
        task_id,
        modules=_request_model_dict(req.modules),
    ))
    return {"task_id": task_id, "total": len(targets)}


@app.post("/api/summarize/user")
async def summarize_user(req: SummarizeUserRequest):
    task_id = f"user-{int(time.time()*1000)}"
    try:
        _, folder_name = resolve_folder_output_subdir(req.folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    register_task(
        task_id,
        "user",
        f"UP 主视频总结: {req.user}",
        total=req.count,
        meta={"user": req.user, "folder": folder_name},
    )

    async def _run():
        uid, resolved_name = await _resolve_user_identity(req.user)
        if not uid:
            await send_progress(task_id, "error", {"message": f"未找到 UP 主: {req.user}"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 1})
            return

        save_user_meta(uid, resolved_name)

        model = req.model or deps.DEFAULT_MODEL
        await send_progress(task_id, "info", {"message": f"获取 UP 主 {resolved_name} (UID:{uid}) 的最新 {req.count} 个视频..."})
        bvids = await get_user_videos(uid, req.count, deps.credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        try:
            output_subdir, _ = resolve_folder_output_subdir(req.folder)
        except ValueError as exc:
            await send_progress(task_id, "error", {"message": str(exc)})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 1})
            return

        await run_batch(
            bvids,
            model,
            get_task_concurrency(),
            output_subdir,
            task_id,
            modules=_request_model_dict(req.modules),
        )

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.post("/api/summarize/user-selected")
async def summarize_user_selected(req: SummarizeUserSelectedRequest):
    raw_bvids = []
    seen = set()
    for bvid in req.bvids:
        candidate = str(bvid or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        raw_bvids.append(candidate)

    if not raw_bvids:
        return JSONResponse(status_code=400, content={"error": "请选择要总结的视频"})

    uid, resolved_name = await _resolve_user_identity(req.user, req.uid)
    if not uid:
        return JSONResponse(status_code=404, content={"error": f"未找到 UP 主: {req.user or req.uid or ''}"})

    model = req.model or deps.DEFAULT_MODEL
    task_id = f"user-selected-{int(time.time()*1000)}"
    try:
        output_subdir, folder_name = resolve_folder_output_subdir(req.folder)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    save_user_meta(uid, resolved_name)
    register_task(
        task_id,
        "user",
        f"{resolved_name} 选中视频总结",
        total=len(raw_bvids),
        meta={"uid": uid, "name": resolved_name, "folder": folder_name, "bvids": raw_bvids[:200]},
    )

    asyncio.create_task(run_batch(
        raw_bvids,
        model,
        get_task_concurrency(),
        output_subdir,
        task_id,
        modules=_request_model_dict(req.modules),
    ))
    return {"task_id": task_id, "total": len(raw_bvids), "uid": uid, "name": resolved_name}


@app.post("/api/summarize/favorites")
async def summarize_favorites(req: SummarizeFavRequest):
    if not deps.credential:
        return JSONResponse(status_code=401, content={"error": "未登录 Bilibili"})

    task_id = f"fav-{int(time.time()*1000)}"
    register_task(
        task_id,
        "favorites",
        "默认收藏夹视频总结",
        total=req.count,
        meta={"count": req.count, "folder": default_folder_name()},
    )

    async def _run():
        model = req.model or deps.DEFAULT_MODEL
        await send_progress(task_id, "info", {"message": f"获取默认收藏夹的最新 {req.count} 个视频..."})
        bvids = await get_favorite_videos(req.count, deps.credential)

        if not bvids:
            await send_progress(task_id, "error", {"message": "未找到视频"})
            await send_progress(task_id, "done", {"total": 0, "success": 0, "skipped": 0, "no_subtitle": 0, "errors": 0})
            return

        await run_batch(
            bvids,
            model,
            get_task_concurrency(),
            default_folder_output_subdir(),
            task_id,
            modules=_request_model_dict(req.modules),
        )

    asyncio.create_task(_run())
    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
async def progress_stream(task_id: str, request: Request):
    last_id = int(request.headers.get("Last-Event-ID", "-1"))
    return StreamingResponse(
        progress_generator(task_id, last_id=last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18520)
