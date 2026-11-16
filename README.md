# Bilibili Summary

B 站视频字幕、ASR 转写与 AI 总结工具：**Chrome 扩展 + FastAPI 后端 + 服务器历史面板**。

- 扩展：读取官方 CC / AI 字幕，合并句子、跟随播放、点击跳转、复制导出；无字幕时一键 ASR。
- 后端：`/api/v2` 提供字幕存储、ASR 任务、总结生成与历史查询。
- 历史页：`/history`（只列跑过 ASR 的视频），字幕全文与详细总结均可点击时间跳转。
- Telegram Bot：发送 B 站链接即可触发总结并回传进度（可选）。

旧版 Web UI（URL / UP 主 / 收藏夹 / 浏览 / 设置）与 macOS 桌面端已移除，运行时配置统一由 `.env.local` 或环境变量提供。

## 架构

```text
Chrome 扩展 (bili-ext/) --X-BS-Token--> /api/v2/*
Telegram Bot --------------------------> 任务队列
                                              |
                                              v
                          DATA_DIR/{subtitles,summary,ass,media}
                                              |
浏览器 /history <------ server.py 渲染的只读历史页
```

- 字幕/总结的权威存储是服务器 `DATA_DIR`。
- 扩展刷新后会回读服务器已落盘的字幕（含 ASR 结果），不依赖浏览器缓存。
- 面板 UI 运行在扩展自己的 iframe 中，与 B 站页面 DOM/CSS 完全隔离。

## 快速开始

### 1. 配置

在仓库根目录创建 `.env.local`（Docker 会挂载进容器）：

```env
# AI 总结（必填）
ANTHROPIC_AUTH_TOKEN=your_api_key
ANTHROPIC_BASE_URL=https://api.anthropic.com
# DEFAULT_MODEL=mimo-v2.5-pro

# B 站登录态（服务端下载/ASR 兜底需要）
BILIBILI_SESSION_TOKEN=your_sessdata
BILIBILI_BILI_JCT=your_bili_jct
# BILIBILI_AC_TIME_VALUE=

# ASR 模式：local | bailian | mimo（默认 local）
ASR_MODE=local

# 小米 Mimo ASR（ASR_MODE=mimo）
# MIMO_ASR_API_KEY=
# MIMO_ASR_BASE_URL=https://api.xiaomimimo.com/v1
# MIMO_ASR_MODEL=mimo-v2.5-asr
# MIMO_ASR_LANGUAGE=auto

# 阿里云百炼 ASR（ASR_MODE=bailian）
# BAILIAN_API_KEY=
# BAILIAN_ASR_MODEL=qwen3-asr-flash-filetrans
# BAILIAN_ASR_BASE_URL=https://dashscope.aliyuncs.com/api/v1

# 百炼 ASR 的文件中转（Cloudflare R2）
# CLOUDFLARE_R2_ACCOUNT_ID=
# CLOUDFLARE_R2_BUCKET=
# CLOUDFLARE_R2_ACCESS_KEY_ID=
# CLOUDFLARE_R2_SECRET_ACCESS_KEY=
# CLOUDFLARE_R2_PUBLIC_BASE_URL=

# Telegram Bot（可选）
# TELEGRAM_BOT_ENABLED=false
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_ALLOWED_USER_IDS=
# TELEGRAM_OUTPUT_FOLDER=Telegram

# 鉴权（公网部署必须开启）
# BILISUMMARY_AUTH=1
# BILISUMMARY_BIND=127.0.0.1
```

配置只在启动时读取，修改后需要重启容器。

### 2. 启动后端

```bash
mkdir -p data
docker compose up -d --build
```

默认监听 `127.0.0.1:18520`。首次启动会在 `data/.api_token` 生成访问令牌：

```bash
cat data/.api_token
open "http://127.0.0.1:18520/history?token=<token>"
```

也可以直接运行：

```bash
pip install -r requirements.txt
python server.py          # 或 uvicorn server:app --host 127.0.0.1 --port 18520
```

### 3. 安装扩展

1. 按 `bili-ext/README.md` 把 `manifest.json` 的 `host_permissions` 换成你的服务器域名（本机调试保留 `http://127.0.0.1:18520/*`）。
2. Chrome → `chrome://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选择 `bili-ext/`。
3. 扩展选项里填服务器地址与 `data/.api_token` 中的令牌。

## API

所有 `/api/v2/*` 请求带 `X-BS-Token`；`/api/v2/health` 与 `/api/status` 公开。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v2/health` | 健康检查、ASR/登录态能力 |
| GET | `/api/v2/subtitles/{bvid}?p=` | 读取已落盘字幕（含 `segments`） |
| POST | `/api/v2/subtitles/{bvid}/import` | 回传官方字幕 |
| POST | `/api/v2/subtitles/{bvid}/asr` | 提交 ASR 任务 |
| GET | `/api/v2/tasks/{id}` / `/stream` | 任务状态（轮询 / SSE） |
| GET | `/api/v2/summaries/{bvid}?p=` | 读取总结 |
| POST | `/api/v2/summaries/{bvid}` | 生成/重新生成总结 |
| GET | `/api/v2/history?q=&limit=&cursor=` | 历史列表 |
| GET | `/history`、`/history/{bvid}` | 服务器历史页 |
| GET | `/api/status` | Docker HEALTHCHECK 探活 |

完整请求/响应字段见 [`bili-ext/README.md`](bili-ext/README.md)。

## 项目结构

```text
server.py                    # FastAPI 入口：/api/v2、/history、Telegram
summarize.py                 # AI 总结与字幕生成管线
routes/
  deps.py                    # 凭证、任务日志、文件夹、进度总线
  bilibili_downloader.py     # 视频/音频下载
  whisper.py                 # ASR 统一调度（local/bailian/mimo）
  bailian_asr.py             # 阿里云百炼 ASR
  mimo_asr.py                # 小米 Mimo ASR
  r2_storage.py              # Cloudflare R2 文件中转
  telegram_bot.py            # Telegram Bot
  v2/                        # 插件 API、存储、任务、历史页
bili-ext/                    # Chrome MV3 扩展
tests/                       # 单元测试
```

## 测试

```bash
python -m pytest -q
cd bili-ext && node --test tests/core.test.mjs
```

## 来源与致谢

本项目最初基于 [jackwener/bilibili-summary](https://github.com/jackwener/bilibili-summary) 进行大规模重构与扩展。上游仓库未声明开源许可证，其原始代码版权归原作者所有；本仓库保留来源说明，但不代表上游代码随本项目重新授权。

- 上游作者：jackwener
- 本仓库新增与修改部分：1zero224
- WBI 签名实现参考 [B-LIPSTICK/bili-digest](https://github.com/B-LIPSTICK/bili-digest)（MIT，声明保留在 `bili-ext/src/wbi.js`）
- 字幕合并/导出设计参考 [IndieKKY/bilibili-subtitle](https://github.com/IndieKKY/bilibili-subtitle)（MIT）

若你是上游作者并希望调整署名或许可方式，请通过 Issue 联系。
