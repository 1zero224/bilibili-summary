/** 设置页：读写 chrome.storage.local 的 bs:prefs，并提供连接自检。 */

const DEFAULTS = {
  mode: "embed",
  serverUrl: "",
  token: "",
  theme: "auto",
  width: 380,
  merged: true,
  follow: true,
  collapsed: true,
  top: 88,
  right: 16,
};

const $ = (id) => document.getElementById(id);
const msg = (text, isErr) => {
  const n = $("msg");
  n.textContent = text;
  n.className = isErr ? "err" : "";
  clearTimeout(msg._t);
  msg._t = setTimeout(() => {
    n.textContent = "";
  }, 4000);
};

async function load() {
  const got = await chrome.storage.local.get("bs:prefs");
  const p = { ...DEFAULTS, ...((got && got["bs:prefs"]) || {}) };
  $("serverUrl").value = p.serverUrl || "";
  $("token").value = p.token || "";
  $("mode").value = p.mode === "float" ? "float" : "embed";
  $("theme").value = p.theme || "auto";
  $("width").value = p.width || 380;
  $("merged").value = p.merged ? "1" : "0";
  $("follow").value = p.follow ? "1" : "0";
}

async function save(opts = {}) {
  const got = await chrome.storage.local.get("bs:prefs");
  const prev = { ...DEFAULTS, ...((got && got["bs:prefs"]) || {}) };
  const next = {
    ...prev,
    serverUrl: $("serverUrl").value.trim().replace(/\/+$/, ""),
    token: $("token").value.trim(),
    mode: $("mode").value === "float" ? "float" : "embed",
    theme: $("theme").value,
    width: Math.min(720, Math.max(280, Number($("width").value) || 380)),
    merged: $("merged").value === "1",
    follow: $("follow").value === "1",
  };
  await chrome.storage.local.set({ "bs:prefs": next });
  if (!opts || !opts.quiet) msg("已保存");
  return next;
}

async function test() {
  // 先落盘再测：否则只点「测试连接」不会保存，面板那边仍然没有令牌
  await save({ quiet: true });
  const base = $("serverUrl").value.trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  if (!base) return msg("先填服务器地址", true);
  msg("测试中…");
  try {
    const res = await fetch(`${base}/api/v2/health`, {
      headers: token ? { "X-BS-Token": token } : {},
    });
    const text = await res.text();
    if (!res.ok) return msg(`失败：HTTP ${res.status} ${text.slice(0, 80)}`, true);
    let data = null;
    try {
      data = JSON.parse(text);
    } catch (_) {
      /* ignore */
    }
    msg(`连接成功${data && data.version ? ` · ${data.version}` : ""}`);
  } catch (e) {
    msg(`失败：${(e && e.message) || e}（检查域名/证书/CORS）`, true);
  }
}

for (const id of ["serverUrl", "token", "mode", "theme", "width", "merged", "follow"]) {
  const node = $(id);
  if (!node) continue;
  node.addEventListener("change", () => save({ quiet: true }));
  node.addEventListener("blur", () => save({ quiet: true }));
}
$("save").addEventListener("click", () => save());
$("test").addEventListener("click", test);
load();
