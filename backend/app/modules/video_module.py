"""模块 G：视频合成。FFmpeg/MoviePy + 字幕对齐（PRD 4.8）+ 时长对账（PRD 9.5）。

字幕用 PIL 渲染为 PNG 再叠加，避免 moviepy TextClip 对 ImageMagick 的依赖（Windows 友好）。
"""
import os
from pathlib import Path

# 配置 ffmpeg 二进制：无系统 ffmpeg 时回退到 imageio-ffmpeg 自带的二进制。
try:
    import imageio_ffmpeg
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())
except Exception:
    pass

# 兼容性补丁：moviepy 1.0.3 使用 Pillow 10+ 已移除的 Image.ANTIALIAS。
try:
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, "ANTIALIAS"):
        _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS
except Exception:
    pass

# 单张图片时长约束（PRD 9.5）
MIN_DUR = 2.0
MAX_DUR = 15.0
WIDTH, HEIGHT = 1080, 1920

# CJK 字体候选（按平台），用于字幕渲染。
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def reconcile_durations(weights: list[float], audio_total: float) -> list[float]:
    """以音频总时长为唯一基准，按权重分配图片时长，并施加 [2,15] 约束。

    返回的时长列表之和严格等于 audio_total（PRD 9.5 对账保证）。
    若图片过多导致不足下限，截断丢弃尾部并由调用方记录。
    """
    n = len(weights)
    if n == 0 or audio_total <= 0:
        return []
    total_w = sum(weights) or 1.0
    durs = [audio_total * (w / total_w) for w in weights]

    # 施加上下限并迭代再分配差额
    for _ in range(10):
        clamped = [min(MAX_DUR, max(MIN_DUR, d)) for d in durs]
        diff = audio_total - sum(clamped)
        if abs(diff) < 0.01:
            durs = clamped
            break
        # 把差额分给未触界的图片
        free = [i for i, d in enumerate(durs)
                if (diff > 0 and clamped[i] < MAX_DUR) or (diff < 0 and clamped[i] > MIN_DUR)]
        if not free:
            durs = clamped
            break
        share = diff / len(free)
        for i in free:
            durs[i] = clamped[i] + share
    else:
        durs = [min(MAX_DUR, max(MIN_DUR, d)) for d in durs]

    # 最终强制对齐：把残差并入最后一张
    residual = audio_total - sum(durs)
    if durs:
        durs[-1] += residual
    return durs


def align_subtitles(segments: list[dict], audio_total: float,
                    seg_durations: list[float] | None = None) -> list[dict]:
    """分配字幕时间，返回 [{text,start,end}]。

    seg_durations（各段真实音频时长）可用且与段数匹配时，按真实时长精确对齐
    （字幕与配音逐段对准）；否则回退按字符比例近似分配（PRD 4.8）。
    """
    texts = [s["text"] for s in segments]
    if seg_durations and len(seg_durations) == len(texts) and sum(seg_durations) > 0:
        out = []
        cum = 0.0
        for t, d in zip(texts, seg_durations):
            start = cum
            cum += d
            out.append({"text": t, "start": round(start, 3), "end": round(cum, 3)})
        return out
    total_chars = sum(len(t) for t in texts) or 1
    out = []
    cum = 0
    for t in texts:
        start = cum / total_chars * audio_total
        cum += len(t)
        end = cum / total_chars * audio_total
        out.append({"text": t, "start": round(start, 3), "end": round(end, 3)})
    return out


def get_audio_duration(audio_path: str) -> float:
    from moviepy.editor import AudioFileClip
    with AudioFileClip(audio_path) as a:
        return float(a.duration)


def _find_font(size: int):
    """加载 CJK 字体，找不到则用 PIL 默认字体。"""
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_subtitle_png(text: str, out_path: Path, font_size: int = 52) -> bool:
    """用 PIL 把字幕渲染成透明 PNG（带描边），避免依赖 ImageMagick。"""
    from PIL import Image, ImageDraw
    font = _find_font(font_size)
    box_w = int(WIDTH * 0.9)
    # 简单按宽度折行
    draw_tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        w = draw_tmp.textbbox((0, 0), test, font=font)[2]
        if w > box_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)

    line_h = font_size + 14
    img_h = line_h * len(lines) + 20
    img = Image.new("RGBA", (WIDTH, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = 10
    for line in lines:
        lw = draw.textbbox((0, 0), line, font=font)[2]
        x = (WIDTH - lw) // 2
        # 描边
        for dx in (-2, 0, 2):
            for dy in (-2, 0, 2):
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(out_path, "PNG")
    return True


def compose(image_paths: list[str], image_weights: list[float], audio_path: str,
            segments: list[dict], out_path: Path, enable_subtitles=True,
            enable_animations=True, bgm_path: str | None = None) -> dict:
    """合成视频。返回 {video_path, duration, dropped_images}。
    bgm_path 非空且存在时，作为背景音乐低音量循环混入（人声为主，BGM 衬底）。"""
    from moviepy.editor import (ImageClip, AudioFileClip, concatenate_videoclips,
                                CompositeVideoClip, CompositeAudioClip, afx)

    audio_total = get_audio_duration(audio_path)
    durs = reconcile_durations(image_weights, audio_total)
    dropped = max(0, len(image_paths) - len(durs))
    used_images = image_paths[:len(durs)]

    clips = []
    for img, d in zip(used_images, durs):
        clip = ImageClip(img).set_duration(d).resize((WIDTH, HEIGHT))
        if enable_animations:
            clip = clip.resize(lambda t: 1 + 0.04 * t)  # 轻微 Ken Burns 放大
            clip = clip.set_position("center")
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(audio_path)
    bgm = None
    if bgm_path and Path(bgm_path).exists():
        # BGM 衬底：降到 15% 音量，循环/截断到与人声等长，与人声混音。
        bgm = AudioFileClip(bgm_path).fx(afx.volumex, 0.15)
        bgm = afx.audio_loop(bgm, duration=audio_total)
        final_audio = CompositeAudioClip([audio, bgm])
    else:
        final_audio = audio
    video = video.set_audio(final_audio).set_duration(audio_total)

    if enable_subtitles and segments:
        subs = align_subtitles(segments, audio_total)
        sub_dir = out_path.parent / "subs"
        sub_dir.mkdir(parents=True, exist_ok=True)
        sub_clips = []
        for i, s in enumerate(subs):
            if not s["text"].strip():
                continue
            png = sub_dir / f"sub_{i}.png"
            try:
                _render_subtitle_png(s["text"], png)
                txt = (ImageClip(str(png))
                       .set_start(s["start"])
                       .set_duration(max(0.1, s["end"] - s["start"]))
                       .set_position(("center", HEIGHT - 360)))
                sub_clips.append(txt)
            except Exception:
                # 字体/渲染异常不阻断主流程
                continue
        if sub_clips:
            video = CompositeVideoClip([video, *sub_clips])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(str(out_path), fps=25, codec="libx264", audio_codec="aac",
                          verbose=False, logger=None)
    video.close()
    audio.close()
    return {"video_path": str(out_path), "duration": round(audio_total, 2),
            "dropped_images": dropped}
