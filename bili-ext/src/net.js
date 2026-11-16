/**
 * 网络层：
 *  - resolveVideo(): 从 URL + /x/web-interface/view 解析 bvid / p / aid / cid / 标题 / 作者 / 时长
 *  - fetchTranscript(): 三级取字幕（未签名 wbi/v2 → player/v2 → WBI 签名 wbi/v2）+ 本地缓存
 *  - getAudioUrl(): 取 DASH 音频直链（ASR 用）
 *  - api(): 经 background 代理访问自建服务器（避免 content script 的跨源 CORS）
 *
 * 为什么全部放在 content script（页面上下文）：
 *  实测页面上下文 + credentials:'include' 调 /x/player/wbi/v2?aid&cid 返回 code 0；
 *  而 Service Worker（chrome-extension:// 源）可能被风控。- 见报告 §9.4 / §9.7
 */
globalThis.BS = globalThis.BS || {};

const CACHE_TTL = 6 * 3600 * 1000;

function hostOf(url) {
  try {
    return new URL(url, location.href).host;
  } catch (_) {
    return String(url).slice(0, 40);
  }
}

/**
 * 通过 MAIN world 页面桥发请求（等价于在页面 Console 里 fetch，带页面登录态）。
 */
BS.pageFetchJson = function pageFetchJson(url, creds = "include") {
  return new Promise((resolve, reject) => {
    const id = `bs${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
    const timer = setTimeout(() => {
      window.removeEventListener("message", onMessage);
      reject(new Error("页面桥超时（page-bridge 未注入？）"));
    }, 4000);
    function onMessage(event) {
      if (event.source !== window) return;
      const data = event.data;
      if (!data || data.type !== "bs:res" || data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      if (!data.ok) {
        reject(new Error(data.error || `HTTP ${data.status}`));
        return;
      }
      try {
        resolve(JSON.parse(data.text));
      } catch (_) {
        reject(new Error("响应不是 JSON"));
      }
    }
    window.addEventListener("message", onMessage);
    try {
      window.postMessage({ type: "bs:req", id, url: new URL(url, location.href).toString(), creds }, "*");
    } catch (e) {
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      reject(e);
    }
  });
};

/**
 * 带降级的 JSON 请求（四档）。
 *
 * content script 的跨源 fetch 可能因为以下原因直接 TypeError: Failed to fetch
 *  - `Access-Control-Allow-Origin: *` 与服务端 `credentials` 冲突
 *  - CDN 校验 Referer / 不返回 CORS 头
 *  - 扩展源与页面源在 Cookie 归属上的差异
 *
 * 顺序：隔离世界(带/不带 cookie) → 页面桥(带/不带) → background 代理。
 * @param {string} url
 * @param {{creds?: 'include'|'omit', viaBg?: boolean, viaPage?: boolean}} [opts]
 */
BS.jsonFetch = async function jsonFetch(url, opts = {}) {
  const abs = new URL(url, location.href).toString();
  const first = opts.creds === "omit" ? "omit" : "include";
  const second = first === "include" ? "omit" : "include";
  const failures = [];

  const direct = async (creds) => {
    const res = await fetch(abs, { credentials: creds, cache: "no-store", mode: "cors" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  };

  // 页面桥放最前：实测「页面上下文 + credentials:'include'」是能通的那一条
  const strategies = [];
  if (opts.viaPage !== false) {
    strategies.push(
      ["页面桥(带cookie)", () => BS.pageFetchJson(abs, first)],
      ["页面桥", () => BS.pageFetchJson(abs, second)],
    );
  }
  strategies.push(["直接(带cookie)", () => direct(first)], ["直接", () => direct(second)]);
  if (opts.viaBg !== false) {
    strategies.push([
      "后台代理",
      async () => {
        const res = await chrome.runtime.sendMessage({ type: "bs:fetch-json", url: abs, creds: first });
        if (!res || !res.ok) throw new Error((res && res.error) || "后台代理失败");
        return res.data;
      },
    ]);
  }

  for (const [name, run] of strategies) {
    try {
      const data = await run();
      return data;
    } catch (e) {
      failures.push(`${name}: ${(e && e.message) || e}`);
    }
  }

  console.error(`[BiliSub] 请求 ${abs.slice(0, 120)} 全部失败：`, failures.join(" | "));
  throw new Error(`请求 ${hostOf(abs)} 失败：${failures[0] || "未知"}`);
};

function pageFromUrl() {
  return Number(new URLSearchParams(location.search).get("p")) || 1;
}

function bvidFromUrl() {
  const m = location.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/);
  if (m) return m[1];
  const q = new URLSearchParams(location.search).get("bvid");
  return q || "";
}

/**
 * 解析当前视频上下文。
 * cid 优先走 view 的 pages[p-1]（避免多 P 拿到 P1），view 不可用时退 pagelist。
 */
BS.resolveVideo = async function resolveVideo() {
  const bvid = bvidFromUrl();
  if (!bvid) throw new Error("不是 B 站视频页");
  const p = pageFromUrl();

  let title = document.title.replace(/[_\-—].*哔哩哔哩.*$/, "").trim();
  let author = "";
  let authorMid = 0;
  let duration = 0;
  let aid = 0;
  let cid = 0;
  const notes = [];

  try {
    const view = await BS.jsonFetch(`//api.bilibili.com/x/web-interface/view?bvid=${bvid}`);
    if (view && view.code === 0) {
      const d = view.data || {};
      const page = Array.isArray(d.pages) ? d.pages[p - 1] : null;
      aid = d.aid || 0;
      cid = (page && page.cid) || d.cid || 0;
      title = d.title || title;
      author = (d.owner && d.owner.name) || "";
      authorMid = (d.owner && d.owner.mid) || 0;
      duration = (page && page.duration) || d.duration || 0;
    } else {
      notes.push(`view code=${view && view.code}`);
    }
  } catch (e) {
    notes.push(`view: ${e.message || e}`);
  }

  if (!cid) {
    try {
      const pl = await BS.jsonFetch(`//api.bilibili.com/x/player/pagelist?bvid=${bvid}`);
      if (pl && pl.code === 0 && Array.isArray(pl.data) && pl.data.length) {
        const page = pl.data[p - 1] || pl.data[0];
        cid = (page && page.cid) || 0;
        duration = duration || (page && page.duration) || 0;
        if (!title && page.part) title = page.part;
      } else {
        notes.push(`pagelist code=${pl && pl.code}`);
      }
    } catch (e) {
      notes.push(`pagelist: ${e.message || e}`);
    }
  }

  if (!cid) {
    console.error("[BiliSub] 解析 cid 失败：", notes.join(" | "));
    throw new Error(`拿不到 cid（${notes.join("；") || "未知原因"}）`);
  }

  return {
    bvid,
    p,
    aid,
    cid,
    title,
    author,
    authorMid,
    duration,
    partTitle: "",
    url: `https://www.bilibili.com/video/${bvid}${p > 1 ? `?p=${p}` : ""}`,
  };
};

/** 取字幕轨道列表（三级降级）。 */
BS.fetchTracks = async function fetchTracks(video) {
  const { bvid, aid, cid } = video;

  const errors = [];
  if (aid) {
    const primary = `//api.bilibili.com/x/player/wbi/v2?aid=${aid}&cid=${cid}`;
    try {
      const r = await BS.jsonFetch(primary);
      if (r && r.code === 0) {
        const tracks = (r.data && r.data.subtitle && r.data.subtitle.subtitles) || [];
        return { tracks: tracks.filter((t) => t && t.subtitle_url), via: "wbi/v2" };
      }
      errors.push(`wbi/v2 code=${r && r.code}`);
    } catch (e1) {
      errors.push(`wbi/v2: ${e1.message || e1}`);
    }
  } else {
    errors.push("wbi/v2 跳过（缺 aid）");
  }

  try {
    const r2 = await BS.jsonFetch(
      `//api.bilibili.com/x/player/v2?bvid=${bvid}&cid=${cid}${aid ? `&aid=${aid}` : ""}`,
    );
    if (r2 && r2.code === 0) {
      const tracks = (r2.data && r2.data.subtitle && r2.data.subtitle.subtitles) || [];
      return { tracks: tracks.filter((t) => t && t.subtitle_url), via: "player/v2" };
    }
    errors.push(`player/v2 code=${r2 && r2.code}`);
  } catch (e2) {
    errors.push(`player/v2: ${e2.message || e2}`);
  }

  try {
    if (!aid) throw new Error("缺 aid");
    const signed = await BS.signedGet("/x/player/wbi/v2", { aid, cid });
    const tracks = (signed.data && signed.data.subtitle && signed.data.subtitle.subtitles) || [];
    return { tracks: tracks.filter((t) => t && t.subtitle_url), via: "wbi/v2(signed)" };
  } catch (e3) {
    errors.push(`signed: ${e3.message || e3}`);
  }

  console.error("[BiliSub] 字幕轨道全部获取失败：", errors.join(" | "));
  throw new Error(`拿不到字幕轨道（${errors.join("；")}）`);
};

function cacheKey(video, lan) {
  return `bs:sub:${video.bvid}_${video.p}_${lan}`;
}

async function readCache(key) {
  try {
    const got = await chrome.storage.local.get(key);
    const hit = got && got[key];
    if (hit && Date.now() - hit.fetchedAt < CACHE_TTL) return hit;
  } catch (_) {
    /* ignore */
  }
  return null;
}

async function writeCache(key, value) {
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch (_) {
    /* 配额满时忽略 */
  }
}

/**
 * 取当前视频字幕。
 * @returns {{source:'native'|'none', via:string, track:object|null, tracks:Array, segments:Array, fetchedAt:number}}
 */
BS.fetchTranscript = async function fetchTranscript(video, preferredLan) {
  const { tracks, via } = await BS.fetchTracks(video);
  const track = preferredLan
    ? tracks.find((t) => String(t.lan) === String(preferredLan)) || BS.pickTrack(tracks)
    : BS.pickTrack(tracks);
  if (!track) {
    return { source: "none", via, track: null, tracks, segments: [], fetchedAt: Date.now() };
  }

  const key = cacheKey(video, track.lan);
  const cached = await readCache(key);
  if (cached && cached.segments && cached.segments.length) {
    return { ...cached, track, tracks, via: `${via}(cache)` };
  }

  // 注意：subtitle_url 带 auth_key 时效签名，只缓存解析后的 segments，不缓存 URL
  const url = String(track.subtitle_url).replace(/^\/\//, "https://").replace(/^http:/, "https:");
  let json;
  try {
    // CDN 通常返回 Access-Control-Allow-Origin: *，带 cookie 反而会被浏览器判 CORS 失败 → 先不带
    json = await BS.jsonFetch(url, { creds: "omit" });
  } catch (e) {
    throw new Error(`字幕文件下载失败（${e.message || e}）`);
  }
  const segments = BS.parseSubtitleJson(json);
  const record = {
    source: "native",
    via,
    lan: track.lan,
    lanDoc: track.lan_doc || track.lan,
    langSource: BS.trackSource(track),
    segments,
    fetchedAt: Date.now(),
  };
  await writeCache(key, record);
  return { ...record, track, tracks };
};

/**
 * 从自建服务器读取已落盘的字幕（ASR 结果 / 插件回传的官方字幕）。
 * 未配置服务器、没有记录或网络失败时返回 null —— 只作兜底，不打断主链路。
 * @returns {Promise<{segments:Array, serverSource:string, lang:string, updatedAt:number}|null>}
 */
BS.fetchServerTranscript = async function fetchServerTranscript(video) {
  if (!video || !video.bvid) return null;
  try {
    const record = await BS.api("GET", `/api/v2/subtitles/${video.bvid}?p=${video.p || 1}`);
    const segments = BS.normalizeSegments(record && record.segments);
    if (!segments.length) return null;
    return {
      segments,
      serverSource: String((record && record.source) || ""),
      lang: String((record && record.lang) || ""),
      updatedAt: Number((record && record.updated_at) || 0),
    };
  } catch (_) {
    return null;
  }
};

/** 取 DASH 音频直链（ASR 用）；失败返回空串。 */
BS.getAudioUrl = async function getAudioUrl(video) {
  const { bvid, cid, aid } = video;
  const tryParse = (r) => {
    const dash = r && r.data && r.data.dash;
    const audio = (dash && dash.audio) || [];
    if (!audio.length) {
      const durl = r && r.data && r.data.durl;
      if (Array.isArray(durl) && durl[0]) return durl[0].baseUrl || durl[0].url || "";
      return "";
    }
    const best = [...audio].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))[0];
    return best.baseUrl || (best.baseUrl2 || "") || "";
  };
  try {
    const r = await BS.jsonFetch(
      `//api.bilibili.com/x/player/playurl?bvid=${bvid}&cid=${cid}&fnval=16&fourk=1`,
    );
    if (r && r.code === 0) return tryParse(r);
  } catch (_) {
    /* fallthrough */
  }
  try {
    const r2 = await BS.signedGet("/x/player/wbi/playurl", {
      bvid,
      cid,
      fnval: 16,
      fourk: 1,
      aid: aid || "",
    });
    if (r2 && r2.code === 0) return tryParse(r2);
  } catch (_) {
    /* give up */
  }
  return "";
};

/** 经 background 代理访问自建服务器。 */
BS.api = async function api(method, path, body) {
  const res = await chrome.runtime.sendMessage({ type: "bs:api", method, path, body });
  if (!res) throw new Error("后台无响应");
  if (!res.ok) {
    // 把 HTTP 状态码挂在 Error 上，调用方按语义区分「空状态」与「真失败」
    const err = new Error(res.error || `HTTP ${res.status}`);
    err.status = Number(res.status) || 0;
    throw err;
  }
  return res.data;
};
