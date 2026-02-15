# BiliSummary — Bilibili 视频总结器

一款基于 AI 的 Bilibili 视频内容总结工具，支持自动获取字幕并生成结构化 Markdown 摘要。

## ✨ 功能

- **URL 模式** — 粘贴视频链接，批量生成总结
- **UP 主模式** — 输入 UP 主名字或 UID，自动拉取视频并总结
- **收藏夹浏览** — 扫码登录后自动加载收藏夹，一键批量总结
- **语音识别 (ASR)** — 无字幕视频可通过 GLM-ASR 语音识别获取内容
- **macOS 原生窗口** — 基于 pywebview，无需浏览器

## 📦 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI + Uvicorn |
| 前端 | Vanilla JS + CSS |
| 桌面 | pywebview |
| AI | Anthropic Claude API (兼容接口) |
| ASR | GLM-ASR-2512 (智谱 AI) |
| B 站 | bilibili-api-python |
| 音频 | PyAV |

## 🚀 快速开始

### 1. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 API

创建 `.env.local` 文件：

```env
ANTHROPIC_AUTH_TOKEN=your_api_key
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic  # 或其他兼容接口
```

### 3. 运行

```bash
python app.py
```

应用将在原生窗口中打开，首次使用需扫码登录 Bilibili。

## 📁 项目结构

```
├── app.py              # 桌面应用入口 (pywebview)
├── server.py           # FastAPI 服务器
├── summarize.py        # AI 总结逻辑
├── routes/             # API 路由模块
│   ├── asr.py          # 语音识别总结
│   ├── auth.py         # B 站认证 (扫码登录)
│   ├── favorites.py    # 收藏夹管理
│   ├── settings.py     # 设置管理
│   └── deps.py         # 共享依赖
├── static/             # 前端资源
│   ├── index.html
│   ├── app.js
│   └── style.css
├── summary/            # 生成的总结 (gitignored)
└── requirements.txt
```

## 📄 License

MIT
