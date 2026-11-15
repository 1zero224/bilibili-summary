"""服务器端「历史 / 详情」页面：`GET /history`、`GET /history/{bvid}`。

- 只展示**跑过 ASR 的视频**（`/api/v2/history?source=asr`），不把浏览过的视频都塞进来
- 多行多列封面网格；点进详情页可看字幕全文与详细总结
- 明暗两套 token；表单控件显式设置 color/background（否则深色下白底白字）
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["v2-page"])

BASE_CSS = """
:root {
  color-scheme: light;
  --bg:#f4f6f8; --card:#ffffff; --line:rgba(16,24,40,.10);
  --text:#14161a; --text-2:#5c6472; --text-3:#98a0ac;
  --accent:#00aeec; --accent-ink:#fff; --accent-soft:rgba(0,174,236,.10);
  --warn:#e8890c; --ok:#12b886; --danger:#e5484d;
  --shadow:0 1px 2px rgba(16,24,40,.05), 0 10px 28px rgba(16,24,40,.08);
  --chevron:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.75 6 7.75l3-3' fill='none' stroke='%2398a0ac' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg:#111418; --card:#191c21; --line:rgba(255,255,255,.10);
    --text:#e8eaee; --text-2:#a2a9b4; --text-3:#6f7683;
    --accent:#3bc1ef; --accent-ink:#08181f; --accent-soft:rgba(59,193,239,.15);
    --warn:#f5a524; --ok:#3ddc97; --danger:#ff6b6b;
    --shadow:0 1px 2px rgba(0,0,0,.45), 0 12px 30px rgba(0,0,0,.5);
    --chevron:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M3 4.75 6 7.75l3-3' fill='none' stroke='%236f7683' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
}
* { box-sizing: border-box; }
body {
  margin:0; padding:24px 22px 56px; background:var(--bg); color:var(--text);
  font:13.5px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","HarmonyOS Sans SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
a { color:inherit; text-decoration:none; }
header.bar {
  position:sticky; top:0; z-index:6; margin:-24px -22px 20px; padding:14px 22px;
  display:flex; gap:12px; align-items:center; flex-wrap:wrap;
  background:color-mix(in srgb, var(--bg) 86%, transparent);
  backdrop-filter:blur(10px); border-bottom:1px solid var(--line);
}
h1 { font-size:16px; margin:0; letter-spacing:.2px; }
.spacer { flex:1 1 auto; }
input[type=search], input[type=password] {
  font:inherit; font-size:13px; color:var(--text); background-color:var(--card);
  border:1px solid var(--line); border-radius:10px; padding:8px 12px; min-width:240px;
}
input[type=search]::placeholder, input[type=password]::placeholder { color:var(--text-3); }
input:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
button {
  font:inherit; font-size:13px; font-weight:500; color:var(--text); background-color:var(--card);
  border:1px solid var(--line); border-radius:10px; padding:8px 14px; cursor:pointer;
  transition:background-color .16s ease, border-color .16s ease;
}
button:hover:not(:disabled) { border-color:var(--accent); }
button.primary { color:var(--accent-ink); background-color:var(--accent); border-color:transparent; font-weight:600; }
button:disabled { opacity:.5; cursor:default; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(258px, 1fr)); gap:16px; }
.card {
  background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden;
  box-shadow:var(--shadow); transition:transform .16s ease, border-color .16s ease;
  display:flex; flex-direction:column;
}
.card:hover { transform:translateY(-2px); border-color:var(--accent); }
.thumb { position:relative; aspect-ratio:16/9; background:linear-gradient(135deg, var(--accent-soft), transparent); }
.thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.thumb .dur {
  position:absolute; right:8px; bottom:8px; padding:1px 6px; border-radius:6px;
  background:rgba(0,0,0,.66); color:#fff; font-size:11px; font-variant-numeric:tabular-nums;
}
.card .body { padding:10px 12px 12px; display:flex; flex-direction:column; gap:6px; flex:1 1 auto; }
.card .title { font-weight:600; font-size:13.5px; line-height:1.5; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.meta { font-size:11.5px; color:var(--text-3); display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.tag { font-size:11px; font-weight:600; padding:1px 7px; border-radius:999px; background:var(--accent-soft); color:var(--accent); }
.tag.warn { background:rgba(245,165,36,.14); color:var(--warn); }
.tag.ok { background:rgba(18,184,134,.14); color:var(--ok); }
.empty { text-align:center; color:var(--text-3); padding:64px 0; }
.err { color:var(--danger); font-size:12px; }
.auth { max-width:420px; margin:12vh auto; background:var(--card); padding:22px; border-radius:14px; border:1px solid var(--line); }
.hint { font-size:12px; color:var(--text-3); margin-top:8px; }
code { background:var(--accent-soft); color:var(--accent); padding:1px 5px; border-radius:5px; font-size:11.5px; }
/* 详情页 */
.detail { max-width:920px; margin:0 auto; }
.hero { display:flex; gap:16px; margin-bottom:18px; flex-wrap:wrap; }
.hero .cover { width:280px; aspect-ratio:16/9; border-radius:12px; overflow:hidden; background:var(--accent-soft); flex:0 0 auto; }
.hero .cover img { width:100%; height:100%; object-fit:cover; display:block; }
.hero .info { flex:1 1 320px; min-width:0; }
.hero h2 { font-size:17px; margin:0 0 6px; line-height:1.45; }
.tabs { display:inline-flex; gap:2px; padding:2px; background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:14px; }
.tab { border:0; background:transparent; color:var(--text-2); font-size:13px; padding:6px 13px; border-radius:8px; cursor:pointer; }
.tab[aria-selected="true"] { background:var(--accent-soft); color:var(--accent); font-weight:600; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:6px 0; }
.row { display:flex; gap:12px; padding:6px 14px; border:0; background:none; width:100%; text-align:left; color:inherit; cursor:pointer; }
.row:hover { background:var(--accent-soft); }
.row .t { min-width:52px; color:var(--text-3); font-size:12px; font-variant-numeric:tabular-nums; }
.md { padding:14px 18px; }
.md h2 { font-size:15px; margin:16px 0 6px; }
.md h3 { font-size:14px; margin:14px 0 4px; }
.md ul { margin:6px 0 10px; padding-left:20px; }
.md blockquote { margin:10px 0; padding:6px 12px; border-left:3px solid var(--accent); background:var(--accent-soft); border-radius:0 8px 8px 0; color:var(--text-2); }
.chip { border:0; border-radius:999px; background:var(--accent-soft); color:var(--accent); font-size:11.5px; font-weight:600; padding:1px 8px; cursor:pointer; }
"""

BASE_JS = """
const $ = (id) => document.getElementById(id);
const KEY = 'bs_token';
let token = localStorage.getItem(KEY) || '';
function showAuth(msg) {
  const a = $('auth'); if (!a) return;
  a.hidden = false;
  document.body.dataset.needAuth = '1';
  if (msg) $('authErr').textContent = msg;
}
function hideAuth() { const a = $('auth'); if (a) a.hidden = true; }
async function api(path) {
  const res = await fetch(path, { headers: token ? { 'X-BS-Token': token } : {} });
  if (res.status === 401) { showAuth('令牌无效或为空'); throw new Error('401'); }
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
function fmtDur(sec) {
  // 注意：后端给的是浮点秒（5.68 / 60.02），必须先取整，否则会拼出 "00:5.68"、"01:0.020000000000003"
  const total = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const p = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(Number(ts) * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function initToken() {
  const urlToken = new URLSearchParams(location.search).get('token');
  if (urlToken) { token = urlToken; localStorage.setItem(KEY, token); }
  const save = $('saveToken');
  if (save) save.addEventListener('click', () => {
    token = $('token').value.trim();
    localStorage.setItem(KEY, token);
    hideAuth();
    if (window.__reload) window.__reload();
  });
}
// BASE_JS 在 <head> 里执行，此时 body 还没解析，必须等 DOM 就绪再绑事件
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initToken);
else initToken();
"""

AUTH_BLOCK = """
<div class="auth" id="auth" hidden>
  <div style="font-weight:600;margin-bottom:8px;">需要访问令牌</div>
  <input id="token" type="password" placeholder="DATA_DIR/.api_token" style="width:100%" />
  <div class="hint">令牌只存在这台浏览器的 localStorage，用于访问本服务器。</div>
  <div class="err" id="authErr"></div>
  <div style="margin-top:12px;"><button id="saveToken" class="primary">保存并加载</button></div>
</div>
"""

HISTORY_BODY = """
<header class="bar">
  <h1>历史</h1>
  <input id="q" type="search" placeholder="搜索标题 / BV 号 / UP 主" />
  <span class="spacer"></span>
  <span class="meta" id="count"></span>
  <button id="reload">刷新</button>
</header>
<main id="main"><div class="empty">加载中…</div></main>
""" + AUTH_BLOCK + """
<script>
window.__reload = load;
async function load() {
  try {
    const q = $('q').value.trim();
    const data = await api('/api/v2/history?limit=200&source=processed' + (q ? '&q=' + encodeURIComponent(q) : ''));
    hideAuth();
    render(data.items || []);
  } catch (e) {
    if (String(e.message) === '401') return;
    $('main').innerHTML = '<div class="empty err">加载失败：' + e.message + '</div>';
  }
}
function render(items) {
  const main = $('main');
  $('count').textContent = items.length ? items.length + ' 条' : '';
  if (!items.length) {
    main.innerHTML = '<div class="empty">还没有记录。在 B 站视频页点面板上的「获取字幕」即可生成。</div>';
    return;
  }
  main.className = 'grid';
  main.innerHTML = items.map((it) => {
    const href = '/history/' + encodeURIComponent(it.bvid) + (Number(it.page) > 1 ? '?p=' + it.page : '');
    const cover = it.cover ? '<img src="' + it.cover + '" loading="lazy" referrerpolicy="no-referrer" alt="">' : '';
    const dur = it.duration ? '<span class="dur">' + fmtDur(it.duration) + '</span>' : '';
    const tags = [];
    if (it.subtitle_source === 'asr') tags.push('<span class="tag">ASR</span>');
    if (it.has_summary) tags.push('<span class="tag ok">总结</span>');
    return '<a class="card" href="' + href + '">' +
      '<div class="thumb">' + cover + dur + '</div>' +
      '<div class="body"><div class="title">' + (it.title || it.bvid) + '</div>' +
      '<div class="meta">' + tags.join('') + '<span>BV ' + it.bvid + '</span>' +
      (it.account || '') + '<span>' + fmtTime(it.updated_at) + '</span></div></div></a>';
  }).join('');
}
let timer = 0;
$('q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
$('reload').addEventListener('click', load);
load();
</script>
"""

DETAIL_BODY = """
<header class="bar">
  <a href="/history" style="font-size:13px;color:var(--text-2)">← 返回历史</a>
  <span class="spacer"></span>
  <button id="openBili">在 B 站打开</button>
</header>
<main class="detail" id="main"></main>
""" + AUTH_BLOCK + """
<script>
const BVID = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop());
const RAW_P = new URLSearchParams(location.search).get('p');
let P = Number(RAW_P) || 0;          // 0 = 未指定，交给后端取第一条记录
let SEGMENTS = [];
window.__reload = load;
function seekLink(sec) {
  return 'https://www.bilibili.com/video/' + BVID + '?p=' + (P || 1) + '&t=' + Math.floor(sec);
}
async function load() {
  try {
    let sub = null, sum = null;
    try { sub = await api('/api/v2/subtitles/' + BVID + (P ? '?p=' + P : '')); } catch (e) { if (String(e.message) === '401') return; }
    if (sub && !P) P = Number(sub.page) || 1;
    try { sum = await api('/api/v2/summaries/' + BVID + '?p=' + P); } catch (e) { if (String(e.message) === '401') return; }
    hideAuth();
    render(sub, sum);
  } catch (e) {
    $('main').innerHTML = '<div class="empty err">加载失败：' + e.message + '</div>';
  }
}
function render(sub, sum) {
  SEGMENTS = (sub && sub.segments) || [];
  const title = (sub && sub.title) || (sum && sum.title) || BVID;
  const cover = (sub && sub.cover) || (sum && sum.cover) || '';
  const author = (sub && sub.author) || (sum && sum.author) || '';
  const dur = (sub && sub.duration) || (sum && sum.duration) || 0;
  const tags = [];
  if (sub) tags.push('<span class="tag">ASR ' + (sub.count || SEGMENTS.length) + ' 条</span>');
  if (sum) tags.push('<span class="tag ok">总结</span>');

  $('main').innerHTML =
    '<div class="hero"><div class="cover">' + (cover ? '<img src="' + cover + '" referrerpolicy="no-referrer">' : '') + '</div>' +
    '<div class="info"><h2>' + title + '</h2>' +
    '<div class="meta">' + tags.join('') + '<span>BV ' + BVID + (P > 1 ? ' · P' + P : '') + '</span>' +
    (dur ? '<span>' + fmtDur(dur) + '</span>' : '') + (author ? '<span>' + author + '</span>' : '') + '</div></div></div>' +
    (SEGMENTS.length
      ? '<div class="tabs"><button class="tab" id="tabSub" aria-selected="true">字幕</button>' +
        '<button class="tab" id="tabSum" aria-selected="false">总结</button></div>'
      : '') +
    '<div class="panel" id="paneSub" style="display:' + (SEGMENTS.length ? '' : 'none') + '"></div>' +
    '<div class="panel md" id="paneSum" style="display:none"></div>';

  const paneSub = $('paneSub'), paneSum = $('paneSum');
  if (!SEGMENTS.length) paneSub.innerHTML = '<div class="empty">没有字幕记录</div>';
  else {
    paneSub.innerHTML = SEGMENTS.map((s) =>
      '<button class="row" data-t="' + s.start + '"><span class="t">' + fmtDur(s.start) + '</span><span>' + s.text + '</span></button>'
    ).join('');
    paneSub.querySelectorAll('.row').forEach((n) => n.addEventListener('click', () => {
      window.open(seekLink(Number(n.dataset.t)), '_blank');
    }));
  }
  const detailed = sum && (sum.detailed || sum.summary);
  paneSum.innerHTML = detailed ? markdown(detailed) : '<div class="empty">没有总结。可在 B 站视频页的面板里生成。</div>';
  paneSum.querySelectorAll('.chip').forEach((n) => n.addEventListener('click', () => {
    window.open(seekLink(Number(n.dataset.t)), '_blank');
  }));

  function tabTo(which) {
    const isSub = which === 'sub';
    $('tabSub').setAttribute('aria-selected', isSub ? 'true' : 'false');
    $('tabSum').setAttribute('aria-selected', isSub ? 'false' : 'true');
    paneSub.style.display = isSub ? '' : 'none';
    paneSum.style.display = isSub ? 'none' : '';
  }
  if (SEGMENTS.length && $('tabSub')) {
    $('tabSub').addEventListener('click', () => tabTo('sub'));
    $('tabSum').addEventListener('click', () => tabTo('sum'));
  }
  // 没有字幕：不显示 tab 栏，直接看总结
  if (!SEGMENTS.length && detailed) { paneSub.style.display = 'none'; paneSum.style.display = ''; }
}

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function clock(v) {
  const t = String(v || '').trim();
  if (/^\\d+$/.test(t)) return Number(t);
  const p = t.split(':').map(Number);
  if (p.some((n) => !Number.isFinite(n))) return null;
  return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p.length === 2 ? p[0] * 60 + p[1] : null;
}
function markdown(md) {
  const inline = (raw) => {
    let s = esc(raw);
    s = s.replace(/\\[\\[\\s*时间段\\s*[:：]\\s*([0-9:]+)\\s*[-–~]\\s*([0-9:]+)\\s*\\]\\]/g, (m, a, b) => {
      const sec = clock(a);
      return sec == null ? '<span>' + a + '–' + b + '</span>'
        : '<button class="chip" data-t="' + sec + '">' + a + '–' + b + '</button>';
    });
    return s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  };
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
  for (const line of String(md || '').split(/\\r?\\n/)) {
    const text = line.trim();
    if (!text) { closeList(); continue; }
    const h = /^(#{1,4})\\s+(.*)$/.exec(text);
    if (h) { closeList(); const lv = Math.min(4, h[1].length + 1); out.push('<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>'); continue; }
    if (/^[-*]\\s+/.test(text)) { if (!inList) { out.push('<ul>'); inList = true; } out.push('<li>' + inline(text.replace(/^[-*]\\s+/, '')) + '</li>'); continue; }
    if (/^>\\s?/.test(text)) { closeList(); out.push('<blockquote>' + inline(text.replace(/^>\\s?/, '')) + '</blockquote>'); continue; }
    closeList();
    out.push('<p>' + inline(text) + '</p>');
  }
  closeList();
  return out.join('');
}

$('openBili').addEventListener('click', () => {
  window.open('https://www.bilibili.com/video/' + BVID + (P > 1 ? '?p=' + P : ''), '_blank');
});
load();
</script>
"""


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>{BASE_CSS}</style>
<script>{BASE_JS}</script>
</head>
<body>
{body}
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/history", response_class=HTMLResponse)
async def history_page():
    return _page("BiliSub · ASR 历史", HISTORY_BODY)


@router.get("/history/{bvid}", response_class=HTMLResponse)
async def history_detail_page(bvid: str):
    return _page(f"BiliSub · {bvid}", DETAIL_BODY)
