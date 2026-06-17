# Design System v1.1

This document defines the active UI contract for BiliSummary. It is intentionally practical, so new UI work stays consistent across Summarize, Favorites, Browse, and Reading flows.

## 1. Core Principles

- One component, one behavior: shared UI patterns must reuse the same class and interaction model.
- Token-first styling: spacing, font size, radius, motion, and focus states come from design tokens.
- Accessibility by default: every interactive element must support keyboard focus and reduced-motion users.
- Consistent density: card sizes, button heights, and list spacing should feel uniform across pages.
- No inline style drift: use CSS classes and tokens instead of one-off inline values.

## 2. Token Layers

Tokens live in `/Users/jakevin/code/bilibili-summary/static/style.css` under `:root` and `[data-theme="light"]`.

- Typography tokens: `--font-sans`, `--font-mono`, `--text-xs` ... `--text-2xl`
- Spacing tokens: `--space-1` ... `--space-8`
- Motion tokens: `--duration-fast`, `--duration-normal`, `--duration-slow`, `--ease-standard`
- Surface and border tokens: `--bg-*`, `--border*`, `--hover`
- State and semantic tokens: `--accent`, `--success`, `--warning`, `--error`, `--info`
- Interaction tokens: `--interactive-height`, `--focus-ring`
- Layout token: `--sidebar-width`

## 3. Component Contracts

### Buttons

- Base class: `.btn`
- Variants: `.btn-primary`, `.btn-secondary`, `.btn-footer`, `.action-btn-*`
- Required behavior:
  - Minimum interactive height must align with `--interactive-height` (except compact action chips).
  - Hover and transition must use tokenized motion.
  - Keyboard focus must be visible via focus ring.

### Inputs

- Base class: `.input` and `textarea`
- Required behavior:
  - Shared height, padding, font size, and border radius.
  - Focus state uses `--focus-ring`.

### Cards

- Base class: `.card` for container panels.
- Content cards: `.video-card` (shared by Browse and Favorites thumbnail view).
- Required behavior:
  - Same hover elevation model.
  - Same title + meta structure and truncation behavior.
  - Same status badge and cover fallback behavior.

### View Toggle

- Base classes: `.browse-view-toggle`, `.fav-view-toggle`
- Toggle buttons: `.browse-view-btn`, `.fav-view-btn`
- Required behavior:
  - Same dimensions and active state visuals.
  - Same tooltip and focus behavior.

### Sidebar Navigation

- Base groups:
  - Static pages: `.nav-item`
  - Browse categories: `.nav-parent`, `.nav-child`
  - Favorite folders: `.fav-folder-item`, `.fav-folder-toggle`
- Required behavior:
  - Unified text baseline and icon slot width.
  - Unified hover motion (`translateX`) and active-state indicator.
  - Collapsible arrows use icon rotation, not text glyph swapping.

### UI States

- Shared state primitive: `.ui-state` (`loading`, `empty`, `error`).
- Use JS helper `renderState(container, config)` instead of ad-hoc inline HTML.
- Required behavior:
  - Loading, empty, and error states must use the same visual structure.
- Optional retry action uses the same button contract as other controls.

### Reading Navigation

- Top local back button remains in reading header for context.
- Global gutter back button (`#globalBackBtn`, `.gutter-back-btn`) appears when any reading panel is active.
- Required behavior:
  - Hidden outside reading mode.
  - One-click return to list without scrolling to top.

### Status Semantics

- Product-wide status vocabulary:
  - `processing` => `处理中`
  - `success` => `成功`
  - `failed` => `失败`
  - `no_subtitle` => `无字幕`
  - `skipped` => `已跳过`
- Use JS helpers `normalizeStatus()` and `statusText()` to avoid per-module wording drift.

## 4. Accessibility Rules

- Focus states: use `:focus-visible` ring for all interactive controls.
- Motion fallback: honor `prefers-reduced-motion: reduce`.
- Screen-reader labels: icon-only buttons must include an accessible label.
- Do not rely on color-only cues for critical state.

## 5. Rules for Future UI Changes

- Do not introduce hardcoded spacing/font values unless a token is first added.
- Do not create new card variants when an existing shared card can be reused.
- If Browse and Favorites diverge in behavior, align both to the shared interaction pattern.
- When adding a new view (e.g., timeline, grouped list), add it as a mode of the shared list system.
- Any temporary UI experiment must either be tokenized or removed before merge.

## 6. Current Gaps / Next Iteration (v2)

- Extract utility classes into clearer sections (`layout`, `surface`, `interactive`, `feedback`) and reduce selector sprawl.
- Normalize responsive behavior for tablet and narrow desktop widths.
- Expand reusable state components for long-running operations and retry states.
- Add visual regression snapshots for sidebar + card density consistency.
