# Design System v1

This document defines the baseline UI contract for BiliSummary. It is intentionally small and practical, so new UI work stays consistent across Browse, Favorites, and Reading pages.

## 1. Core Principles

- One component, one behavior: shared UI patterns must reuse the same class and interaction model.
- Token-first styling: spacing, font size, radius, motion, and focus states come from design tokens.
- Accessibility by default: every interactive element must support keyboard focus and reduced-motion users.
- Consistent density: card sizes, button heights, and list spacing should feel uniform across pages.

## 2. Token Layers

Tokens live in `/Users/jakevin/code/bilibili-summary/static/style.css` under `:root` and `[data-theme="light"]`.

- Typography tokens: `--font-sans`, `--font-mono`, `--text-xs` ... `--text-2xl`
- Spacing tokens: `--space-1` ... `--space-8`
- Motion tokens: `--duration-fast`, `--duration-normal`, `--duration-slow`, `--ease-standard`
- Surface and border tokens: `--bg-*`, `--border*`, `--hover`
- State and semantic tokens: `--accent`, `--success`, `--warning`, `--error`, `--info`
- Interaction tokens: `--interactive-height`, `--focus-ring`

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

### View Toggle

- Base classes: `.browse-view-toggle`, `.fav-view-toggle`
- Toggle buttons: `.browse-view-btn`, `.fav-view-btn`
- Required behavior:
  - Same dimensions and active state visuals.
  - Same tooltip and focus behavior.

## 4. Accessibility Rules

- Focus states: use `:focus-visible` ring for all interactive controls.
- Motion fallback: honor `prefers-reduced-motion: reduce`.
- Screen-reader labels: icon-only buttons must include an accessible label.

## 5. Rules for Future UI Changes

- Do not introduce hardcoded spacing/font values unless a token is first added.
- Do not create new card variants when an existing shared card can be reused.
- If Browse and Favorites diverge in behavior, align both to the shared interaction pattern.
- When adding a new view (e.g., timeline, grouped list), add it as a mode of the shared list system.

## 6. Next Iteration (v2)

- Extract repeated utility patterns into clearer DS sections (`layout`, `surface`, `interactive`, `feedback`).
- Add explicit empty/loading/error state primitives.
- Introduce responsive breakpoints as tokens and normalize mobile spacing.
