"""
字幕记录的落盘与历史索引（服务端为权威）。

存两份：
1) ``DATA_DIR/subtitles/<bvid>_p<page>_<source>.json`` —— 插件/ASR 统一主存（结构化 segments）
2) ``DATA_DIR/ass/<subdir>/<safe_title>.ass`` —— 兼容既有 Web UI 的字幕读取（``server._parse_ass_file``）

历史列表 = 既有 ``summary/**/*.meta.json``（总结记录） ∪ ``subtitles/*.json``（字幕记录）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from routes.deps import DATA_DIR

SUBTITLE_DIR = DATA_DIR / "subtitles"
SUMMARY_DIR = DATA_DIR / "summary"

_SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str, limit: int = 80) -> str:
    value = _SAFE.sub("_", str(text or "")).strip().strip(".")
    return (value or "untitled")[:limit]


def normalize_segments(raw: Any) -> list[dict]:
    """把 ``{start,end,text}`` / ``{from,to,content}`` 两种写法统一成前者。"""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", item.get("from", 0)) or 0)
            end = float(item.get("end", item.get("to", 0)) or 0)
        except (TypeError, ValueError):
            continue
        text = str(item.get("text", item.get("content", "")) or "").strip()
        if not text or end <= start:
            continue
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    out.sort(key=lambda seg: seg["start"])
    return out


def _record_path(bvid: str, page: int, source: str) -> Path:
    return SUBTITLE_DIR / f"{safe_name(bvid, 24)}_p{int(page)}_{safe_name(source, 24)}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_subtitle(
    *,
    bvid: str,
    page: int,
    cid: int | None,
    source: str,
    lang: str = "",
    title: str = "",
    duration: int = 0,
    segments: list[dict],
    extra_subdir: str = "",
    cover: str = "",
    author: str = "",
) -> Path:
    """写字幕记录（并以 .ass 形式同步一份给既有 Web UI）。"""
    payload = {
        "bvid": bvid,
        "page": int(page),
        "cid": int(cid) if cid else None,
        "source": source,
        "lang": lang,
        "title": title,
        "duration": int(duration or 0),
        "cover": cover,
        "author": author,
        "segments": segments,
        "count": len(segments),
        "updated_at": time.time(),
    }
    path = _record_path(bvid, page, source)
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=1))

    if segments and title:
        try:
            from summarize import save_ass  # 延迟导入，避免循环依赖

            subdir = extra_subdir or "standalone"
            ass_items = [{"from": s["start"], "to": s["end"], "content": s["text"]} for s in segments]
            save_ass(title, ass_items, subdir)
        except Exception:  # noqa: BLE001 - .ass 只是兼容产物，失败不影响主存
            pass
    return path


def load_subtitle(bvid: str, page: int | None = None) -> dict | None:
    """按 bvid(+page) 取一条字幕记录；source 优先 native-cc > native-ai > asr。"""
    if not SUBTITLE_DIR.is_dir():
        return None
    pattern = f"{safe_name(bvid, 24)}_p*"
    candidates: list[dict] = []
    for path in sorted(SUBTITLE_DIR.glob(pattern)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if page is not None and int(data.get("page") or 0) != int(page):
            continue
        candidates.append(data)
    if not candidates:
        return None
    order = {"native-cc": 0, "native-ai": 1, "asr": 2}
    candidates.sort(key=lambda d: (order.get(str(d.get("source")), 9), -float(d.get("updated_at") or 0)))
    return candidates[0]


def _iter_subtitle_records() -> list[dict]:
    if not SUBTITLE_DIR.is_dir():
        return []
    items: list[dict] = []
    for path in SUBTITLE_DIR.glob("*.json"):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return items


def _iter_summary_meta() -> list[dict]:
    if not SUMMARY_DIR.is_dir():
        return []
    items: list[dict] = []
    for path in SUMMARY_DIR.rglob("*.meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rel = path.relative_to(SUMMARY_DIR)
        folder = str(rel.parent).replace("\\", "/")
        md_path = path.with_suffix("").with_suffix(".md")
        try:
            updated = md_path.stat().st_mtime
        except OSError:
            updated = path.stat().st_mtime
        items.append(
            {
                "bvid": str(meta.get("bvid") or ""),
                "title": str(meta.get("title") or ""),
                "duration": int(meta.get("duration") or 0),
                "author": str(meta.get("author_name") or ""),
                "cover": str(meta.get("cover_url") or ""),
                "folder": folder,
                "updated_at": updated,
                "has_summary": True,
            }
        )
    return items


def build_history(query: str = "", limit: int = 60, cursor: str = "", source: str = "") -> dict:
    """合并两类记录，按 bvid(+page) 去重后按更新时间倒序分页。"""
    merged: dict[str, dict] = {}

    def key(bvid: str, page: int) -> str:
        return f"{bvid or '?'}#{page or 1}"

    summary_index: dict[str, dict] = {}
    for meta in _iter_summary_meta():
        summary_index.setdefault(meta["bvid"], meta)

    # source=""        → 全部（总结 + 所有字幕记录）
    # source="processed" → 处理过的：既有总结 + ASR + 与总结同 bvid 的字幕
    # source="asr"       → 仅 ASR
    if source in ("", "processed"):
        for meta in _iter_summary_meta():
            k = key(meta["bvid"], 1)
            merged[k] = {
                "bvid": meta["bvid"],
                "page": 1,
                "title": meta["title"],
                "duration": meta["duration"],
                "author": meta["author"],
                "cover": meta["cover"],
                "folder": meta["folder"],
                "has_summary": True,
                "has_subtitle": False,
                "updated_at": meta["updated_at"],
            }

    for rec in _iter_subtitle_records():
        rec_source = str(rec.get("source") or "")
        if source == "asr" and rec_source != "asr":
            continue
        k = key(str(rec.get("bvid") or ""), int(rec.get("page") or 1))
        item = merged.get(k)
        # processed 模式：只把原生字幕并进"已处理过"的条目，不为纯浏览记录新建卡片
        if item is None and source == "processed" and rec_source != "asr":
            continue
        if item is None:
            item = {
                "bvid": str(rec.get("bvid") or ""),
                "page": int(rec.get("page") or 1),
                "title": str(rec.get("title") or ""),
                "duration": int(rec.get("duration") or 0),
                "author": "",
                "cover": "",
                "folder": "",
                "has_summary": False,
                "has_subtitle": False,
                "updated_at": 0.0,
            }
            merged[k] = item
        item["has_subtitle"] = True
        item["subtitle_source"] = str(rec.get("source") or "")
        item["subtitle_count"] = int(rec.get("count") or 0)
        if rec.get("cover"):
            item["cover"] = str(rec.get("cover") or "")
        if rec.get("author") and not item.get("author"):
            item["author"] = str(rec.get("author") or "")
        # 同一视频若已有总结：只打标 + 借封面/UP 主，不额外新增条目
        meta = summary_index.get(str(rec.get("bvid") or ""))
        if meta:
            item["has_summary"] = True
            if not item.get("cover") and meta.get("cover"):
                item["cover"] = str(meta.get("cover") or "")
            if not item.get("author") and meta.get("author"):
                item["author"] = str(meta.get("author") or "")
        if not item["title"]:
            item["title"] = str(rec.get("title") or "")
        if not item["duration"]:
            item["duration"] = int(rec.get("duration") or 0)
        item["updated_at"] = max(float(item.get("updated_at") or 0), float(rec.get("updated_at") or 0))

    items = list(merged.values())
    q = (query or "").strip().lower()
    if q:
        items = [
            it
            for it in items
            if q in str(it.get("title", "")).lower()
            or q in str(it.get("bvid", "")).lower()
            or q in str(it.get("author", "")).lower()
        ]
    items.sort(key=lambda it: float(it.get("updated_at") or 0), reverse=True)

    start = int(cursor or 0)
    page_items = items[start : start + max(1, min(200, limit))]
    next_cursor = str(start + len(page_items)) if start + len(page_items) < len(items) else ""
    return {"items": page_items, "total": len(items), "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# 旧版产物的兼容：老流程只落了 .ass（没有 subtitles/*.json）
# ---------------------------------------------------------------------------
_ASS_DIALOGUE_RE = re.compile(r"^Dialogue:\s*(.*)$")


def _strip_ass_text(text: str) -> str:
    cleaned = re.sub(r"\{[^}]*\}", "", text or "")
    cleaned = cleaned.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return cleaned.strip()


def _parse_ass_timestamp(value: str) -> float:
    match = re.match(r"^(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d{1,2}))?$", (value or "").strip())
    if not match:
        return 0.0
    hours, minutes, seconds, centis = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if centis:
        total += int(centis) / (10 ** len(centis))
    return float(total)


def parse_ass_file(ass_path: Path) -> list[dict]:
    """读 .ass 的 Dialogue 行，返回 ``[{start,end,text}]``。"""
    segments: list[dict] = []
    try:
        raw = ass_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return segments
    for line in raw.splitlines():
        match = _ASS_DIALOGUE_RE.match(line)
        if not match:
            continue
        fields = match.group(1).split(",", 9)
        if len(fields) < 10:
            continue
        text = _strip_ass_text(fields[9])
        if not text:
            continue
        start = _parse_ass_timestamp(fields[1])
        end = _parse_ass_timestamp(fields[2])
        if end <= start:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segments
