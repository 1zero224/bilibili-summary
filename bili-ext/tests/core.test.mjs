/**
 * core.js 的纯函数单测（无依赖，直接跑）：
 *   node --test bili-ext/tests/
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "core.js"), "utf8");
// core.js 是普通脚本（挂到 globalThis.BS），用 indirect eval 在当前上下文求值
globalThis.BS = {};
(0, eval)(source);
const BS = globalThis.BS;

const SAMPLE = {
  body: [
    { from: 0.12, to: 0.6, sid: 1, location: 2, content: "同志们", music: 0 },
    { from: 0.6, to: 2.02, sid: 2, location: 2, content: "咱们今天讲讲欧洲啊", music: 0 },
    { from: 2.02, to: 4.08, sid: 3, location: 2, content: "也是好久没讲欧洲这边的情况了", music: 0 },
    { from: 6.5, to: 7.2, sid: 4, location: 2, content: "第一点。", music: 0 },
    { from: 7.2, to: 8.0, sid: 5, location: 2, content: "第二点！", music: 0 },
  ],
};

test("parseSubtitleJson 过滤非法项并归一化字段", () => {
  const segs = BS.parseSubtitleJson({
    body: [
      ...SAMPLE.body,
      { from: 5, to: 5, content: "零长度" },
      { from: 9, to: 10, content: "   " },
      { from: "x", to: 11, content: "坏时间" },
    ],
  });
  assert.equal(segs.length, SAMPLE.body.length);
  assert.deepEqual(Object.keys(segs[0]).sort(), ["end", "loc", "music", "start", "text"]);
  assert.equal(segs[0].start, 0.12);
});

test("mergeSegments 在句末标点/间隔/超长处断行", () => {
  const rows = BS.mergeSegments(BS.parseSubtitleJson(SAMPLE), { gap: 0.8, maxLen: 42 });
  assert.equal(rows.length, 3);
  assert.equal(rows[0].start, 0.12);
  assert.equal(rows[0].end, 4.08);
  assert.equal(rows[0].text, "同志们咱们今天讲讲欧洲啊也是好久没讲欧洲这边的情况了");
  assert.equal(rows[1].text, "第一点。");
  assert.equal(rows[2].text, "第二点！");
});

test("mergeSegments maxLen 生效", () => {
  const segs = BS.parseSubtitleJson({
    body: Array.from({ length: 6 }, (_, i) => ({
      from: i * 0.5,
      to: i * 0.5 + 0.4,
      content: "一二三四五六七八九十",
      music: 0,
      location: 2,
    })),
  });
  const rows = BS.mergeSegments(segs, { gap: 5, maxLen: 25 });
  assert.ok(rows.length >= 2, "长句应被切分");
  for (const r of rows) assert.ok(r.text.length <= 40, `行过长: ${r.text.length}`);
});

test("indexOfTime 二分命中", () => {
  const rows = BS.mergeSegments(BS.parseSubtitleJson(SAMPLE), { gap: 0.8, maxLen: 42 });
  assert.equal(BS.indexOfTime(rows, 0.01), -1);
  assert.equal(BS.indexOfTime(rows, 0.12), 0);
  assert.equal(BS.indexOfTime(rows, 3.0), 0);
  assert.equal(BS.indexOfTime(rows, 7.5), 2);
  assert.equal(BS.indexOfTime(rows, 999), rows.length - 1);
});

test("pickTrack 人工中文优先于 AI 中文", () => {
  assert.equal(BS.pickTrack([{ lan: "ai-zh", subtitle_url: "u" }, { lan: "zh-CN", subtitle_url: "v" }]).lan, "zh-CN");
  assert.equal(BS.pickTrack([{ lan: "ai-zh", subtitle_url: "u" }, { lan: "en", subtitle_url: "w" }]).lan, "ai-zh");
  assert.equal(BS.pickTrack([{ lan: "en", subtitle_url: "" }, { lan: "zh", subtitle_url: "z" }]).lan, "zh");
  assert.equal(BS.pickTrack([]), null);
});

test("trackSource 分类", () => {
  assert.equal(BS.trackSource({ lan: "ai-zh" }), "ai");
  assert.equal(BS.trackSource({ lan: "zh-CN" }), "human");
  assert.equal(BS.trackSource({ lan: "en" }), "other");
});

test("时间格式化", () => {
  assert.equal(BS.fmtTime(0), "00:00");
  assert.equal(BS.fmtTime(3725), "1:02:05");
  assert.equal(BS.fmtSrtTime(3725.456), "01:02:05,456");
  assert.equal(BS.fmtVttTime(3725.456), "01:02:05.456");
  assert.equal(BS.fmtAssTime(3725.456), "1:02:05.46");
});

test("buildExport 生成合法 srt / vtt / ass 骨架", () => {
  const rows = BS.mergeSegments(BS.parseSubtitleJson(SAMPLE), { gap: 0.8, maxLen: 42 });
  const payload = { title: "标题", url: "https://x", author: "up", rows, segments: [] };

  const srt = BS.buildExport("srt", payload);
  assert.match(srt, /^1\r?\n00:00:00,120 --> 00:00:04,080/);
  assert.equal(srt.trim().split(/\n\r?\n/).length, rows.length);

  const vtt = BS.buildExport("vtt", payload);
  assert.ok(vtt.startsWith("WEBVTT"));
  assert.ok(vtt.includes("00:00:00.120 --> 00:00:04.080"));

  const ass = BS.buildExport("ass", payload);
  assert.ok(ass.includes("[V4+ Styles]"));
  assert.equal(ass.split("Dialogue:").length - 1, rows.length);

  const json = JSON.parse(BS.buildExport("json", payload));
  assert.equal(json.title, "标题");
});

test("safeName 去非法字符", () => {
  assert.equal(BS.safeName('a/b:c*d?"e"'), "a_b_c_d__e_");
});

test("parseClock 解析多种时间写法", () => {
  assert.equal(BS.parseClock("125"), 125);
  assert.equal(BS.parseClock("02:05"), 125);
  assert.equal(BS.parseClock("1:02:05"), 3725);
  assert.equal(BS.parseClock(""), null);
  assert.equal(BS.parseClock("abc"), NaN === NaN ? null : null);
});

test("renderMarkdown 基础结构与转义", () => {
  const html = BS.renderMarkdown("## 标题\n\n- 一\n- 二\n\n正文 <b>危险</b>");
  assert.ok(html.includes("<h3>标题</h3>"));
  assert.ok(html.includes("<ul>") && html.includes("<li>一</li>"));
  assert.ok(html.includes("&lt;b&gt;危险&lt;/b&gt;"), "HTML 必须被转义");
  assert.ok(html.includes("<strong>") === false);
});

test("renderMarkdown 把 [[时间段]] 变成可点击 chip", () => {
  const html = BS.renderMarkdown("## [[时间段: 1:02-3:40]] 开场\n\n**要点**：他讲了欧洲");
  assert.ok(html.includes('class="chip" data-t="62"'), html);
  assert.ok(html.includes("1:02–3:40"));
  assert.ok(html.includes("<strong>要点</strong>"));
});

test("renderMarkdown 非数字时间段原样保留，不生成 chip", () => {
  const html = BS.renderMarkdown("[[时间段: 未知-未知]]");
  assert.ok(html.includes("[[时间段: 未知-未知]]"), html);
  assert.ok(!html.includes("chip"));
});

test("renderMarkdown 数字但格式非法的时间段退化为 span", () => {
  const html = BS.renderMarkdown("[[时间段: 1:2:3:4-9:9]]");
  assert.ok(html.includes('<span class="ts">1:2:3:4–9:9</span>'), html);
  assert.ok(!html.includes("chip"));
});

test("buildExport json 里 segments 与 rows 独立", () => {
  const rows = BS.mergeSegments(BS.parseSubtitleJson(SAMPLE), { gap: 0.8, maxLen: 42 });
  const json = JSON.parse(BS.buildExport("json", { title: "t", rows, segments: [{ start: 1, end: 2, text: "x" }] }));
  assert.equal(json.segments.length, 1);
  assert.equal(json.title, "t");
});
