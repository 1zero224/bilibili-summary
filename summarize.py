#!/usr/bin/env python3
"""
Bilibili 视频总结器：字幕 / AI 总结生成管线。

供 `/api/v2`、Telegram Bot 与后台任务调用；入口是 `server.py`。
"""

import asyncio
import os
import time
import re
import json
from datetime import datetime
from pathlib import Path

import anthropic

DATA_DIR = Path(os.environ.get('BILISUMMARY_DATA_DIR', os.path.dirname(os.path.abspath(__file__))))


def extract_bvid(url: str) -> str:
    """从 Bilibili URL 中提取 BV 号"""
    match = re.search(r'BV[a-zA-Z0-9]+', url)
    if match:
        return match.group(0)
    raise ValueError(f"无法从 URL 中提取 BV 号: {url}")


def sanitize_filename(title: str) -> str:
    """清理文件名中的非法字符"""
    # 替换 Windows/Mac/Linux 不允许的文件名字符
    return re.sub(r'[<>:"/\\|?*]', '_', title).strip()


def format_ass_time(seconds: float) -> str:
    """将秒数转换为 ASS 时间格式 (H:MM:SS.CC)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def format_summary_time(seconds: float) -> str:
    """将秒数转换为总结提示词使用的时间格式。"""
    total = max(0, int(seconds or 0))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_timestamped_transcript(segments: list[dict]) -> str:
    """将转录片段转换为带时间段的文本，供详细总结模型引用。"""
    lines = []
    for segment in segments or []:
        start = segment.get("from", segment.get("start", 0))
        end = segment.get("to", segment.get("end", start))
        text = (segment.get("content", segment.get("text", "")) or "").strip()
        if not text:
            continue
        lines.append(f"[{format_summary_time(start)}-{format_summary_time(end)}] {text}")
    return "\n".join(lines).strip()


def save_ass(title: str, subtitles: list, output_subdir: str = "standalone"):
    """保存字幕为 ASS 文件"""
    if not subtitles:
        return
    
    # 创建 ass 目录
    ass_dir = DATA_DIR / "ass" / output_subdir
    ass_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成安全的文件名
    safe_title = sanitize_filename(title)
    filepath = ass_dir / f"{safe_title}.ass"
    
    # ASS 文件头
    ass_header = """[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(title=title)
    
    # 生成字幕行
    lines = []
    for item in subtitles:
        start = format_ass_time(item.get('from', 0))
        end = format_ass_time(item.get('to', 0))
        content = item.get('content', '').replace('\n', '\\N')
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{content}")
    
    # 写入文件
    filepath.write_text(ass_header + '\n'.join(lines), encoding='utf-8')
    print(f"  📝 字幕已保存: {filepath}")


DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "mimo-v2.5-pro")


def _message_create_kwargs(client: anthropic.AsyncAnthropic, model: str, prompt: str) -> dict:
    kwargs = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    if "xiaomimimo.com" in str(getattr(client, "base_url", "")):
        kwargs["thinking"] = {"type": "disabled"}
    return kwargs


async def summarize_with_claude(subtitle: str, title: str, client: anthropic.AsyncAnthropic, model: str = None) -> tuple[str, float]:
    """使用 Claude API 生成视频总结，返回 (总结内容, 耗时秒数)"""
    model = model or DEFAULT_MODEL
    if not subtitle:
        return "⚠️ 无法获取转录文本，无法生成总结", 0.0
    
    prompt = f"""你是一个专业的视频内容分析师。请根据以下视频转录文本，生成一份**全面、精细且有条理**的视频笔记。

视频标题: {title}

转录文本:
{subtitle[:30000]}

请用中文输出，严格按照以下格式：

## 内容整理

将作者的原始表述进行整理和精简，去除口语化的重复、语气词和冗余表达，但**不能遗漏任何实质内容**。用更清晰流畅的书面语重新组织，保留作者的原意、论证逻辑和关键用词。按话题分段呈现。

## 核心观点

全面覆盖作者在视频中表达的所有重要观点，不要人为限制数量。每个观点下面：
- 先用一句话精准概括该观点
- 然后列出作者用来支撑该观点的**具体例子、故事、数据或类比**（如果有的话）

注意：观点数量应由内容决定，确保不遗漏任何重要论点。短视频可能只有 2-3 个观点，长视频可能有 10 个以上。

## 行动建议

如果视频包含可操作的建议或方法论，请列出具体的行动步骤。如果视频偏向于分享观点/故事而非方法论，可以省略此部分。
"""

    max_retries = 5
    base_wait = 2

    for attempt in range(max_retries):
        try:
            t_start = time.time()
            message = await client.messages.create(**_message_create_kwargs(client, model, prompt))
            t_end = time.time()
            duration = t_end - t_start
            text = _message_text(message)
            if not text:
                return "⚠️ 生成总结失败: 模型未返回文本内容", duration
            return text, duration
            
        except anthropic.RateLimitError as e:
            if attempt < max_retries - 1:
                wait_time = base_wait * (2 ** attempt)
                print(f"    ⚠️  API 速率限制 (429)，{wait_time}s 后重试... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
            else:
                return f"⚠️ 生成总结失败 (Rate Limit Exhausted): {e}", 0.0
                
        except anthropic.APIError as e:
             # Handle other API errors
             return f"⚠️ 生成总结失败 (API Error): {e}", 0.0
             
        except Exception as e:
            return f"⚠️ 生成总结失败: {e}", 0.0
            
    return "⚠️ 生成总结失败 (Unknown)", 0.0


def _message_text(message) -> str:
    parts = []
    for item in getattr(message, "content", []) or []:
        text = getattr(item, "text", "")
        if text:
            parts.append(text)
        elif isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(item["text"])
    return "\n".join(parts).strip()


async def generate_detailed_summary_with_claude(
    transcript: str,
    title: str,
    client: anthropic.AsyncAnthropic,
    model: str = None,
) -> tuple[str, float]:
    """生成详细总结，返回 (内容, 耗时秒数)。"""
    model = model or DEFAULT_MODEL
    if not transcript:
        return "⚠️ 无法获取转录文本，无法生成详细总结", 0.0

    prompt = f"""请基于以下视频转录文本生成一份详细总结。

视频标题: {title}

转录文本:
{transcript[:30000]}

要求：
1. 使用 Markdown 输出。
2. 只输出详细总结，不要输出简短摘要、核心观点列表或无时间信息的总结。
3. 必须按时间段整理内容，每个时间段使用一行二级标题，格式严格为：## [[时间段: 起始时间-结束时间]] 标题。
4. 起始时间和结束时间必须来自转录内容对应的视频时间，使用 M:SS 或 H:MM:SS 格式；示例：## [[时间段: 0:00-3:25]] 开场与问题背景。
5. 每个时间段下说明该段主要内容、关键观点、论证脉络和重要细节，不遗漏关键事实、数据、人物和结论。
6. 如果该时间段包含行动建议或决策依据，请放在对应时间段下，不要单独脱离时间段输出。
"""

    try:
        t_start = time.time()
        message = await client.messages.create(**_message_create_kwargs(client, model, prompt))
        return _message_text(message), time.time() - t_start
    except Exception as e:
        return f"⚠️ 生成详细总结失败: {e}", 0.0


def save_summary(
    title: str,
    bvid: str,
    url: str,
    duration: int,
    summary: str,
    output_subdir: str = "standalone",
    author_name: str = "",
    author_uid: int = 0,
    cover_url: str = "",
    media_path: str = "",
):
    """保存总结到 markdown 文件"""
    # 创建 summary 目录
    summary_dir = DATA_DIR / "summary" / output_subdir
    summary_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成安全的文件名
    safe_title = sanitize_filename(title)
    filepath = summary_dir / f"{safe_title}.md"
    
    # 格式化时长
    minutes, seconds = divmod(duration, 60)
    duration_str = f"{minutes:02d}:{seconds:02d}"
    
    # Author line
    author_line = ""
    if author_name and author_uid:
        author_line = f"**作者**: [{author_name}](https://space.bilibili.com/{author_uid})\n"
    elif author_name:
        author_line = f"**作者**: {author_name}\n"

    # 生成 markdown 内容
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if cover_url.startswith("//"):
        cover_url = "https:" + cover_url

    content = f"""# {title}

**BV号**: {bvid}
**视频链接**: {url or f"https://www.bilibili.com/video/{bvid}"}
{author_line}**时长**: {duration_str}
**生成时间**: {generated_at}

---

## 📝 摘要

{summary}
"""
    
    filepath.write_text(content, encoding='utf-8')
    meta_path = filepath.with_suffix(".meta.json")
    meta_payload = {
        "title": title,
        "bvid": bvid,
        "url": url,
        "duration": duration,
        "author_name": author_name,
        "author_uid": author_uid,
        "cover_url": cover_url,
        "media_path": media_path,
        "generated_at": generated_at,
    }
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ 已保存: {filepath}")
