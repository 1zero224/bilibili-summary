#!/usr/bin/env python3
"""
Bilibili 视频总结器

用法:
  python summarize.py                     # 总结 config.toml 中的视频 URL
  python summarize.py --user UID --count N  # 总结某 UP主 最新 N 个视频
"""

import argparse
import asyncio
import re
import os
from datetime import datetime
from pathlib import Path

import toml
from dotenv import load_dotenv
from bilibili_api import video, user, Credential
import anthropic


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


async def get_subtitle(v: video.Video) -> tuple[str, list]:
    """获取视频字幕内容，返回 (纯文本, 原始字幕数据)"""
    try:
        # 首先获取视频分P信息以获取 cid
        pages = await v.get_pages()
        if not pages:
            print(f"  ⚠️ 无法获取视频分P信息")
            return "", []
        
        # 使用第一个分P的 cid
        cid = pages[0].get('cid')
        if not cid:
            print(f"  ⚠️ 无法获取 cid")
            return "", []
        
        # 获取字幕列表
        player_info = await v.get_player_info(cid=cid)
        subtitle_info = player_info.get('subtitle', {})
        
        if not subtitle_info or not subtitle_info.get('subtitles'):
            print(f"  ⚠️ 视频没有字幕")
            return "", []
        
        # 获取第一个字幕（通常是 AI 生成的中文字幕）
        subtitle_list = subtitle_info['subtitles']
        subtitle_url = None
        
        # 优先选择中文字幕
        for sub in subtitle_list:
            if 'zh' in sub.get('lan', '').lower():
                subtitle_url = sub.get('subtitle_url', '')
                break
        
        # 如果没有中文字幕，使用第一个
        if not subtitle_url and subtitle_list:
            subtitle_url = subtitle_list[0].get('subtitle_url', '')
        
        if not subtitle_url:
            return "", []
        
        # 确保 URL 包含协议
        if subtitle_url.startswith('//'):
            subtitle_url = 'https:' + subtitle_url
        
        # 下载字幕内容
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(subtitle_url) as resp:
                subtitle_data = await resp.json()
        
        # 提取字幕文本
        if 'body' in subtitle_data:
            raw_subtitles = subtitle_data['body']
            texts = [item.get('content', '') for item in raw_subtitles]
            return '\n'.join(texts), raw_subtitles
        
        return "", []
    
    except Exception as e:
        print(f"  ⚠️ 获取字幕失败: {e}")
        return "", []


def format_ass_time(seconds: float) -> str:
    """将秒数转换为 ASS 时间格式 (H:MM:SS.CC)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def save_ass(title: str, subtitles: list):
    """保存字幕为 ASS 文件"""
    if not subtitles:
        return
    
    # 创建 ass 目录
    ass_dir = Path("ass")
    ass_dir.mkdir(exist_ok=True)
    
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


def summarize_with_claude(subtitle: str, title: str, client: anthropic.Anthropic) -> str:
    """使用 Claude API 生成视频总结"""
    if not subtitle:
        return "⚠️ 无法获取字幕，无法生成总结"
    
    prompt = f"""请根据以下视频字幕内容，生成一份简洁的视频总结。

视频标题: {title}

字幕内容:
{subtitle[:15000]}  # 限制字幕长度

请用中文输出总结，格式要求：
1. 用 3-5 个要点概括视频主要内容
2. 每个要点简洁明了
3. 如果有重要结论或关键信息，请特别指出
"""

    try:
        message = client.messages.create(
            model="GLM-4.7-Flash",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return message.content[0].text
    except Exception as e:
        return f"⚠️ 生成总结失败: {e}"


def save_summary(title: str, bvid: str, url: str, duration: int, summary: str):
    """保存总结到 markdown 文件"""
    # 创建 summary 目录
    summary_dir = Path("summary")
    summary_dir.mkdir(exist_ok=True)
    
    # 生成安全的文件名
    safe_title = sanitize_filename(title)
    filepath = summary_dir / f"{safe_title}.md"
    
    # 格式化时长
    minutes, seconds = divmod(duration, 60)
    duration_str = f"{minutes:02d}:{seconds:02d}"
    
    # 生成 markdown 内容
    content = f"""# {title}

**BV号**: {bvid}
**视频链接**: https://www.bilibili.com/video/{bvid}
**时长**: {duration_str}
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 📝 摘要

{summary}
"""
    
    filepath.write_text(content, encoding='utf-8')
    print(f"  ✅ 已保存: {filepath}")


async def process_video(url: str, client: anthropic.Anthropic, credential: Credential = None):
    """处理单个视频"""
    try:
        # 提取 BV 号
        bvid = extract_bvid(url)
        print(f"\n🎬 处理视频: {bvid}")
        
        # 创建 Video 对象（带登录凭证以获取字幕）
        v = video.Video(bvid=bvid, credential=credential)
        
        # 获取视频信息
        info = await v.get_info()
        title = info.get('title', bvid)
        duration = info.get('duration', 0)
        print(f"  📌 标题: {title}")
        
        # 获取字幕
        print(f"  📝 获取字幕...")
        subtitle_text, subtitle_raw = await get_subtitle(v)
        
        # 保存 ASS 字幕文件
        save_ass(title, subtitle_raw)
        
        # 生成总结
        print(f"  🤖 生成总结...")
        summary = summarize_with_claude(subtitle_text, title, client)
        
        # 保存
        save_summary(title, bvid, url, duration, summary)
        
    except Exception as e:
        print(f"  ❌ 处理失败: {e}")


async def process_by_bvid(bvid: str, client: anthropic.Anthropic, credential: Credential = None):
    """通过 BV 号处理视频"""
    url = f"https://www.bilibili.com/video/{bvid}"
    await process_video(url, client, credential)


async def get_user_videos(uid: int, count: int, credential: Credential = None) -> list:
    """获取 UP主 最新的 N 个视频"""
    u = user.User(uid=uid, credential=credential)
    
    # 获取用户信息
    try:
        user_info = await u.get_user_info()
        print(f"👤 UP主: {user_info.get('name', uid)}")
    except Exception as e:
        print(f"⚠️ 无法获取用户信息: {e}")
    
    # 获取视频列表
    videos_data = await u.get_videos(ps=count, pn=1)
    video_list = videos_data.get('list', {}).get('vlist', [])
    
    return [v.get('bvid') for v in video_list if v.get('bvid')]


async def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Bilibili 视频总结器')
    parser.add_argument('--user', type=int, help='UP主 UID')
    parser.add_argument('--count', type=int, default=5, help='总结视频数量 (默认 5)')
    args = parser.parse_args()
    
    # 加载环境变量
    load_dotenv('.env.local')
    
    # 初始化 Bilibili 登录凭证
    sessdata = os.getenv('BILIBILI_SESSION_TOKEN')
    bili_jct = os.getenv('BILIBILI_BILI_JCT')
    credential = None
    if sessdata:
        credential = Credential(sessdata=sessdata, bili_jct=bili_jct)
        print("✅ 已加载 Bilibili 登录凭证")
    else:
        print("⚠️ 未配置 BILIBILI_SESSION_TOKEN，可能无法获取字幕")
    
    # 初始化 Anthropic 客户端
    client = anthropic.Anthropic(
        base_url=os.getenv('ANTHROPIC_BASE_URL'),
        api_key=os.getenv('ANTHROPIC_AUTH_TOKEN')
    )
    
    # 根据模式处理
    if args.user:
        # 模式二: 总结某 UP主 的最新 N 个视频
        print(f"\n📹 获取 UP主 {args.user} 的最新 {args.count} 个视频...")
        bvids = await get_user_videos(args.user, args.count, credential)
        
        if not bvids:
            print("❌ 未找到视频")
            return
        
        print(f"📋 共有 {len(bvids)} 个视频需要总结")
        
        for bvid in bvids:
            await process_by_bvid(bvid, client, credential)
            await asyncio.sleep(1)
    else:
        # 模式一: 总结 config.toml 中的视频 URL
        config = toml.load("config.toml")
        urls = config.get("summary-videos", [])
        
        if not urls:
            print("❌ config.toml 中没有配置视频 URL")
            return
        
        print(f"📋 共有 {len(urls)} 个视频需要总结")
        
        unique_urls = list(dict.fromkeys(urls))
        for url in unique_urls:
            await process_video(url, client, credential)
            await asyncio.sleep(1)
    
    print("\n✨ 完成!")


if __name__ == "__main__":
    asyncio.run(main())
