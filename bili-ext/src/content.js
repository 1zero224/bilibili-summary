/**
 * content script：只做"搬运"，不往 B 站 DOM 里塞任何 UI。
 *
 * 架构照抄参考实现（IndieKKY/bilibili-subtitle）：
 *   - UI 是 **iframe**（chrome.runtime.getURL('panel.html')），插进 #danmukuBox 的第一个子节点；
 *     样式/滚动/JS 全在独立文档里 → 不会影响 B 站封面、UP 主信息、评论区。
 *   - iframe 折叠时 44px，展开时跟随播放器高度（320–680）。
 *   - 找 #danmukuBox 每秒重试；找到后再等 1.5s 才插入（参考实现注释：插太快 B 站会自己刷新页面）。
 *
 * 本文件负责：取字幕（页面上下文，net.js）、播放时间推送、seek、ASR/总结任务、与服务器同步。
 * 与 iframe 之间用 window.postMessage 通信。
 */
(function () {
  "use strict";
  if (!globalThis.BS || !BS.resolveVideo) return;
  if (window.__bsContentInstalled) return;
  window.__bsContentInstalled = true;

  const IFRAME_ID = "bs-panel-iframe";
  const COLLAPSED_HEIGHT = 44;
  const MIN_HEIGHT = 320;
  const MAX_HEIGHT = 680;

  const DEFAULT_PREFS = {
    prefsVersion: 2,
    mode: "embed",
    serverUrl: "",
    token: "",
    theme: "auto",
    width: 380,
    merged: true,
    follow: true,
    collapsed: false,
    tab: "sub",
  };

  const state = {
    prefs: { ...DEFAULT_PREFS },
    video: null,
    track: null,
    langSource: "",
    segments: [],
    status: "",
    error: "",
    via: "",
    sync: "",
    syncWarn: false,
    loading: false,
    importedKey: "",
    href: location.href,
    asrRunning: false,
    floating: false,
  };

  let iframe = null;
  let iframeReady = false;
  let lastTime = -1;

  const taskHandlers = new Map();

  // ---------- 小工具 ----------
  function videoEl() {
    return (
      document.querySelector("#bilibili-player video") ||
      document.querySelector(".bpx-player-video-wrap video") ||
      document.querySelector("video")
    );
  }

  function postToPanel(payload) {
    if (!iframe || !iframe.contentWindow) return;
    try {
      iframe.contentWindow.postMessage(payload, "*");
    } catch (_) {
      /* ignore */
    }
  }

  async function loadPrefs() {
    try {
      const got = await chrome.storage.local.get("bs:prefs");
      state.prefs = { ...DEFAULT_PREFS, ...((got && got["bs:prefs"]) || {}) };
    } catch (_) {
      /* ignore */
    }
  }

  function savePrefs(patch) {
    state.prefs = { ...state.prefs, ...patch };
    chrome.storage.local.set({ "bs:prefs": state.prefs }).catch(() => {});
  }

  // ---------- iframe 挂载 ----------
  function setIframeHeight() {
    if (!iframe) return;
    if (state.prefs.collapsed) {
      iframe.style.height = `${COLLAPSED_HEIGHT}px`;
      return;
    }
    if (state.floating) {
      iframe.style.height = "520px";
      return;
    }
    const v = videoEl();
    const h = v && v.offsetHeight ? v.offsetHeight : 480;
    iframe.style.height = `${Math.min(Math.max(h, MIN_HEIGHT), MAX_HEIGHT)}px`;
  }

  function createIframe() {
    const box = document.getElementById("danmukuBox");
    if (!box) return false;

    if (document.getElementById(IFRAME_ID)) {
      iframe = document.getElementById(IFRAME_ID);
      return true;
    }

    const frame = document.createElement("iframe");
    frame.id = IFRAME_ID;
    frame.src = chrome.runtime.getURL("src/panel.html");
    frame.style.cssText =
      "border:none;width:100%;height:44px;margin-bottom:3px;display:block;border-radius:16px;overflow:hidden;background:transparent;transition:height .18s cubic-bezier(.4,0,.2,1);";
    frame.allow = "clipboard-read; clipboard-write;";
    frame.setAttribute("scrolling", "no");

    // 参考实现的做法：把 danmukuBox 上的 data-v-* 钩子抄给 iframe，保持 B 站的容器样式语义
    try {
      for (const key of Object.keys(box.dataset || {})) {
        if (key.startsWith("v-")) frame.dataset[key] = box.dataset[key];
      }
    } catch (_) {
      /* ignore */
    }

    box.insertBefore(frame, box.firstChild);
    iframe = frame;
    setIframeHeight();
    return true;
  }

  /** 每秒找一次 #danmukuBox；找到后再等 1.5s 才插（对齐参考实现，避免 B 站自己刷新页面） */
  function scheduleMount() {
    if (document.getElementById(IFRAME_ID)) {
      iframe = document.getElementById(IFRAME_ID);
      setIframeHeight();
      return;
    }
    const timer = setInterval(() => {
      if (document.getElementById("danmukuBox")) {
        clearInterval(timer);
        setTimeout(() => {
          if (createIframe()) pushState();
        }, 1500);
      }
    }, 1000);

    // 兜底：30s 还没找到 danmukuBox（新版布局可能没有）→ 用 fixed 定位插到 body（同样是隔离文档，不影响排版）
    setTimeout(() => {
      clearInterval(timer);
      if (document.getElementById(IFRAME_ID)) return;
      createFloatingIframe();
    }, 8000);
  }

  function createFloatingIframe() {
    if (document.getElementById(IFRAME_ID)) {
      iframe = document.getElementById(IFRAME_ID);
      return;
    }
    const frame = document.createElement("iframe");
    frame.id = IFRAME_ID;
    frame.src = chrome.runtime.getURL("src/panel.html");
    frame.style.cssText = [
      "position: fixed",
      "right: 16px",
      "top: 96px",
      "width: 380px",
      `height: ${state.prefs.collapsed ? COLLAPSED_HEIGHT : 520}px`,
      "border: none",
      "border-radius: 12px",
      "box-shadow: 0 12px 32px rgba(0,0,0,.18)",
      "z-index: 2147482000",
      "background: transparent",
      "transition: height .18s cubic-bezier(.4,0,.2,1)",
    ].join("; ");
    frame.allow = "clipboard-read; clipboard-write;";
    document.body.appendChild(frame);
    iframe = frame;
    state.floating = true;
    setIframeHeight();
  }

  function watchIframeAlive() {
    setInterval(() => {
      if (!document.getElementById(IFRAME_ID)) {
        iframe = null;
        iframeReady = false;
        scheduleMount();
      } else {
        setIframeHeight();
      }
    }, 2000);
  }

  // ---------- 状态推送给面板 ----------
  function pushState() {
    if (!iframeReady) return;
    postToPanel({
      __bs: "state",
      data: {
        video: state.video,
        segments: state.segments,
        source: state.langSource,
        label: labelOf(state.langSource),
        via: state.via,
        status: state.status,
        error: state.error,
        sync: state.sync,
        syncWarn: state.syncWarn,
        prefs: {
          tab: state.prefs.tab,
          merged: state.prefs.merged,
          collapsed: state.prefs.collapsed,
          theme: state.prefs.theme,
        },
      },
    });
  }

  function labelOf(src) {
    if (src === "human") return "官方 CC";
    if (src === "ai") return "官方 AI 字幕";
    if (src === "asr") return "ASR 转写";
    return "";
  }

  function setSync(text, warn) {
    state.sync = text;
    state.syncWarn = !!warn;
    postToPanel({ __bs: "sync", text, warn: !!warn });
  }

  function pushTask(payload) {
    postToPanel({ __bs: "task", data: payload });
  }

  function pushSummary(data, error) {
    postToPanel({ __bs: "summary", data, error: error || "" });
  }

  // ---------- 播放时间 ----------
  function timeLoop() {
    requestAnimationFrame(timeLoop);
    const v = videoEl();
    if (!v) return;
    const t = v.currentTime;
    if (Math.abs(t - lastTime) < 0.04) return;
    lastTime = t;
    postToPanel({ __bs: "time", t });
  }

  // ---------- 服务器 ----------
  async function importToServer() {
    if (!state.segments.length || !state.video) return;
    if (!state.prefs.serverUrl) return setSync("未配置服务器", true);
    if (!state.prefs.token) return setSync("未填令牌 · 点此设置", true);

    const key = `${state.video.bvid}_${state.video.p}_${state.track && state.track.lan}`;
    if (state.importedKey === key) return;
    state.importedKey = key;
    setSync("回传中…", false);
    try {
      await BS.api("POST", `/api/v2/subtitles/${state.video.bvid}/import`, {
        p: state.video.p,
        cid: state.video.cid,
        source: state.langSource === "human" ? "native-cc" : "native-ai",
        lang: (state.track && state.track.lan) || "",
        title: state.video.title,
        duration: state.video.duration,
        segments: state.segments.map((s) => ({ start: s.start, end: s.end, text: s.text })),
      });
      setSync("已回传服务器", false);
    } catch (e) {
      state.importedKey = "";
      let msg = String((e && e.message) || e);
      if (/未授权|401/i.test(msg)) {
        const len = (state.prefs.token || "").length;
        msg = len ? `未授权（令牌长度 ${len}）` : "未授权（本地没存到令牌）";
      }
      console.error("[BiliSub] 字幕回传失败：", msg, "| tokenLen=", (state.prefs.token || "").length);
      setSync(msg.slice(0, 34), true);
    }
  }

  // ---------- 任务 ----------
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "bs:progress") return;
    const handler = taskHandlers.get(msg.taskId);
    if (handler) handler(msg.payload);
  });

  function watchTask(taskId, { onStatus } = {}) {
    return new Promise((resolve, reject) => {
      let settled = false;
      let gotEvent = false;
      let polling = false;

      const stop = () => {
        taskHandlers.delete(taskId);
        chrome.runtime.sendMessage({ type: "bs:stream:stop", taskId }).catch(() => {});
      };
      const done = (fn, arg) => {
        if (settled) return;
        settled = true;
        stop();
        fn(arg);
      };
      const fallback = () => {
        if (settled || polling) return;
        polling = true;
        pollTask(taskId, onStatus).then((p) => done(resolve, p)).catch((e) => done(reject, e));
      };

      taskHandlers.set(taskId, (payload) => {
        if (!payload) return;
        gotEvent = true;
        if (payload.type === "state" || payload.type === "update") {
          if (onStatus) onStatus(payload);
          return;
        }
        if (payload.type === "final") {
          if (payload.status === "failed" || payload.error) done(reject, new Error(payload.error || "任务失败"));
          else done(resolve, payload);
          return;
        }
        if (payload.type === "error") done(reject, new Error(payload.error || "任务出错"));
      });

      chrome.runtime
        .sendMessage({ type: "bs:stream", taskId })
        .then((res) => {
          if (!res || !res.ok) fallback();
        })
        .catch(() => fallback());

      setTimeout(() => {
        if (!settled && !gotEvent) fallback();
      }, 30000);
    });
  }

  async function pollTask(taskId, onStatus) {
    const started = Date.now();
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      if (Date.now() - started > 45 * 60 * 1000) throw new Error("任务超时");
      let t;
      try {
        t = await BS.api("GET", `/api/v2/tasks/${taskId}`);
      } catch (e) {
        if (onStatus) onStatus({ step: `查询进度失败，重试中…（${e.message || e}）` });
        continue;
      }
      if (onStatus) onStatus(t);
      const status = t && (t.status || t.state);
      if (status === "failed" || status === "error") throw new Error(t.error || "服务器任务失败");
      if (status === "done" || status === "success" || status === "completed") return t;
    }
  }

  function normalizeSegments(raw) {
    return (raw || [])
      .map((s) => ({
        start: Number(s.start != null ? s.start : s.from) || 0,
        end: Number(s.end != null ? s.end : s.to) || 0,
        text: String(s.text != null ? s.text : s.content || "").trim(),
        music: false,
      }))
      .filter((s) => s.text && s.end > s.start);
  }

  async function runAsr() {
    if (state.asrRunning) return;
    if (!state.video) return;
    state.asrRunning = true;
    try {
      pushTask({ kind: "asr", status: "running", step: "准备音频直链" });
      const audio = await BS.getAudioUrl(state.video);
      const res = await BS.api("POST", `/api/v2/subtitles/${state.video.bvid}/asr`, {
        p: state.video.p,
        cid: state.video.cid,
        title: state.video.title,
        duration: state.video.duration,
        source_url: state.video.url,
        audio_url: audio || "",
        audio_only: true,
      });
      const taskId = res && (res.task_id || res.taskId);
      if (!taskId) throw new Error("服务器未返回 task_id");
      const payload = await watchTask(taskId, {
        onStatus: (p) => pushTask({ kind: "asr", status: "running", step: p.step || p.status || "" }),
      });
      const segs = normalizeSegments(payload.segments);
      if (!segs.length) throw new Error("服务器返回的字幕为空");
      state.segments = segs;
      state.track = { lan: "asr", lan_doc: "ASR" };
      state.langSource = "asr";
      state.error = "";
      state.status = `ASR 转写 · ${segs.length} 条`;
      pushTask({ kind: "asr", status: "done", step: state.status });
      pushState();
    } catch (e) {
      pushTask({ kind: "asr", status: "failed", error: String(e.message || e) });
    } finally {
      state.asrRunning = false;
    }
  }

  async function loadSummary() {
    if (!state.video) return;
    try {
      const data = await BS.api("GET", `/api/v2/summaries/${state.video.bvid}?p=${state.video.p}`);
      pushSummary(data, "");
    } catch (e) {
      const msg = String(e.message || e);
      pushSummary(null, /404/.test(msg) ? "" : `读取总结失败：${msg}`);
    }
  }

  async function genSummary(force) {
    if (!state.video) return;
    try {
      const res = await BS.api("POST", `/api/v2/summaries/${state.video.bvid}`, {
        p: state.video.p,
        type: "detailed",
        force: !!force,
      });
      if (res && res.cached) {
        await loadSummary();
        return;
      }
      const taskId = res && (res.task_id || res.taskId);
      if (!taskId) throw new Error("服务器未返回 task_id");
      await watchTask(taskId, {
        onStatus: (p) => pushTask({ kind: "summary", status: "running", step: p.step || p.status || "" }),
      });
      pushTask({ kind: "summary", status: "done", step: "完成" });
      await loadSummary();
    } catch (e) {
      pushTask({ kind: "summary", status: "failed", error: String(e.message || e) });
      pushSummary(null, `生成失败：${e.message || e}`);
    }
  }

  // ---------- 主流程 ----------
  async function load(force) {
    if (state.loading) return;
    state.loading = true;
    state.error = "";
    state.status = "读取中…";
    pushState();

    try {
      const video = await BS.resolveVideo();
      const same = state.video && state.video.bvid === video.bvid && state.video.p === video.p;
      state.video = video;
      if (!same || force) {
        state.segments = [];
        state.importedKey = "";
      }
      const r = await BS.fetchTranscript(video, state.track && state.track.lan);
      state.track = r.track || null;
      state.langSource = r.langSource || (r.source === "none" ? "none" : "other");
      state.via = r.via || "";
      state.segments = r.segments || [];
      state.status = state.segments.length ? `${state.segments.length} 条` : "未找到可用字幕";
      pushState();
      importToServer();
    } catch (e) {
      state.error = `读取失败：${e.message || e}`;
      state.status = "";
      console.error("[BiliSub] 读取失败：", e);
      pushState();
    } finally {
      state.loading = false;
    }
  }

  // ---------- 与 iframe 的通信 ----------
  window.addEventListener("message", (event) => {
    if (!iframe || event.source !== iframe.contentWindow) return;
    const data = event.data;
    if (!data || typeof data.__bs !== "string") return;

    if (data.__bs === "ready") {
      iframeReady = true;
      setIframeHeight();
      pushState();
      return;
    }
    if (data.__bs === "seek") {
      const v = videoEl();
      if (!v) return;
      v.currentTime = Math.max(0, Number(data.t) || 0);
      if (v.paused) v.play().catch(() => {});
      return;
    }
    if (data.__bs === "reload") {
      load(true);
      return;
    }
    if (data.__bs === "asr") {
      runAsr();
      return;
    }
    if (data.__bs === "summary") {
      if (data.force) genSummary(true);
      else loadSummary();
      return;
    }
    if (data.__bs === "collapse") {
      savePrefs({ collapsed: !!data.collapsed });
      setIframeHeight();
      return;
    }
    if (data.__bs === "options") {
      chrome.runtime.sendMessage({ type: "bs:open-options" }).catch(() => {});
      return;
    }
    if (data.__bs === "history") {
      const base = String(state.prefs.serverUrl || "").replace(/\/+$/, "");
      if (!base) {
        chrome.runtime.sendMessage({ type: "bs:open-options" }).catch(() => {});
      } else {
        chrome.runtime.sendMessage({ type: "bs:open-url", url: `${base}/history` }).catch(() => {});
      }
    }
  });

  // ---------- SPA 监听 ----------
  function watchSpa() {
    setInterval(() => {
      if (location.href === state.href) return;
      state.href = location.href;
      if (!/\/video\/BV/.test(location.pathname)) return;
      setTimeout(() => load(true), 700);
    }, 1000);
  }

  // ---------- 启动 ----------
  (async function start() {
    await loadPrefs();
    scheduleMount();
    watchIframeAlive();
    watchSpa();
    timeLoop();
    await load(false);
    chrome.storage.onChanged.addListener((changes) => {
      if (changes["bs:prefs"]) {
        state.prefs = { ...DEFAULT_PREFS, ...(changes["bs:prefs"].newValue || {}) };
        setIframeHeight();
        pushState();
      }
    });
  })();
})();
