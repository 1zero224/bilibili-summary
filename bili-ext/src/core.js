/**
 * 纯函数层：轨道选择 / 字幕解析 / 句合并 / 时间格式化 / 导出。
 * 不依赖 DOM 与 chrome API，便于单测与移植。
 *
 * 字幕 JSON 实测形状（页面上下文抓取，2026 实测）：
 *   { body: [ { from: 0.12, to: 0.6, sid: 1, location: 2, content: "同志们", music: 0 }, ... ] }
 *   - from/to 单位秒（float）
 *   - location：2 = 位于画面底部（用于排版提示）
 *   - music：1 = 音乐/无词段，可弱化
 *   - B 站 AI 字幕断句是"短语级"，必须合并成句才可读
 */
globalThis.BS = globalThis.BS || {};

/** 人工中文字幕优先，其次 AI 中文，最后任意有 url 的轨道。 */
BS.pickTrack = function pickTrack(tracks) {
  const list = (tracks || []).filter((t) => t && t.subtitle_url);
  if (!list.length) return null;
  const human = ["zh-CN", "zh-Hans", "zh-Hant", "zh"];
  for (const lan of human) {
    const hit = list.find((t) => String(t.lan || "").toLowerCase() === lan.toLowerCase());
    if (hit) return hit;
  }
  const ai = list.find((t) => /^ai-/i.test(String(t.lan || "")));
  return ai || list[0];
};

/** 来源标签：human | ai | other */
BS.trackSource = function trackSource(track) {
  const lan = String(track && track.lan || "").toLowerCase();
  if (/^ai-/.test(lan)) return "ai";
  if (lan === "zh-cn" || lan === "zh-hans" || lan === "zh-hant" || lan === "zh") return "human";
  return "other";
};

/**
 * 服务端/任务返回的 segments → 标准形状。
 * 兼容 `{start,end,text}` 与 `{from,to,content}` 两种写法，过滤空文本与非法时间。
 */
BS.normalizeSegments = function normalizeSegments(raw) {
  return (raw || [])
    .map((s) => ({
      start: Number(s && (s.start != null ? s.start : s.from)) || 0,
      end: Number(s && (s.end != null ? s.end : s.to)) || 0,
      text: String((s && (s.text != null ? s.text : s.content)) || "").trim(),
      music: false,
    }))
    .filter((s) => s.text && s.end > s.start);
};

/** 原始 body[] → 归一化 segments（过滤空文本与非法时间）。 */
BS.parseSubtitleJson = function parseSubtitleJson(json) {
  const body = json && Array.isArray(json.body) ? json.body : [];
  const out = [];
  for (const item of body) {
    const start = Number(item && item.from);
    const end = Number(item && item.to);
    const text = String((item && item.content) || "").trim();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;
    if (!text) continue;
    out.push({
      start,
      end,
      text,
      loc: Number(item.location) || 0,
      music: Number(item.music) === 1,
    });
  }
  return out;
};

const SENTENCE_END = /[。！？!?.…]$/;

/**
 * 把"短语级"字幕合并成可读行。
 * 判据（任一命中即断行）：句末标点 / 与下一条间隔 > gap / 累计长度 > maxLen / music 标记变化。
 * @returns [{start, end, text, from, to, music, raw: [...]}]
 */
BS.mergeSegments = function mergeSegments(segments, opts = {}) {
  const gap = opts.gap == null ? 0.8 : opts.gap;
  const maxLen = opts.maxLen == null ? 42 : opts.maxLen;
  const rows = [];
  let cur = null;

  const flush = () => {
    if (!cur) return;
    cur.text = cur.raw.map((s) => s.text).join("");
    rows.push(cur);
    cur = null;
  };

  for (let i = 0; i < segments.length; i += 1) {
    const s = segments[i];
    if (cur) {
      const tooFar = s.start - cur.end > gap;
      const tooLong = cur.raw.reduce((n, r) => n + r.text.length, 0) >= maxLen;
      const musicChanged = !!s.music !== !!cur.music;
      const ended = SENTENCE_END.test(cur.raw[cur.raw.length - 1].text);
      if (tooFar || tooLong || musicChanged || ended) flush();
    }
    if (!cur) cur = { start: s.start, end: s.end, music: !!s.music, raw: [] };
    cur.raw.push(s);
    cur.end = s.end;
  }
  flush();

  return rows.map((r) => ({
    start: r.start,
    end: r.end,
    text: r.text,
    music: r.music,
    raw: r.raw,
  }));
};

/** 当前播放时间 → 命中的行下标（二分，返回 -1 表示还没到第一行）。 */
BS.indexOfTime = function indexOfTime(rows, t) {
  let lo = 0;
  let hi = rows.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].start <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
};

BS.fmtTime = function fmtTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const p2 = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p2(m)}:${p2(ss)}` : `${p2(m)}:${p2(ss)}`;
};

const p2 = (n) => String(n).padStart(2, "0");
const p3 = (n) => String(n).padStart(3, "0");

/** SRT 时间：00:00:01,234 */
BS.fmtSrtTime = function fmtSrtTime(sec) {
  const v = Math.max(0, Number(sec) || 0);
  const ms = Math.round((v % 1) * 1000);
  const total = Math.floor(v);
  return `${p2(Math.floor(total / 3600))}:${p2(Math.floor((total % 3600) / 60))}:${p2(total % 60)},${p3(ms)}`;
};

/** VTT 时间：00:00:01.234 */
BS.fmtVttTime = function fmtVttTime(sec) {
  return BS.fmtSrtTime(sec).replace(",", ".");
};

/** ASS 时间：H:MM:SS.cc */
BS.fmtAssTime = function fmtAssTime(sec) {
  const v = Math.max(0, Number(sec) || 0);
  const cs = Math.round((v % 1) * 100);
  const total = Math.floor(v);
  return `${Math.floor(total / 3600)}:${p2(Math.floor((total % 3600) / 60))}:${p2(total % 60)}.${p2(cs)}`;
};

const ASS_HEAD = `[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,36,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

/**
 * 生成导出文本。
 * @param {'txt'|'txt-time'|'srt'|'vtt'|'ass'|'json'} kind
 * @param {{title:string, url:string, author:string, rows:Array, segments:Array}} payload
 */
BS.buildExport = function buildExport(kind, payload) {
  const { title = "", url = "", author = "", rows = [], segments = [] } = payload || {};
  const head = `${title}\n${url}${author ? `\n${author}` : ""}\n\n`;

  if (kind === "json") {
    return JSON.stringify({ title, url, author, segments }, null, 2);
  }
  if (kind === "txt") {
    return head + rows.map((r) => r.text).join("\n");
  }
  if (kind === "txt-time") {
    return head + rows.map((r) => `[${BS.fmtTime(r.start)}] ${r.text}`).join("\n");
  }
  if (kind === "srt") {
    return rows
      .map((r, i) => `${i + 1}\n${BS.fmtSrtTime(r.start)} --> ${BS.fmtSrtTime(r.end)}\n${r.text}\n`)
      .join("\n");
  }
  if (kind === "vtt") {
    return (
      "WEBVTT\n\n" +
      rows
        .map((r, i) => `${i + 1}\n${BS.fmtVttTime(r.start)} --> ${BS.fmtVttTime(r.end)}\n${r.text}\n`)
        .join("\n")
    );
  }
  if (kind === "ass") {
    return (
      ASS_HEAD +
      rows
        .map(
          (r) =>
            `Dialogue: 0,${BS.fmtAssTime(r.start)},${BS.fmtAssTime(r.end)},Default,,0,0,0,,${r.text.replace(/\r?\n/g, "\\N")}`,
        )
        .join("\n")
    );
  }
  return head + rows.map((r) => r.text).join("\n");
};

/** 触发浏览器下载。 */
BS.download = function download(filename, text, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 1000);
};

/** 文件名安全化。 */
BS.safeName = function safeName(s) {
  return String(s || "subtitle").replace(/[\\/:*?"<>|\n\r\t]/g, "_").slice(0, 80);
};

const HTML_ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
BS.escapeHtml = function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => HTML_ESC[c]);
};

/** "1:02:05" / "02:05" / "125" → 秒；无法解析返回 null。 */
BS.parseClock = function parseClock(value) {
  const text = String(value == null ? "" : value).trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(":").map((n) => Number(n));
  if (!parts.length || parts.some((n) => !Number.isFinite(n))) return null;
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return null;
};

/**
 * 极简 Markdown → HTML（够面板用，无第三方依赖）：
 * 标题 / 列表 / 引用 / 分隔线 / 粗体 / 行内代码，外加把
 * `[[时间段: 1:02-3:40]]` 转成可点击的时间 chip（`data-t` = 起始秒）。
 */
BS.renderMarkdown = function renderMarkdown(md) {
  const inline = (raw) => {
    let s = BS.escapeHtml(raw);
    s = s.replace(
      /\[\[\s*时间段\s*[:：]\s*([0-9:]+)\s*[-–~]\s*([0-9:]+)\s*\]\]/g,
      (_m, a, b) => {
        const sec = BS.parseClock(a);
        const label = `${a}–${b}`;
        if (sec == null) return `<span class="ts">${label}</span>`;
        return `<button class="chip" data-t="${sec}">${label}</button>`;
      },
    );
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    return s;
  };

  const lines = String(md || "").split(/\r?\n/);
  const out = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  for (const line of lines) {
    const text = line.trim();
    if (!text) {
      closeList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(text);
    if (heading) {
      closeList();
      const level = Math.min(4, heading[1].length + 1); // # → h2（面板里 h1 给标题用）
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(text)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(text.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^>\s?/.test(text)) {
      closeList();
      out.push(`<blockquote>${inline(text.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(text)) {
      closeList();
      out.push("<hr>");
      continue;
    }
    closeList();
    out.push(`<p>${inline(text)}</p>`);
  }
  closeList();
  return out.join("\n");
};
