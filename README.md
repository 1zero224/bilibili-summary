# BiliSummary

Desktop-first Bilibili summarizer with AI-generated Markdown output, favorites workflow, and unified browse/reading UX.

## Features

- URL mode: summarize videos from pasted Bilibili URLs.
- UP mode: summarize recent videos by UP name or UID.
- Favorites mode:
  - QR login to Bilibili.
  - Load favorite folders and videos.
  - Summarize unsummarized favorites in batch.
  - Unfavorite with short-window undo.
- Browse mode:
  - Unified card system with Favorites.
  - Thumbnail / compact view toggle.
  - Click-through reading page.
- Reading UX:
  - Unified top action buttons.
  - Global gutter back button between sidebar and content.
- ASR fallback:
  - For videos without subtitles, trigger speech-to-text summarize flow.

## Stack

- Backend: FastAPI + Uvicorn
- Frontend: Vanilla JS + CSS (tokenized design system)
- Desktop shell: pywebview
- Bilibili integration: `bilibili-api-python`
- AI summarize: Anthropic-compatible API
- ASR: GLM ASR integration
- Audio: PyAV

## Quick Start

1. Install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Create `.env.local`:

```env
ANTHROPIC_AUTH_TOKEN=your_api_key
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
```

3. Run desktop app:

```bash
python app.py
```

## Project Layout

```text
app.py                # Desktop entry (pywebview)
server.py             # FastAPI app
summarize.py          # Summarization pipeline
routes/               # API route modules
static/               # Frontend assets (index.html / app.js / style.css)
docs/                 # Design system and project status
summary/              # Generated summaries
```

## Documentation

- Design system: `/Users/jakevin/code/bilibili-summary/docs/design-system.md`
- Current status and next tasks: `/Users/jakevin/code/bilibili-summary/docs/project-status.md`

## License

MIT
