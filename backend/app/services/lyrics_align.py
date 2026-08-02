"""歌词对齐服务：把歌词文本与音频时长对齐，生成 seg_texts + seg_durations。

核心逻辑（原型版）：
- 按行拆分歌词
- 按每行字数占比分配音频总时长
- 保留空行作为段落分隔（空行会生成一个短暂停，标记段落边界）
- 返回分镜对齐所需的 seg_texts / seg_durations 以及 paragraph_breaks

后续可升级：
- 接入 ASR 时间戳做精确对齐
- 接入 forced-alignment（aeneas）做字级对齐
"""
import os
import re
import sys
import subprocess

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# 段落分隔（空行）对应的暂停时长（秒）
_PARAGRAPH_PAUSE = 0.5


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
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr or "")
    if m:
        hh, mm, ss = m.groups()
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


def _count_chars(text: str) -> int:
    """统计有效字数：中文字符 + 字母数字。标点/空格不计。"""
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def align_lyrics(lyrics: str, audio_path: str) -> tuple[list[str], list[float], list[int]]:
    """把歌词文本按行对齐到音频时长，保留段落结构（空行）。

    Args:
        lyrics: 歌词文本，每句一行；空行表示段落分隔
        audio_path: 本地音频文件路径

    Returns:
        (seg_texts, seg_durations, paragraph_breaks)
        - seg_texts: 分段文本列表，空行保留为空字符串 ""
        - seg_durations: 每段时长（秒），空行段落给 _PARAGRAPH_PAUSE 暂停
        - paragraph_breaks: 段落起始位置的索引列表（每段第一句的下标）
    """
    audio_path = str(audio_path)
    total_duration = _get_audio_duration(audio_path)
    if total_duration <= 0:
        raise ValueError("无法获取音频时长")

    # 拆行（保留空行作为段落分隔标记）
    lines = [ln.strip() for ln in lyrics.splitlines()]

    # 收集非空行用于时长计算
    non_empty_lines = [ln for ln in lines if ln]
    if not non_empty_lines:
        raise ValueError("歌词为空")

    # 统计段落数（连续空行算一个段落分隔）
    paragraph_count = 0
    prev_empty = True  # 开头如果是空行不算段落
    for ln in lines:
        if not ln:
            if not prev_empty:
                paragraph_count += 1
            prev_empty = True
        else:
            prev_empty = False

    # 段落暂停占用的总时长
    total_paragraph_pause = paragraph_count * _PARAGRAPH_PAUSE
    # 极短音频 + 段落很多时，暂停总时长可能逼近甚至超过总时长——不能再 floor 到与
    # total_duration 脱节的固定常数(1.0s)，那会让分配给歌词行的时长凭空多出一截，
    # 后面只对最后一行做误差修正又受 0.3s 地板限制吸收不完，导致总时长跑出音频实际长度。
    # 改为按音频总长本身留一个比例下限，floor 随音频长度一起缩放。
    if total_paragraph_pause > total_duration * 0.9:
        total_paragraph_pause = total_duration * 0.9
    available_duration = max(total_duration * 0.1, total_duration - total_paragraph_pause)

    # 计算每个非空行的字数占比
    non_empty_counts = [_count_chars(ln) for ln in non_empty_lines]
    total_chars = sum(non_empty_counts) or len(non_empty_lines)

    # 为每个非空行分配时长
    line_durations = {}
    for idx, cnt in enumerate(non_empty_counts):
        ratio = cnt / total_chars if total_chars > 0 else 1.0 / len(non_empty_lines)
        line_durations[idx] = available_duration * ratio

    # 组装最终输出：遍历原始 lines，空行给暂停时长，非空行给计算的时长
    seg_texts = []
    seg_durations = []
    non_empty_idx = 0

    for ln in lines:
        if ln:
            # 非空行
            dur = line_durations[non_empty_idx]
            seg_texts.append(ln)
            seg_durations.append(dur)
            non_empty_idx += 1
        else:
            # 空行：如果前一行也是空行则跳过（避免连续空行产生多个暂停）
            if seg_texts and seg_texts[-1]:
                # 空行作为段落分隔
                seg_texts.append("")  # 空字符串标记段落分隔
                seg_durations.append(_PARAGRAPH_PAUSE)

    # 修正最后一个非空行的时长，吸收累计误差
    last_non_empty = -1
    for i in range(len(seg_texts) - 1, -1, -1):
        if seg_texts[i]:
            last_non_empty = i
            break

    if last_non_empty >= 0:
        # 实际总时长应该等于 total_duration
        current_total = sum(seg_durations)
        diff = total_duration - current_total
        # 把误差加到最后一行
        seg_durations[last_non_empty] = max(0.3, seg_durations[last_non_empty] + diff)

    # 防零长（极小值时给 0.3s 地板，剪映不接受零长片段）
    for i in range(len(seg_durations)):
        if seg_texts[i]:  # 非空行才做地板保护
            seg_durations[i] = max(0.3, seg_durations[i])

    # 重新收集段落起始位置（基于最终的 seg_texts）
    paragraph_breaks = []
    for i, text in enumerate(seg_texts):
        if text and (not paragraph_breaks or (i > 0 and not seg_texts[i - 1])):
            # 当前是非空行，且是开头或前一个是空行（段落分隔）
            paragraph_breaks.append(i)

    return seg_texts, seg_durations, paragraph_breaks
