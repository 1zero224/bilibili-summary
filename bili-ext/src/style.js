/**
 * 面板样式（注入 Shadow Root，与 B 站页面 CSS 完全隔离）。
 * 统一走 token：浅/深色各一套，组件只用变量，不再各处写 rgba。
 */
globalThis.BS = globalThis.BS || {};

globalThis.BS.CSS = `
:host {
  --bg: #ffffff;
  --bg-soft: #f4f6f8;
  --bg-hover: #eef1f5;
  --line: rgba(16, 24, 40, .09);
  --text: #14161a;
  --text-2: #5c6472;
  --text-3: #98a0ac;
  --accent: #00aeec;
  --accent-ink: #ffffff;
  --accent-soft: rgba(0, 174, 236, .10);
  --warn: #e8890c;
  --ok: #12b886;
  --danger: #e5484d;
  --radius: 16px;
  --radius-sm: 9px;
  --shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 10px 28px rgba(16, 24, 40, .10);
  --chevron: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.75 6 7.75l3-3' fill='none' stroke='%2398a0ac' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  --thumb: rgba(139, 148, 160, .40);
  --thumb-hover: rgba(110, 120, 134, .62);
}
.panel[data-theme="dark"] {
  --bg: #17191d;
  --bg-soft: #21252b;
  --bg-hover: #2a2f36;
  --line: rgba(255, 255, 255, .09);
  --text: #e8eaee;
  --text-2: #a2a9b4;
  --text-3: #6f7683;
  --accent: #3bc1ef;
  --accent-ink: #08181f;
  --accent-soft: rgba(59, 193, 239, .15);
  --warn: #f5a524;
  --ok: #3ddc97;
  --danger: #ff6b6b;
  --shadow: 0 1px 2px rgba(0, 0, 0, .45), 0 12px 30px rgba(0, 0, 0, .5);
  --chevron: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.75 6 7.75l3-3' fill='none' stroke='%236f7683' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  --thumb: rgba(255, 255, 255, .22);
  --thumb-hover: rgba(255, 255, 255, .38);
}

:host, * { box-sizing: border-box; }

/* ---------- 容器 ---------- */
.panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  overflow: hidden;
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  font: 13px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "HarmonyOS Sans SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.panel.dock {
  position: relative;          /* host 负责绝对定位，面板填满 host */
  width: 100%;
  max-height: var(--bs-dock-h, 520px);
}
.panel.float {
  position: fixed;
  right: var(--bs-right, 16px);
  top: var(--bs-top, 88px);
  width: var(--bs-width, 380px);
  max-height: var(--bs-float-h, 72vh);
  z-index: 2147483000;
}

/* ---------- 第一行：分段选项卡 + 图标按钮 ---------- */
.topbar {
  display: flex; align-items: center; gap: 7px;
  padding: 8px 10px;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
.panel.float .topbar { cursor: move; }
.topbar .grow { flex: 1 1 auto; }

.tabs {
  display: inline-flex; gap: 2px; padding: 2px;
  background: var(--bg-soft);
  border-radius: 10px;
}
.tab {
  border: 0; background: transparent;
  color: var(--text-2);
  font-size: 13px; font-weight: 500; line-height: 1;
  padding: 6px 13px; border-radius: 8px;
  cursor: pointer;
  transition: background .16s ease, color .16s ease, box-shadow .16s ease;
}
.tab:hover { color: var(--text); }
.tab[aria-selected="true"] {
  background: var(--bg);
  color: var(--accent);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .12);
}

/* ---------- 通用按钮 ---------- */
button {
  font: inherit; font-size: 12px; line-height: 1;
  padding: 7px 11px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  transition: background .16s ease, color .16s ease, border-color .16s ease;
}
button:hover:not(:disabled) { background: var(--bg-hover); color: var(--text); }
button:active:not(:disabled) { transform: translateY(.5px); }
button:disabled { opacity: .45; cursor: default; }
button.primary {
  background: var(--accent); color: var(--accent-ink); font-weight: 600;
  border-color: transparent;
}
button.primary:hover:not(:disabled) { background: var(--accent); filter: brightness(1.06); }

/* 顶栏两个图标按钮 */
.topbar > button { padding: 6px 9px; color: var(--text-3); }
.topbar > button:hover:not(:disabled) { color: var(--text); }

/* ---------- 工具条 ---------- */
.toolbar {
  display: flex;
  flex-wrap: nowrap;            /* 单行，不换行 */
  align-items: center;
  gap: 5px;
  padding: 7px 10px;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
  overflow-x: auto;             /* 极端窄屏时可横向滚，但平时不会出现 */
  scrollbar-width: none;
}
.toolbar::-webkit-scrollbar { display: none; }
.toolbar > button { flex: 0 0 auto; white-space: nowrap; padding: 6px 9px; }
.toolbar > .group { flex: 0 0 auto; }
/* 下拉 + 下载按钮组成一个胶囊 */
.group {
  display: inline-flex; align-items: stretch;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg);
}
.group > button {
  border-radius: 0; border: 0; padding: 6px 9px; white-space: nowrap;
}
.group > * + * { border-left: 1px solid var(--line); }
.sel {
  appearance: none; -webkit-appearance: none;
  font: inherit; font-size: 12px; line-height: 1;
  padding: 6px 21px 6px 8px;
  border: 0; border-radius: 0;
  background-color: transparent;
  color: var(--text);
  background-image: var(--chevron);
  background-repeat: no-repeat;
  background-position: right 7px center;
  background-size: 11px;
  cursor: pointer;
}
.sel:focus-visible { outline: 2px solid var(--accent-soft); }
.sel option { background: var(--bg); color: var(--text); }

/* ---------- 状态行 ---------- */
.status {
  flex: 0 0 auto;
  padding: 6px 12px 2px;
  font-size: 11.5px;
  color: var(--text-3);
}

/* ---------- 字幕列表 ---------- */
.list {
  position: relative; /* 让 .row 的 offsetTop 相对列表容器 */
  flex: 1 1 auto;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 4px 0 6px;
}

/* 滚动条：细圆角拇指 + 透明轨道，浅/深色都跟随 token */
.list, .summary {
  scrollbar-width: thin;
  scrollbar-color: var(--thumb) transparent;
}
.list::-webkit-scrollbar, .summary::-webkit-scrollbar { width: 10px; height: 10px; }
.list::-webkit-scrollbar-track, .summary::-webkit-scrollbar-track { background: transparent; }
.list::-webkit-scrollbar-thumb, .summary::-webkit-scrollbar-thumb {
  background-color: var(--thumb);
  border-radius: 999px;
  border: 3px solid transparent;
  background-clip: content-box;
  transition: background-color .16s ease;
}
.list::-webkit-scrollbar-thumb:hover, .summary::-webkit-scrollbar-thumb:hover {
  background-color: var(--thumb-hover);
  background-clip: content-box;
}

.row {
  position: relative;
  display: flex; align-items: flex-start; gap: 10px;
  width: 100%;
  padding: 6px 12px 6px 14px;
  border: 0; background: none;
  color: inherit; text-align: left;
  border-radius: 0;
  cursor: pointer;
  transition: background .12s ease;
}
.row:hover { background: var(--bg-soft); }
.row .t {
  flex: 0 0 auto; min-width: 42px;
  font-size: 11.5px; font-variant-numeric: tabular-nums;
  color: var(--text-3); padding-top: 2px;
}
.row .x { flex: 1 1 auto; word-break: break-word; }
.row.active { background: var(--accent-soft); }
.row.active::before {
  content: ""; position: absolute; left: 0; top: 5px; bottom: 5px; width: 3px;
  border-radius: 0 3px 3px 0; background: var(--accent);
}
.row.active .t { color: var(--accent); font-weight: 600; }
.row.music .x { color: var(--text-3); font-style: italic; }

/* ---------- 空状态 / 错误 ---------- */
.empty {
  padding: 18px 16px 20px;
  color: var(--text-2);
  font-size: 12.5px;
  line-height: 1.85;
}
.empty > button { margin: 8px 6px 0 0; border: 1px solid var(--line); }
.err { color: var(--danger); }

/* ---------- 总结正文 ---------- */
.summary {
  flex: 1 1 auto;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 6px 14px 16px;
  font-size: 13px; line-height: 1.8;
}
.summary h2 { font-size: 15px; margin: 16px 0 6px; letter-spacing: .2px; }
.summary h3 { font-size: 13.5px; margin: 14px 0 4px; }
.summary h4 { font-size: 12.5px; margin: 12px 0 4px; color: var(--text-2); }
.summary p { margin: 6px 0; }
.summary ul { margin: 4px 0 10px; padding-left: 18px; }
.summary li { margin: 3px 0; }
.summary li::marker { color: var(--text-3); }
.summary blockquote {
  margin: 10px 0; padding: 6px 10px;
  border-left: 3px solid var(--accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--accent-soft);
  color: var(--text-2);
}
.summary hr { border: 0; border-top: 1px solid var(--line); margin: 14px 0; }
.summary code {
  background: var(--bg-soft); padding: 1px 5px; border-radius: 5px; font-size: 12px;
}
.summary .ts { color: var(--text-3); font-variant-numeric: tabular-nums; }

.chip {
  display: inline-flex; align-items: center;
  margin: 0 2px; padding: 1px 8px;
  border: 0; border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11.5px; font-weight: 600;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.chip:hover { background: var(--accent-soft); filter: brightness(1.08); }

.sum-actions {
  display: flex; gap: 6px; align-items: center;
  padding: 8px 0 10px; flex: 0 0 auto;
}
.sum-actions > button { border: 1px solid var(--line); }
.sum-actions > button.primary { border-color: transparent; }

/* ---------- 顶栏图标按钮 ---------- */
button.icon {
  width: 26px; height: 26px; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 13px; line-height: 1;
  color: var(--text-3);
  border-radius: 8px;
}
button.icon:hover:not(:disabled) { color: var(--text); background: var(--bg-hover); }
button.icon.lg { width: 30px; height: 30px; font-size: 16px; }
button.icon.chev { width: 28px; height: 28px; font-size: 12px; }

/* 状态行里的小提示（只在同步异常时出现） */
.status .tag {
  display: inline-flex; align-items: center; gap: 5px;
  margin-left: 8px; padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px; font-weight: 600;
}
.status .tag::before {
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: currentColor;
}
.status .tag.warn { background: rgba(245, 165, 36, .14); color: var(--warn); }

/* ---------- Toast ---------- */
.toast {
  position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%) translateY(4px);
  max-width: 88%;
  padding: 7px 13px;
  border-radius: 999px;
  background: rgba(20, 22, 26, .92);
  color: #fff;
  font-size: 12px;
  opacity: 0;
  pointer-events: none;
  transition: opacity .18s ease, transform .18s ease;
}
.panel[data-theme="dark"] .toast { background: rgba(240, 243, 247, .95); color: #14161a; }
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

/* ---------- 动画 ---------- */
@keyframes bs-panel-in {
  from { opacity: 0; transform: translateY(6px) scale(.995); }
  to { opacity: 1; transform: none; }
}
@keyframes bs-fade-in {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: none; }
}
@keyframes bs-row-in {
  from { opacity: 0; transform: translateX(-3px); }
  to { opacity: 1; transform: none; }
}
.panel.enter { animation: bs-panel-in .24s cubic-bezier(.2, .8, .3, 1) both; }

/* 列表入场：只给前 24 行做错落动画，避免长列表开销 */
.list > .row { animation: bs-row-in .2s ease-out both; }
.list > .row:nth-child(-n+24) { animation-delay: calc(var(--bs-i, 0) * 8ms); }
.list > .empty, .summary > * { animation: bs-fade-in .18s ease-out both; }

/* 选项卡切换时内容轻微淡入 */
.summary h2, .summary p, .summary ul, .summary blockquote { animation: bs-fade-in .2s ease-out both; }

/* 折叠/展开时的高度过渡由父级 iframe 负责；面板内部做一点收拢感 */
.toolbar, .list, .summary, .status { transition: opacity .16s ease; }

@media (prefers-reduced-motion: reduce) {
  .panel.enter, .list > .row, .summary > *, .summary h2, .summary p, .summary ul, .summary blockquote {
    animation: none !important;
  }
  .toolbar, .list, .summary, .status, .toast { transition: none !important; }
}
`;
