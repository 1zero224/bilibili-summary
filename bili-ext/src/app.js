/**
 * 面板 UI（跑在扩展自己的 iframe 里，与 B 站页面完全隔离）。
 *
 * 为什么用 iframe：参考实现（IndieKKY/bilibili-subtitle）就是这么做的——
 * UI 的 DOM、CSS、滚动、JS 都在独立文档里，绝不会影响 B 站自己的封面/UP 主信息/评论区。
 * 本文件只负责渲染；数据与动作全部通过 postMessage 与父级 content script 交互：
 *
 *   parent → 本页: {__bs:'state'|'time'|'task'}
 *   本页 → parent: {__bs:'ready'|'seek'|'reload'|'asr'|'summary'|'collapse'|'options'|'copy'}
 */
(function () {
  "use strict";
  const root = document.getElementById("root");

  const state = {
    video: null,
    segments: [],
    rows: [],
    source: "",
    label: "",
    via: "",
    status: "",
    error: "",
    sync: "",
    syncWarn: false,
    prefs: { tab: "sub", merged: true, collapsed: true, theme: "auto" },
    active: -1,
    time: 0,
    task: null,
    summary: { data: null, loading: false, pending: false, error: "", status: "" },
  };

  const el = {};
  let built = false;

  const h = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function post(payload) {
    try {
      window.parent.postMessage(payload, "*");
    } catch (_) {
      /* ignore */
    }
  }

  function toast(text) {
    if (!el.toast) return;
    el.toast.textContent = text;
    el.toast.className = "toast show";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.toast.className = "toast";
    }, 2000);
  }

  // ---------- 主题 ----------
  // 主题只由一条路径决定：prefs.theme + 系统偏好；prefs 更新或系统主题切换时都要重算，
  // 否则浅色/深色会卡在首次构建时的状态。
  function applyTheme() {
    if (!el.panel) return;
    el.panel.dataset.theme =
      state.prefs.theme === "dark" ||
      (state.prefs.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches)
        ? "dark"
        : "light";
  }

  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  // ---------- 构建 ----------
  function build() {
    const style = document.createElement("style");
    style.textContent = BS.CSS;
    document.head.appendChild(style);

    el.panel = h("div", "panel dock");
    applyTheme();

    // 第一行：分段选项卡 + 折叠
    el.topbar = h("div", "topbar");
    el.tabs = h("div", "tabs");
    el.tabSub = h("button", "tab", "字幕");
    el.tabSum = h("button", "tab", "总结");
    el.tabSub.addEventListener("click", () => switchTab("sub"));
    el.tabSum.addEventListener("click", () => switchTab("sum"));
    el.tabs.append(el.tabSub, el.tabSum);
    el.historyBtn = h("button", "icon", "↗");
    el.historyBtn.title = "打开历史页面（服务器）";
    el.historyBtn.addEventListener("click", () => post({ __bs: "history" }));
    el.settingsBtn = h("button", "icon lg", "⚙");
    el.settingsBtn.title = "设置";
    el.settingsBtn.addEventListener("click", () => post({ __bs: "options" }));
    el.foldBtn = h("button", "icon chev", "▾");
    el.foldBtn.title = "展开/折叠";
    el.foldBtn.addEventListener("click", () => {
      const collapsed = !state.prefs.collapsed;
      state.prefs.collapsed = collapsed;
      applyLayout();
      post({ __bs: "collapse", collapsed });
    });
    el.topbar.append(el.tabs, h("span", "grow"), el.historyBtn, el.settingsBtn, el.foldBtn);

    // 工具条
    el.toolbar = h("div", "toolbar");
    const copyAll = h("button", "", "复制");
    el.copyBtn = copyAll;
    copyAll.title = "复制全文（带时间轴）";
    copyAll.addEventListener("click", copyAllText);
    const mergeBtn = h("button", "", "合并");
    mergeBtn.addEventListener("click", () => {
      state.prefs.merged = !state.prefs.merged;
      buildRows();
      render();
    });
    el.mergeBtn = mergeBtn;
    mergeBtn.title = "合并成句 / 逐条原文 切换";
    const sel = h("select", "sel");
    for (const [v, label] of [
      ["srt", "srt"],
      ["ass", "ass"],
      ["vtt", "vtt"],
      ["txt", "txt"],
      ["txt-time", "txt+时间"],
      ["json", "json"],
    ]) {
      const o = h("option", "", label);
      o.value = v;
      sel.appendChild(o);
    }
    el.sel = sel;
    const dl = h("button", "", "下载");
    dl.addEventListener("click", () => download(sel.value));
    const group = h("div", "group");
    group.append(sel, dl);
    el.group = group;
    const asrBtn = h("button", "primary", "获取字幕");
    asrBtn.title = "让服务器下载音频并转写（ASR）";
    asrBtn.addEventListener("click", () => {
      asrBtn.disabled = true;
      asrBtn.textContent = "提交中…";
      post({ __bs: "asr" });
      setTimeout(() => {
        asrBtn.disabled = false;
        asrBtn.textContent = "获取字幕";
      }, 4000);
    });
    el.asrBtn = asrBtn;
    const reload = h("button", "icon", "↻");
    reload.title = "重载字幕";
    reload.addEventListener("click", () => post({ __bs: "reload" }));
    el.toolbar.append(copyAll, mergeBtn, group, asrBtn, reload);

    el.status = h("div", "status", "");
    el.statusText = h("span", "", "");
    el.syncTag = h("span", "", "");
    el.syncTag.style.cursor = "pointer";
    el.syncTag.addEventListener("click", () => post({ __bs: "options" }));
    el.syncTag.style.display = "none";
    el.status.append(el.statusText, el.syncTag);
    el.list = h("div", "list");
    el.list.addEventListener("wheel", () => {}, { passive: true });

    el.summary = h("div", "summary");
    el.summary.style.display = "none";
    el.summary.addEventListener("click", (e) => {
      const chip = e.target && e.target.classList && e.target.classList.contains("chip") ? e.target : null;
      if (chip) {
        const sec = Number(chip.dataset.t);
        if (Number.isFinite(sec)) post({ __bs: "seek", t: sec });
      }
    });

    el.toast = h("div", "toast", "");

    el.panel.append(el.topbar, el.toolbar, el.status, el.list, el.summary, el.toast);
    el.panel.classList.add("enter");
    root.appendChild(el.panel);
    built = true;
    applyLayout();
    render();
  }

  function applyLayout() {
    if (!built) return;
    const folded = !!state.prefs.collapsed;
    el.toolbar.style.display = folded || state.prefs.tab === "sum" ? "none" : "";
    el.list.style.display = folded || state.prefs.tab === "sum" ? "none" : "";
    el.summary.style.display = folded || state.prefs.tab !== "sum" ? "none" : "";
    el.status.style.display = folded || state.prefs.tab === "sum" ? "none" : "";
    el.foldBtn.textContent = folded ? "▾" : "▴";
    el.mergeBtn.textContent = state.prefs.merged ? "逐条" : "合并";
    el.tabSub.setAttribute("aria-selected", state.prefs.tab === "sub" ? "true" : "false");
    el.tabSum.setAttribute("aria-selected", state.prefs.tab === "sum" ? "true" : "false");
  }

  function switchTab(tab) {
    state.prefs.tab = tab;
    if (state.prefs.collapsed) {
      state.prefs.collapsed = false;
      post({ __bs: "collapse", collapsed: false });
    }
    applyLayout();
    render();
    if (tab === "sum") post({ __bs: "summary", force: false });
  }

  // ---------- 渲染 ----------
  function buildRows() {
    state.rows = state.prefs.merged
      ? BS.mergeSegments(state.segments, { gap: 0.8, maxLen: 42 })
      : state.segments.map((s) => ({ start: s.start, end: s.end, text: s.text, music: s.music, raw: [s] }));
  }

  function renderSub() {
    el.list.replaceChildren();
    state.active = -1;

    if (state.error) {
      const box = h("div", "empty");
      box.append(h("div", "err", state.error));
      const retry = h("button", "", "重试");
      retry.addEventListener("click", () => post({ __bs: "reload" }));
      box.append(retry);
      el.list.appendChild(box);
      return;
    }
    if (!state.rows.length) {
      const box = h("div", "empty");
      box.append(
        h("div", "", "这个视频没有可用字幕。"),
        h("div", "", "可以点上方「获取字幕」，由服务器 ASR 生成。"),
      );
      el.list.appendChild(box);
      return;
    }
    const frag = document.createDocumentFragment();
    for (let i = 0; i < state.rows.length; i += 1) {
      const r = state.rows[i];
      const btn = h("button", `row${r.music ? " music" : ""}`);
      btn.dataset.i = String(i);
      btn.style.setProperty("--bs-i", String(Math.min(i, 24)));
      btn.append(h("span", "t", BS.fmtTime(r.start)), h("span", "x", r.text));
      btn.addEventListener("click", () => post({ __bs: "seek", t: r.start }));
      frag.appendChild(btn);
    }
    el.list.appendChild(frag);
  }

  function renderSummary() {
    el.summary.replaceChildren();
    const s = state.summary;
    if (s.loading) {
      el.summary.appendChild(h("div", "empty", "读取总结…"));
      return;
    }
    if (s.error) el.summary.appendChild(h("div", "err", s.error));
    if (s.pending) {
      el.summary.appendChild(h("div", "empty", `生成中… ${s.status || ""}`));
      return;
    }
    const detailed = s.data && (s.data.detailed || s.data.summary);
    const actions = h("div", "sum-actions");
    const btn = h("button", "primary", detailed ? "重新生成" : "生成本视频总结");
    btn.addEventListener("click", () => {
      s.pending = true;
      s.status = "提交任务…";
      renderSummary();
      post({ __bs: "summary", force: !!detailed });
    });
    actions.appendChild(btn);
    if (detailed) {
      const cp = h("button", "", "复制总结");
      cp.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(detailed);
          toast("已复制总结");
        } catch (_) {
          toast("复制失败");
        }
      });
      actions.appendChild(cp);
    }
    el.summary.appendChild(actions);
    if (!detailed) {
      el.summary.appendChild(h("div", "empty", "还没有总结。点上面按钮生成（带可点击时间段）。"));
      return;
    }
    const body = h("div");
    body.innerHTML = BS.renderMarkdown(detailed);
    el.summary.appendChild(body);
  }

  function render() {
    if (!built) return;
    applyLayout();
    if (state.prefs.tab === "sum") renderSummary();
    else renderSub();
  }

  /** 只在面板自己内部滚动，永不触碰页面滚动（这是之前把 B 站顶栏滚走的元凶）。 */
  function highlight(idx) {
    const rows = el.list.querySelectorAll(".row");
    rows.forEach((n) => n.classList.remove("active"));
    if (idx < 0 || idx >= rows.length) return;
    const node = rows[idx];
    node.classList.add("active");
    const box = el.list;
    const target = node.offsetTop - (box.clientHeight - node.offsetHeight) / 2;
    const max = Math.max(0, box.scrollHeight - box.clientHeight);
    const top = Math.max(0, Math.min(max, target));
    if (Math.abs(box.scrollTop - top) < 4) return;
    try {
      box.scrollTo({ top, behavior: "smooth" });
    } catch (_) {
      box.scrollTop = top;
    }
  }

  function payloadForExport(kind) {
    return {
      title: (state.video && state.video.title) || "subtitle",
      url: (state.video && state.video.url) || "",
      author: (state.video && state.video.author) || "",
      rows: state.rows,
      segments: state.segments,
    };
  }

  async function copyAllText() {
    if (!state.rows.length) return toast("没有字幕可复制");
    try {
      await navigator.clipboard.writeText(BS.buildExport("txt-time", payloadForExport()));
      toast(`已复制 ${state.rows.length} 行`);
    } catch (_) {
      toast("复制失败");
    }
  }

  function download(kind) {
    if (!state.rows.length) return toast("没有字幕可下载");
    const p = payloadForExport(kind);
    const ext = kind === "json" ? "json" : kind === "txt-time" ? "txt" : kind;
    const suffix = state.video && state.video.p > 1 ? `-P${state.video.p}` : "";
    BS.download(`${BS.safeName(p.title)}${suffix}.${ext}`, BS.buildExport(kind, p));
  }

  // ---------- 与父级（content script）通信 ----------
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data.__bs !== "string") return;
    if (data.__bs === "state") {
      const d = data.data || {};
      const prevKey = state.video ? `${state.video.bvid}_${state.video.p}` : "";
      state.video = d.video || null;
      state.segments = d.segments || [];
      state.source = d.source || "";
      state.label = d.label || "";
      state.via = d.via || "";
      state.status = d.status || "";
      state.error = d.error || "";
      state.sync = d.sync || "";
      state.syncWarn = !!d.syncWarn;
      state.prefs = { ...state.prefs, ...(d.prefs || {}) };
      applyTheme();
      const nextKey = state.video ? `${state.video.bvid}_${state.video.p}` : "";
      if (prevKey !== nextKey) state.summary = { data: null, loading: false, pending: false, error: "", status: "" };
      buildRows();
      if (!built) build();
      else {
        applyLayout();
        render();
      }
      updateStatus();
      return;
    }
    if (data.__bs === "time") {
      state.time = Number(data.t) || 0;
      if (state.prefs.tab !== "sub" || state.prefs.collapsed) return;
      const idx = BS.indexOfTime(state.rows, state.time);
      if (idx === state.active) return;
      state.active = idx;
      highlight(idx);
      return;
    }
    if (data.__bs === "task") {
      const t = data.data || {};
      if (t.kind === "summary") {
        if (t.status === "failed") {
          state.summary.pending = false;
          state.summary.error = `生成失败：${t.error || "未知错误"}`;
        } else if (t.status === "done") {
          state.summary.pending = false;
        } else {
          state.summary.status = t.step || t.status || "";
        }
        renderSummary();
      } else {
        if (el.statusText) el.statusText.textContent = `转写中… ${t.step || t.status || ""}`;
      }
      return;
    }
    if (data.__bs === "summary") {
      state.summary.data = data.data || null;
      state.summary.loading = false;
      state.summary.pending = false;
      state.summary.error = data.error || "";
      renderSummary();
      return;
    }
    if (data.__bs === "sync") {
      state.sync = data.text || "";
      state.syncWarn = !!data.warn;
      updateStatus();
    }
  });

  function updateStatus() {
    if (!el.status || !el.syncTag || !el.statusText) return;
    const text = state.error
      ? ""
      : [state.label, state.segments.length ? `${state.segments.length} 条` : "", state.via ? `via ${state.via}` : ""]
          .filter(Boolean)
          .join(" · ") || state.status || "";
    el.statusText.textContent = text;
    const has = state.segments.length > 0;
    el.asrBtn.style.display = "";
    el.asrBtn.className = has ? "" : "primary";
    el.asrBtn.textContent = "获取字幕";
    if (el.copyBtn) el.copyBtn.style.display = has ? "" : "none";
    if (el.mergeBtn) el.mergeBtn.style.display = has ? "" : "none";
    if (el.group) el.group.style.display = has ? "" : "none";

    // 只显示"需要你知道"的同步异常；成功状态不再打扰
    const sync = state.sync || "";
    const bad = state.syncWarn && sync && !/回传中/.test(sync);
    el.syncTag.style.display = bad ? "" : "none";
    if (bad) {
      el.syncTag.className = "tag warn";
      el.syncTag.textContent = sync;
      el.syncTag.title = "点击打开设置";
    }
  }

  // 首次上报就绪：父级收到后开始推状态
  post({ __bs: "ready" });
})();
