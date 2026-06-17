# Bilibili Summary

Bilibili 视频 AI 摘要工具，支持语音识别、文件夹管理、批量总结和 Telegram Bot 集成。

## 功能特性

- **URL 模式**: 粘贴 Bilibili 视频链接进行总结，支持多 P 视频和合集/系列链接
- **UP 主模式**: 按 UP 主名或 UID 浏览视频列表，选择性批量总结
- **收藏夹模式**: QR 码登录、加载收藏夹、批量总结、取消收藏（带撤销）
- **浏览模式**: 卡片/紧凑视图切换、文件夹分组、批量移动和删除
- **视频详情**: 本地视频播放器 + 可点击字幕时间跳转 + 详细总结标签页
- **语音识别**: 支持本地 Whisper 和阿里云百炼 ASR 两种后端
- **详细总结**: 带时间段标记的 Markdown 详细总结，支持按需生成
- **任务日志**: 持久化任务记录，实时状态追踪，详情弹窗
- **Telegram Bot**: 发送 Bilibili 链接到 Bot 即可触发总结，实时回传进度
- **文件夹管理**: 创建/删除文件夹，移动总结记录，级联管理关联文件

## 技术栈

- 后端: FastAPI + Uvicorn
- 前端: Vanilla JS + CSS（tokenized design system）
- 桌面: pywebview
- Bilibili 集成: `bilibili-api-python`
- 视频下载: 进程内并发下载器 + FFmpeg 混流
- AI 摘要: Anthropic 兼容 API（支持小米 MIMO 平台）
- 语音识别: faster-whisper（本地）/ 阿里云百炼 DashScope（云端）
- 存储中转: Cloudflare R2（S3 兼容）
- 音视频工具: FFmpeg / FFprobe

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env.local` 文件：

```env
# AI API 配置（必填）
ANTHROPIC_AUTH_TOKEN=your_api_key
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 小米 MIMO 平台（可选，自动识别）
# ANTHROPIC_BASE_URL=https://api.xiaomimimo.com
# MIMO_API_KEY=your_mimo_key

# Telegram Bot（可选）
TELEGRAM_BOT_ENABLED=false
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_OUTPUT_FOLDER=Telegram

# 语音识别模式（可选，默认 local）
ASR_MODE=local

# 百炼 ASR（ASR_MODE=bailian 时需要）
BAILIAN_API_KEY=your_bailian_key
BAILIAN_ASR_MODEL=qwen3-asr-flash-filetrans
BAILIAN_ASR_BASE_URL=https://dashscope.aliyuncs.com/api/v1

# Cloudflare R2（百炼 ASR 文件中转用）
CLOUDFLARE_R2_ACCOUNT_ID=your_account_id
CLOUDFLARE_R2_BUCKET=your_bucket
CLOUDFLARE_R2_ACCESS_KEY_ID=your_access_key
CLOUDFLARE_R2_SECRET_ACCESS_KEY=your_secret_key
CLOUDFLARE_R2_PUBLIC_BASE_URL=https://your-domain.com
```

### 3. 运行

```bash
# Web 模式
python server.py

# 桌面模式
python app.py
```

## 项目结构

```text
app.py                          # 桌面入口 (pywebview)
server.py                       # FastAPI 应用主文件
summarize.py                    # AI 摘要生成管线
routes/
  asr.py                        # ASR 路由入口
  whisper.py                    # Whisper/百炼 ASR 统一调度
  bailian_asr.py                # 阿里云百炼 ASR 客户端
  bilibili_downloader.py        # Bilibili 视频并发下载器
  r2_storage.py                 # Cloudflare R2 存储辅助
  telegram_bot.py               # Telegram Bot 集成
  deps.py                       # 核心依赖：任务管理、文件夹、日志
  favorites.py                  # 收藏夹相关路由
  settings.py                   # 设置管理路由
static/
  index.html                    # 前端入口
  app.js                        # 前端逻辑
  style.css                     # 样式表
  vendor/                       # 第三方库 (marked.js, DOMPurify)
tests/                          # 单元测试
docs/                           # 文档
```

## Telegram 使用

发送包含 BV 号的 Bilibili 链接到 Bot：

```text
https://www.bilibili.com/video/BV1xx411c7mD
https://www.bilibili.com/video/BV1yy411c7mD
```

支持 `/start` 和 `/help` 命令查看使用说明。发送 `/help` 可获取当前 Telegram 用户 ID，用于配置访问白名单。

## 文档

- 设计系统: `docs/design-system.md`
- 项目状态: `docs/project-status.md`

## 许可证

MIT
