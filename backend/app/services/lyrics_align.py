"""歌词对齐服务：把歌词文本与音频时长对齐，生成 seg_texts + seg_durations。

核心逻辑（原型版）：
- 按行拆分歌词
- 按每行字数占比分配音频总时长
- 返回分镜对齐所需的 seg_texts / seg_durations

后续可升级：
- 接入 ASR 时间戳做精确对齐
- 接入 forced-alignment（aeneas）做字级对齐
"""
import os
import re
import subprocess
from pathlib import Path


def _ffmpeg_exe() -> str:
    env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env and os.path.exists(env):
        return env
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _get_audio_duration(audio_path: str) -> float:
    """用 ffmpeg 解析音频时长（秒）。"""
    ff = _ffmpeg_exe()
    cmd = [ff, "-i", audio_path, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr or "")
    if m:
        hh, mm, ss = m.groups()
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


def _count_chars(text: str) -> int:
    """统计有效字数：中文字符 + 字母数字。标点/空格不计。"""
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def align_lyrics(lyrics: str, audio_path: str) -> tuple[list[str], list[float]]:
    """把歌词文本按行对齐到音频时长。

    Args:
        lyrics: 歌词文本，每句一行（允许空行，会自动过滤）
        audio_path: 本地音频文件路径

    Returns:
        (seg_texts, seg_durations) —— 可直接写入 T 模块产物供合成使用
    """
    audio_path = str(audio_path)
    total_duration = _get_audio_duration(audio_path)
    if total_duration <= 0:
        raise ValueError("无法获取音频时长")

    # 拆行并过滤空行
    lines = [ln.strip() for ln in lyrics.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        raise ValueError("歌词为空")

    # 按字数占比分配时长
    counts = [_count_chars(ln) for ln in lines]
    total_chars = sum(counts) or len(lines)

    raw_durations = []
    for cnt in counts:
        ratio = cnt / total_chars if total_chars > 0 else 1.0 / len(lines)
        raw_durations.append(total_duration * ratio)

    # 最后一句兜底：把累计误差全部吸收到最后一句，确保总时长严格等于音频时长
    seg_durations = raw_durations[:-1]
    seg_durations.append(total_duration - sum(seg_durations))
    # 防零长（极小值时给 0.3s 地板，剪映不接受零长片段）
    seg_durations = [max(0.3, d) for d in seg_durations]
    # 再次修正最后一句
    seg_durations[-1] = total_duration - sum(seg_durations[:-1])
    seg_durations[-1] = max(0.3, seg_durations[-1])

    return lines, seg_durations
