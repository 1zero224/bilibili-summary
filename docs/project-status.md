# Project Status

Last updated: February 15, 2026

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
