# Project Status

Last updated: June 16, 2026

## What Is Implemented

- Unified module naming in sidebar and headers (`总结 | 收藏 | 浏览`).
- Browse and Favorites card views share the same card system.
- Browse and Favorites both support:
  - Thumbnail view
  - Compact list view
  - Click-through reading view
- Reading action buttons are unified across pages.
- Unfavorite flow:
  - Removed confirmation step
  - Added undo via toast action
  - Enabled in Favorites and Browse favorites category
- Sidebar consistency pass:
  - Unified hover motion and active behavior
  - Unified icon/text alignment baseline
- Global gutter back button:
  - Appears between sidebar and content during reading mode
  - Returns to list without requiring top scroll
- Global state semantics standardized:
  - `processing`, `success`, `failed`, `no_subtitle`, `skipped`, `pending`
- Inline style cleanup:
  - Main UI sizing/spacing moved to tokenized classes
- Telegram bot integration:
  - Settings page supports Bot Token, allowed user IDs, enable switch, and output folder
  - Bot accepts one or multiple Bilibili links and mirrors task progress/errors back to Telegram
  - Unrelated Telegram messages receive the command guide
- Bilibili media download:
  - Replaced yutto CLI invocation with in-process playurl parsing, concurrent range downloads, FFmpeg muxing, and atomic media replacement
  - Download tasks use per-output locks plus global task/chunk limits for batch concurrency safety
- ASR (Automatic Speech Recognition):
  - Local Whisper transcription via faster-whisper
  - Alibaba Cloud Bailian (DashScope) cloud ASR with R2 storage integration
  - Unified ASR entry point with mode switching (local/bailian)
- Folder management:
  - Create/delete folders, move summaries between folders
  - Cascade delete/move of associated files (media, subtitles, detailed summaries)
  - Default folder system replacing hardcoded output directories
- Task logging:
  - Persistent task log system with status tracking (queued/running/done/failed)
  - Task detail modal with event timeline and progress
  - Auto-reload incomplete tasks as failed on restart
- Detailed summary generation:
  - Timestamped Markdown summaries with [[M:SS-H:MM:SS]] markers
  - On-demand generation from summary detail view
  - Modular generation config (normal + detailed)
- Video detail view:
  - Dual-pane layout: local video player + subtitle/detailed summary tabs
  - Clickable subtitle timestamps for video seeking
  - Draggable panel divider with persistence
- UP主 mode redesign:
  - Paginated video grid browsing
  - Selective video submission with checkbox selection
  - Per-video summary status indicators
- Browse batch operations:
  - Edit mode with multi-select checkboxes
  - Batch move to folder and batch delete
- Markdown rendering:
  - Upgraded to marked.js + DOMPurify for GFM support
  - Timestamp link conversion to clickable buttons
- Settings expansion:
  - ASR mode, Whisper, Bailian, R2 storage, Telegram bot, task concurrency
  - MIMO platform auto-detection and API adaptation
  - Hot-reload on settings save
- Sidebar:
  - Collapsible with localStorage persistence
  - Folder-based browse navigation
- Channel/collection support:
  - Auto-detect season_id/series_id URLs
  - Auto-expand single BV to multi-part URLs

## Current UX Baseline

- Cards and reading views should look and behave the same in Browse and Favorites.
- Sidebar items should share the same density, type scale, and interaction feedback.
- Any icon-only control should expose accessible labeling.

## Known Follow-Ups

- Continue global size audit in less-visible regions and utility cleanup.
- Add regression checks for sidebar alignment and card-size consistency.
- Improve responsive behavior for medium-width desktop/tablet layouts.
- Consider extracting a small component layer from repeated template HTML in `static/app.js`.

## Documentation Policy

- Keep README focused on current behavior only.
- Keep design rules in `docs/design-system.md`.
- Keep delivery progress and backlog in this file.
- Update docs in the same PR/commit when UX behavior changes.
