"""总结：读取既有产物 + 触发新任务（复用 summarize.py 的 LLM 与文件布局）。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.deps import DATA_DIR, summary_asset_path
from routes.v2 import tasks as task_store
from routes.v2.auth import require_token
from routes.v2.store import load_subtitle

SUMMARY_DIR = DATA_DIR / "summary"

router = APIRouter(
    prefix="/api/v2/summaries",
    tags=["v2"],
    dependencies=[Depends(require_token)],
)


class SummaryRequest(BaseModel):
    p: int = Field(default=1, ge=1)
    type: str = "detailed"
    force: bool = False
    subdir: str = ""
    model: str = ""


def find_summary_meta(bvid: str, page: int = 1) -> tuple[Path, dict] | None:
    """在 `summary/**/*.meta.json` 里按 bvid 找总结记录。"""
    if not SUMMARY_DIR.is_dir():
        return None
    best: tuple[float, Path, dict] | None = None
    for meta_path in SUMMARY_DIR.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(meta.get("bvid") or "") != bvid:
            continue
        md_path = meta_path.with_suffix("").with_suffix(".md")
        try:
            mtime = md_path.stat().st_mtime
        except OSError:
            mtime = meta_path.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, md_path, meta)
    if not best:
        return None
    return best[1], best[2]


def read_summary(bvid: str, page: int = 1) -> dict | None:
    found = find_summary_meta(bvid, page)
    if not found:
        return None
    md_path, meta = found
    try:
        summary_md = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        summary_md = ""
    detailed = ""
    try:
        asset = summary_asset_path(md_path, "detailed_summary")
        if asset.is_file():
            detailed = asset.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        detailed = ""
    return {
        "bvid": bvid,
        "page": page,
        "title": str(meta.get("title") or ""),
        "duration": int(meta.get("duration") or 0),
        "author": str(meta.get("author_name") or ""),
        "cover": str(meta.get("cover_url") or ""),
        "url": str(meta.get("url") or ""),
        "path": str(md_path),
        "summary": summary_md,
        "detailed": detailed,
        "has_summary": True,
        "updated_at": md_path.stat().st_mtime if md_path.exists() else 0,
    }


@router.get("/{bvid}")
async def get_summary(bvid: str, p: int = Query(default=1, ge=1)):
    data = read_summary(bvid, p)
    if not data:
        raise HTTPException(status_code=404, detail="该视频还没有总结")
    return data


@router.post("/{bvid}")
async def create_summary(bvid: str, body: SummaryRequest):
    existing = read_summary(bvid, body.p)
    if existing and existing.get("detailed") and not body.force:
        return {"cached": True, "task_id": None, "summary": existing}

    record = load_subtitle(bvid, body.p)
    if not record or not (record.get("segments") or []):
        raise HTTPException(status_code=409, detail="该视频还没有字幕，请先抓取或转写")

    title = str((existing or {}).get("title") or (record or {}).get("title") or bvid)
    duration = int((existing or {}).get("duration") or (record or {}).get("duration") or 0)
    subdir = body.subdir or "standalone"

    task_id = task_store.start_summary_job(
        bvid=bvid,
        page=body.p,
        title=title,
        duration=duration,
        subdir=subdir,
        model=body.model,
        kind="detailed" if body.type != "brief" else "brief",
    )
    return {"cached": False, "task_id": task_id, "status": "queued", "title": title}
