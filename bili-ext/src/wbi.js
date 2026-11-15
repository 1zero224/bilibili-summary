/**
 * B 站 WBI 签名（兜底用）。
 *
 * 说明：主链路（页面上下文请求 /x/player/wbi/v2?aid&cid）实测不需要签名；
 * 本文件只在主链路与 player/v2 都失败、或需要 /x/web-interface/wbi/view 反查 cid 时使用。
 *
 * 算法与实现移植自 B-LIPSTICK/bili-digest (MIT, Copyright (c) 2026 bili-digest contributors)
 * 文件 lib/wbi.js；本项目同样以 MIT 使用，保留此声明。
 */
globalThis.BS = globalThis.BS || {};

BS.MIXIN_KEY_ENC_TAB = [
  46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
  27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
  37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
  22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
];

BS.getMixinKey = function getMixinKey(imgKey, subKey) {
  const raw = `${imgKey || ""}${subKey || ""}`;
  if (raw.length < 64) throw new Error("getMixinKey: key 不完整");
  let out = "";
  for (let i = 0; i < 32; i += 1) out += raw[BS.MIXIN_KEY_ENC_TAB[i]];
  return out;
};

/** https://i0.hdslb.com/bfs/wbi/abc.png -> "abc" */
BS.extractWbiKey = function extractWbiKey(url) {
  if (!url) return "";
  return String(url).split("/").pop().split(".")[0];
};

function filterChars(value) {
  return String(value).replace(/[!'()*]/g, "");
}

function rotateLeft(value, count) {
  return ((value << count) | (value >>> (32 - count))) >>> 0;
}

BS.md5Hex = function md5Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const bitLength = bytes.length * 8;
  const paddedLength = (((bytes.length + 8) >> 6) + 1) * 64;
  const buffer = new Uint8Array(paddedLength);
  buffer.set(bytes);
  buffer[bytes.length] = 0x80;

  const view = new DataView(buffer.buffer);
  view.setUint32(paddedLength - 8, bitLength >>> 0, true);
  view.setUint32(paddedLength - 4, Math.floor(bitLength / 4294967296), true);

  const shifts = [
    [7, 12, 17, 22],
    [5, 9, 14, 20],
    [4, 11, 16, 23],
    [6, 10, 15, 21],
  ];
  const constants = Array.from({ length: 64 }, (_, i) =>
    Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) >>> 0,
  );

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  for (let offset = 0; offset < paddedLength; offset += 64) {
    const words = new Array(16);
    for (let i = 0; i < 16; i += 1) words[i] = view.getUint32(offset + i * 4, true);

    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;

    for (let i = 0; i < 64; i += 1) {
      let f;
      let index;
      if (i < 16) {
        f = (b & c) | (~b & d);
        index = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        index = (5 * i + 1) % 16;
      } else if (i < 48) {
        f = b ^ c ^ d;
        index = (3 * i + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        index = (7 * i) % 16;
      }
      const mixed = (f + a + constants[i] + words[index]) >>> 0;
      a = d;
      d = c;
      c = b;
      b = (b + rotateLeft(mixed, shifts[Math.floor(i / 16)][i % 4])) >>> 0;
    }

    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }

  const le = (value) => {
    let out = "";
    for (let i = 0; i < 4; i += 1) out += ((value >>> (i * 8)) & 0xff).toString(16).padStart(2, "0");
    return out;
  };
  return le(a0) + le(b0) + le(c0) + le(d0);
};

BS.encWbi = function encWbi(params, mixinKey, wts = Math.floor(Date.now() / 1000)) {
  const withTs = { ...params, wts: String(wts) };
  const query = Object.keys(withTs)
    .sort()
    .map((k) => `${k}=${encodeURIComponent(filterChars(withTs[k]))}`)
    .join("&");
  return { w_rid: BS.md5Hex(`${query}${mixinKey}`), wts };
};

/** 内存内缓存 mixinKey，12h 过期。 */
let mixinCache = { key: "", at: 0 };

BS.getMixinKeyCached = async function getMixinKeyCached(force = false) {
  if (!force && mixinCache.key && Date.now() - mixinCache.at < 12 * 3600 * 1000) {
    return mixinCache.key;
  }
  const nav = await fetch("//api.bilibili.com/x/web-interface/nav", {
    credentials: "include",
    cache: "no-store",
  }).then((r) => r.json());
  const img = nav && nav.data && nav.data.wbi_img && nav.data.wbi_img.img_url;
  const sub = nav && nav.data && nav.data.wbi_img && nav.data.wbi_img.sub_url;
  if (!img || !sub) throw new Error("获取 WBI 密钥失败");
  const key = BS.getMixinKey(BS.extractWbiKey(img), BS.extractWbiKey(sub));
  mixinCache = { key, at: Date.now() };
  return key;
};

/** 带签名的 GET（仅用于兜底）。 */
BS.signedGet = async function signedGet(path, params, retry = true) {
  const run = async (force) => {
    const mixinKey = await BS.getMixinKeyCached(force);
    const { w_rid, wts } = BS.encWbi(params, mixinKey);
    const qs = new URLSearchParams({ ...params, w_rid, wts });
    // 走统一的多档请求（直接 → 页面桥 → 后台代理）
    return await BS.jsonFetch(`//api.bilibili.com${path}?${qs.toString()}`, { creds: "include" });
  };
  try {
    const data = await run(false);
    if (data && typeof data.code === "number" && data.code !== 0) {
      throw new Error(`code ${data.code} ${data.message || ""}`);
    }
    return data;
  } catch (err) {
    const msg = String((err && err.message) || err);
    if (retry && /-403|-412|403|412/.test(msg)) return run(true);
    throw err;
  }
};
