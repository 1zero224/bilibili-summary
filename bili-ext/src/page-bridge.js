/**
 * MAIN world 页面桥：在**页面自身的 JS 上下文**里发请求。
 *
 * 为什么需要它：
 *   content script 跑在隔离世界，跨源 fetch 受页面 Origin 的 CORS 与 Cookie 归属影响；
 *   实测「页面上下文中 fetch('//api.bilibili.com/x/player/wbi/v2?...', {credentials:'include'})」
 *   是能拿到字幕轨道的。这个桥把请求转发给页面上下文执行，等价于在 Console 里跑那一段。
 *
 * 通信：content script → window.postMessage({type:'bs:req'}) → 这里 fetch → postMessage({type:'bs:res'})
 * 只做 JSON GET，不接受任意方法/头，避免被页面脚本滥用。
 */
(() => {
  const REQ = "bs:req";
  const RES = "bs:res";
  const seen = new Set();

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.type !== REQ || typeof data.id !== "string") return;
    if (seen.has(data.id)) return;
    seen.add(data.id);
    if (seen.size > 200) seen.clear();

    const url = String(data.url || "");
    if (!/^https:\/\/([\w-]+\.)*(bilibili\.com|hdslb\.com)\//.test(url)) {
      window.postMessage({ type: RES, id: data.id, ok: false, error: "域名不在白名单" }, "*");
      return;
    }

    try {
      const res = await fetch(url, {
        credentials: data.creds === "include" ? "include" : "omit",
        cache: "no-store",
      });
      const text = await res.text();
      window.postMessage({ type: RES, id: data.id, ok: res.ok, status: res.status, text }, "*");
    } catch (e) {
      window.postMessage(
        { type: RES, id: data.id, ok: false, error: String((e && e.message) || e) },
        "*",
      );
    }
  });
})();
