/**
 * Service Worker：只做两件事
 *  1) 代理对自建服务器的 API 请求（content script 直接跨源 fetch 会受 B 站页面 Origin 的 CORS 限制，
 *     而扩展在 host_permissions 覆盖下不受限）
 *  2) 点击工具栏图标 → 打开服务器的历史主面板
 *
 * 注意：不在这里请求任何 B 站接口（实测页面上下文更稳，见 net.js 注释）。
 */

const DEFAULT_PREFS = {
  serverUrl: "",
  token: "",
  theme: "auto",
  width: 380,
  merged: true,
  follow: true,
  collapsed: false,
  top: 88,
  right: 16,
};

async function getPrefs() {
  const got = await chrome.storage.local.get("bs:prefs");
  return { ...DEFAULT_PREFS, ...((got && got["bs:prefs"]) || {}) };
}

async function handleApi({ method = "GET", path, body }) {
  const prefs = await getPrefs();
  const base = String(prefs.serverUrl || "").replace(/\/+$/, "");
  if (!base) return { ok: false, status: 0, error: "未配置服务器地址（扩展选项）" };
  if (!path || !path.startsWith("/")) return { ok: false, status: 0, error: "非法路径" };

  const headers = {};
  if (prefs.token) headers["X-BS-Token"] = String(prefs.token);
  const init = { method, headers };
  if (body !== undefined && body !== null) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  try {
    const res = await fetch(`${base}${path}`, init);
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_) {
        data = text;
      }
    }
    if (!res.ok) {
      const detail = (data && (data.detail || data.error)) || "";
      let error = detail || `HTTP ${res.status}`;
      if (res.status === 401) error = `未授权（${detail || "令牌为空或不正确"}）`;
      else if (res.status === 0) error = "无法连接服务器";
      return { ok: false, status: res.status, error, data };
    }
    return { ok: true, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, error: `无法连接服务器：${(e && e.message) || e}` };
  }
}

/**
 * SSE 中继：服务端用 fetch + ReadableStream 读（EventSource 不能带 X-BS-Token），
 * 逐条 push 回发起方所在标签页的 content script。
 */
const streams = new Map(); // taskId -> AbortController

async function handleStream(msg, sender) {
  const prefs = await getPrefs();
  const base = String(prefs.serverUrl || "").replace(/\/+$/, "");
  const tabId = sender && sender.tab && sender.tab.id;
  if (!base) return { ok: false, error: "未配置服务器地址" };
  if (!tabId) return { ok: false, error: "无法确定标签页" };

  const { taskId } = msg;
  if (!taskId) return { ok: false, error: "缺少 taskId" };
  if (streams.has(taskId)) return { ok: true, already: true };

  const controller = new AbortController();
  streams.set(taskId, controller);

  (async () => {
    const headers = { Accept: "text/event-stream" };
    if (prefs.token) headers["X-BS-Token"] = String(prefs.token);
    try {
      const res = await fetch(`${base}/api/v2/tasks/${encodeURIComponent(taskId)}/stream`, {
        headers,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const dataLines = raw
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim());
          if (!dataLines.length) continue;
          let payload = null;
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch (_) {
            continue;
          }
          chrome.tabs
            .sendMessage(tabId, { type: "bs:progress", taskId, payload })
            .catch(() => {});
          if (payload.type === "final" || payload.type === "error") {
            controller.abort();
          }
        }
      }
    } catch (e) {
      if (String((e && e.name) || "") !== "AbortError") {
        chrome.tabs
          .sendMessage(tabId, {
            type: "bs:progress",
            taskId,
            payload: { type: "error", error: String((e && e.message) || e) },
          })
          .catch(() => {});
      }
    } finally {
      streams.delete(taskId);
    }
  })();

  return { ok: true };
}

/** content script 取不到时（CORS / Referer 校验）由 SW 代取 JSON。 */
async function handleFetchJson(msg) {
  const url = String(msg.url || "");
  if (!/^https?:\/\//.test(url)) return { ok: false, error: "非法 url" };
  try {
    const res = await fetch(url, {
      credentials: msg.creds === "include" ? "include" : "omit",
      cache: "no-store",
      headers: { Accept: "application/json, text/plain, */*" },
    });
    if (!res.ok) return { ok: false, status: res.status, error: `HTTP ${res.status}` };
    const text = await res.text();
    try {
      return { ok: true, data: JSON.parse(text) };
    } catch (_) {
      return { ok: false, error: "响应不是 JSON" };
    }
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "bs:api") {
    if (msg && msg.type === "bs:fetch-json") {
      handleFetchJson(msg).then(sendResponse).catch((e) =>
        sendResponse({ ok: false, error: String((e && e.message) || e) }),
      );
      return true;
    }
    if (msg && msg.type === "bs:open-url") {
      const url = String(msg.url || "");
      if (/^https?:\/\//i.test(url)) chrome.tabs.create({ url }).catch(() => {});
      sendResponse({ ok: true });
      return false;
    }
    if (msg && msg.type === "bs:open-options") {
      chrome.runtime.openOptionsPage();
      sendResponse({ ok: true });
      return false;
    }
    if (msg && msg.type === "bs:stream") {
      handleStream(msg, sender).then(sendResponse).catch((e) =>
        sendResponse({ ok: false, error: String((e && e.message) || e) }),
      );
      return true;
    }
    if (msg && msg.type === "bs:stream:stop" && msg.taskId) {
      const controller = streams.get(msg.taskId);
      if (controller) controller.abort();
      streams.delete(msg.taskId);
      sendResponse({ ok: true });
      return false;
    }
    return false;
  }
  handleApi(msg).then(sendResponse).catch((e) =>
    sendResponse({ ok: false, status: 0, error: String((e && e.message) || e) }),
  );
  return true; // 异步响应
});

chrome.action.onClicked.addListener(async () => {
  const prefs = await getPrefs();
  const base = String(prefs.serverUrl || "").replace(/\/+$/, "");
  if (!base) {
    chrome.runtime.openOptionsPage();
    return;
  }
  chrome.tabs.create({ url: `${base}/history` }).catch(() => chrome.runtime.openOptionsPage());
});

chrome.runtime.onInstalled.addListener(async () => {
  const got = await chrome.storage.local.get("bs:prefs");
  if (!got || !got["bs:prefs"]) {
    await chrome.storage.local.set({ "bs:prefs": DEFAULT_PREFS });
  }
});
