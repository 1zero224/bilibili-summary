# BiliSub 面板（Chrome/Edge MV3，自用）

> **部署提示**：后端需要一个可访问的 HTTPS 地址（扩展与 Caddy/反代均不在本仓库范围内）。
> 首次使用：`manifest.json` 的 `host_permissions` 加入你的域名 → 扩展选项里填服务器地址与令牌。
> 令牌由后端首次启动生成，落在 `$DATA_DIR/.api_token`。

## 状态

| 能力 | 状态 |
|---|---|
| 面板 = **iframe**（扩展自己的文档），插进 `#danmukuBox` 第一个子节点 —— 与参考实现 IndieKKY 一致 | ✅ 默认 |
| iframe **默认折叠**（44px 顶栏），展开后跟随播放器高度（320–680） | ✅ |
| 找不到 `#danmukuBox` 时退回 fixed 浮动（8s 后，同样不影响页面排版） | ✅ |
| 第一行 = 分段选项卡（字幕/总结），点选项卡展开 | ✅ |
| 深色模式（含格式下拉、总结正文、chip、滚动条） | ✅ |

> **为什么必须是 iframe**：直接往 B 站容器里插 DOM（哪怕用 Shadow DOM）会打断它自己的组件渲染——实测表现为封面、UP 主头像、评论区加载不出来；`scrollIntoView` 还会把整页滚走（顶栏消失）。iframe 把 DOM/CSS/滚动/JS 全部隔离，这是参考实现唯一可靠的形态。
| 官方/AI 字幕抓取（页面上下文，三级降级） | ✅ 已实现 |
| 短语级字幕合并成句 / 逐条切换 | ✅ 已实现 |
| 跟随播放高亮 + 自动滚动（手动滚动暂停 8s） | ✅ 已实现 |
| 点击行跳转（`video.currentTime`） | ✅ 已实现 |
| 复制全文 / 导出 srt、ass、vtt、txt、txt+时间、json | ✅ 已实现 |
| 多 P 切换（URL `p` → `view.pages[p-1].cid`） | ✅ 已实现 |
| 字幕本地缓存（只缓存 segments，不缓存带签名的 URL） | ✅ 已实现 |
| 字幕回传服务器 `POST /api/v2/subtitles/{bvid}/import` | ✅ 已实现；底部状态栏显示「同步：已回传 / 未回传（原因）」，点它打开选项 |
| 无字幕 → 一键 ASR（音频直链 → 服务端 playurl → 整段视频 三级兜底） | ✅ 前端 + 后端已实现 |
| 「总结」页：读/生成详细总结，`[[时间段]]` 变可点击 chip | ✅ 已实现 |
| 任务进度：SSE 推送 + 轮询兜底 | ✅ 已实现 |
| 服务器端历史页 `/history` | ✅ 只列 **ASR** 结果，封面网格（多行多列）、搜索标题/BV/UP |
| 历史详情页 `/history/{bvid}` | ✅ 字幕全文（时间戳可点）+ 详细总结（`[[时间段]]` 可点）+ 在 B 站打开 |
| 明暗主题 | ✅ 历史/详情页与设置页都是双 token，表单控件显式配色 |
| CC 挂载 | ⛔ 按决定不做 |

## 安装（unpacked）

1. 把 `manifest.json` 的 `host_permissions` 里 `https://your-server.example/*` 换成你的域名（必须是 https；本机调试保留 `http://127.0.0.1:18520/*`）。
2. Chrome → `chrome://extensions` → 打开"开发者模式" → "加载已解压的扩展程序" → 选本目录 `bili-ext`。
3. 打开扩展选项（右键图标 → 选项），**服务器地址已预填**，只需粘贴**访问 Token** → 点"测试连接"。

> 不配服务器也能用：字幕抓取、面板、同步、导出全部是纯前端能力；只有"一键转写""总结""历史面板"需要服务器。

## 后端契约（已在 `D:\work\bilibili-summary` 实现：`routes/v2/`）

所有 `/api/v2/*` 请求需带 `X-BS-Token`（`/api/v2/health` 免令牌）；CORS 允许 `chrome-extension://*`（正常路径走 background 代理，不依赖 CORS）。

| 方法 | 路径 | 请求 | 响应 | 实现 |
|---|---|---|---|---|
| GET | `/api/v2/health` | — | `{ok, version, features:{asr_mode, asr_label, bilibili_logged_in, ai_configured}}` | `api.py` |
| GET | `/api/v2/videos/{bvid}?p=` | — | `{title,duration,cid,cover,author,has_subtitle,subtitle_source,subtitle_count}` | `api.py` |
| GET | `/api/v2/subtitles/{bvid}?p=` | — | 字幕记录（含 `segments`） | `api.py` |
| POST | `/api/v2/subtitles/{bvid}/import` | `{p, cid, source, lang, title, duration, segments:[{start,end,text}]}` | `{stored, path, count}` | `api.py` → `store.py` |
| POST | `/api/v2/subtitles/{bvid}/asr` | `{p, cid, title, duration, source_url, audio_url, audio_only}` | `{task_id, status, title, duration}` | `api.py` → `tasks.py` |
| GET | `/api/v2/tasks/{task_id}` | — | `{status:'queued'\|'running'\|'done'\|'failed', step, progress, segments?, count, error?}` | `tasks.py` |
| GET | `/api/v2/tasks/{task_id}/stream` | — | **SSE**：`state` → `update`×N（含 `: ping` 心跳）→ `final`（带 `segments`/`summary`） | `tasks.py` |
| GET | `/api/v2/summaries/{bvid}?p=` | — | `{title,duration,author,cover,url,path,summary,detailed,updated_at}` / 404 | `summary.py` |
| POST | `/api/v2/summaries/{bvid}` | `{p, type:'detailed'\|'brief', force, subdir, model}` | 已有且 `force=false` → `{cached:true, task_id:null, summary}`；否则 `{cached:false, task_id}` | `summary.py` → `tasks.py` |
| GET | `/api/v2/history?q=&limit=&cursor=` | — | `{items:[{bvid,page,title,duration,author,cover,folder,has_summary,has_subtitle,subtitle_source,updated_at}], total, next_cursor}` | `store.py` |
| GET | `/history` | — | 服务器端历史主面板（HTML，零构建） | `page.py` |

**存储**：`DATA_DIR/subtitles/<bvid>_p<page>_<source>.json`（权威）+ 同步一份 `DATA_DIR/ass/<subdir>/<title>.ass`（让既有 Web UI 的详情页也能读到）。历史列表 = 既有 `summary/**/*.meta.json` ∪ `subtitles/*.json`。

**ASR 音源（三级，已实现于 `routes/v2/audio.py` + `tasks.py`）**：

1. **插件直链**：content 侧从 `playurl` 拿 `dash.audio[0].baseUrl` 交给后端下载（快路径，不依赖服务端登录态）；
2. **服务端 playurl 只下音频**：复用 `_get_playurl` + `_parse_dash_streams` 取最高带宽音轨（约 5–20MB/10min，需要服务端已登录）；
3. **整段视频兜底**：前两档都失败时走既有 `download_bilibili_media` 下 mp4 再交给 `transcribe_media`（bailian/mimo 内部 `-vn` 抽音，local 由 av 读音轨）。

> 说明：用户实测「境外服务器能正常下载视频并提取音频」，所以不需要"插件下载音频再上传服务器"这条（`/api/v2/asr/upload` 仅作将来可选兜底）。

## 服务器端（已实现）怎么开

```bash
# 1) 首次启动自动生成令牌
python server.py
# 控制台会打印：🔑 X-BS-Token: ab12cd…ef34
# 令牌文件：$DATA_DIR/.api_token（0600）

# 2) 公网部署必须打开鉴权（默认关闭，避免影响本机既有 Web UI）
export BILISUMMARY_AUTH=1
export BILISUMMARY_BIND=127.0.0.1     # 由 Caddy/nginx 反代，不要直接 0.0.0.0 暴露
python server.py

# 3) 自检
curl -s localhost:18520/api/v2/health
curl -s -H "X-BS-Token: <token>" localhost:18520/api/v2/history
```

- 浏览器访问 Web UI 时首次带一次 `?token=<token>`，中间件会种 `bs_token` Cookie，之后正常用。
  （反向代理后建议 `uvicorn --proxy-headers` 或让 Caddy/nginx 传 `X-Forwarded-Proto`，否则中间件按 http 判断会给 Cookie 少加 `Secure`。）
- `BILISUMMARY_AUTH=1` 时**所有 `/api/*`**（不只是 v2）都要令牌——否则公网上任何人都能 `POST /api/settings` 改你的 key。
- 建议在反代再加一层 Basic Auth / IP 白名单，并给 `/api/v2/subtitles/*/asr` 限速（防刷 ASR 费用）。

## 请求链路（为什么会有四档）

content script 的跨源 fetch 在 B 站页面上可能直接 `TypeError: Failed to fetch`，因此 `BS.jsonFetch()` 按下面顺序逐档尝试，全部失败才抛错（并把每档原因打到 Console）：

1. **MAIN world 页面桥**（`src/page-bridge.js`，`world: "MAIN"`）：在页面自身上下文里 fetch，等价于在 Console 里跑 —— **你之前验证成功的那条**；
2. **隔离世界直接 fetch**（带 cookie / 不带 cookie 各一次）：服务端 `Access-Control-Allow-Origin: *` 与 `credentials` 冲突时会失败，故降级一次不带 cookie；
3. **background 代理**（`bs:fetch-json`）：扩展源不受内容脚本的 CORS 限制，用于 CDN 类无需登录的请求。

字幕 JSON（`aisubtitle.hdslb.com`，带 `auth_key`）优先**不带 cookie**取；B 站 API（`view` / `pagelist` / `player/v2` / `wbi/v2`）优先**带 cookie**。

失败时面板会显示「重试 / 复制诊断」两个按钮，Console 里会有 `[BiliSub] 请求 … 全部失败：…` 的分档原因。

## 关键实现约束（都来自实测，别改坏）

1. **字幕请求放在 content script（页面上下文）**，带 `credentials:'include'`。实测 `GET /x/player/wbi/v2?aid=&cid=` **不需要 WBI 签名**返回 `code 0`；放到 Service Worker 里请求反而有被风控的风险。
2. **只传 `aid` + `cid`**（不要额外塞 `bvid` 等参数），失败再降级 `/x/player/v2`，最后才用 WBI 签名兜底（`src/wbi.js`）。
3. **`subtitle_url` 带 `auth_key` 时效签名** → 绝不持久化 URL，只缓存解析后的 `segments`（`chrome.storage.local`，key `bs:sub:{bvid}_{p}_{lan}`，TTL 6h）。
4. **B 站 AI 字幕是短语级**（实测 0.48s / "同志们"）→ 必须合并成句（标点 / 间隔 >0.8s / 长度 ≥42 三重判据），否则列表碎到不可读。
5. **多 P 的 cid 必须取 `view.pages[p-1].cid`**，`view.data.cid` 恒为 P1，会串集。
6. **不做 CC**（B 站原生 CC 与我们自绘层会重影，且没有可靠的官方 API）。

## 测试

```bash
node --test bili-ext/tests/core.test.mjs      # 前端纯函数（15 例）
python -m pytest tests/test_v2_store.py tests/test_v2_summary.py -q   # 后端 store + summary + SSE（12 例）
```

## 调试

- content script 报错：视频页 DevTools Console（隔离世界日志会带扩展前缀），或 `chrome://extensions` → 本扩展 → "检查视图"。
- 服务器请求失败：`chrome://extensions` → "Service Worker" → Console，可见 `bs:api` 的返回。
- 面板不出现：确认 URL 匹配 `https://www.bilibili.com/video/*`，并刷新页面（content script 在 `document_idle` 注入）。

## 归属与许可

- 本目录代码：与主仓库一致。
- `src/wbi.js`（WBI 签名 + 纯 JS MD5）移植自 [B-LIPSTICK/bili-digest](https://github.com/B-LIPSTICK/bili-digest) `lib/wbi.js`，**MIT**，Copyright (c) 2026 bili-digest contributors，已在文件头保留声明。
- 字幕合并/导出/时间格式化的设计参考 [IndieKKY/bilibili-subtitle](https://github.com/IndieKKY/bilibili-subtitle)（MIT）。
