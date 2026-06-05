"""剪映草稿导出。用 pyJianYingDraft 生成 draft，秒级渲染替代 240s 视频合成。

产出一个剪映可直接打开的草稿文件夹，用户导入后可二次编辑（调字幕/换音乐/改时长）。
轨道：图片轨（按时长对账）+ 音频轨 + 字幕轨（复用 align_subtitles）。
时间单位：微秒。
"""
from pathlib import Path
from app.modules.video_module import reconcile_durations, align_subtitles, get_audio_duration

SEC = 1_000_000  # 1 秒 = 1e6 微秒
WIDTH, HEIGHT = 1080, 1920
FPS = 30


def build_draft(image_paths: list[str], image_weights: list[float], audio_path: str,
                segments: list[dict], draft_dir: Path, draft_name: str = "draft",
                enable_subtitles: bool = True, enable_animations: bool = True) -> dict:
    """生成剪映草稿。返回 {draft_path, duration, dropped_images}。"""
    import pyJianYingDraft as draft
    from pyJianYingDraft import (ScriptFile, VideoMaterial, AudioMaterial,
                                 VideoSegment, AudioSegment, TextSegment,
                                 Timerange, TrackType)

    audio_total = get_audio_duration(audio_path)
    durs = reconcile_durations(image_weights, audio_total)
    dropped = max(0, len(image_paths) - len(durs))
    used_images = image_paths[:len(durs)]

    script = ScriptFile(WIDTH, HEIGHT, FPS, maintrack_adsorb=True)
    script.add_track(TrackType.video, "main_video")
    script.add_track(TrackType.audio, "main_audio")
    if enable_subtitles and segments:
        script.add_track(TrackType.text, "subtitle", relative_index=999)

    # 图片轨：按对账时长依次排布
    cursor = 0
    for img, d in zip(used_images, durs):
        dur_us = int(round(d * SEC))
        mat = VideoMaterial(img)
        seg = VideoSegment(mat, Timerange(cursor, dur_us))
        if enable_animations:
            _try_add_zoom(seg, draft)
        script.add_segment(seg, "main_video")
        cursor += dur_us

    # 音频轨：整段铺满
    audio_mat = AudioMaterial(audio_path)
    audio_seg = AudioSegment(audio_mat, Timerange(0, int(round(audio_total * SEC))))
    script.add_segment(audio_seg, "main_audio")

    # 字幕轨：复用字符比例对齐
    if enable_subtitles and segments:
        for s in align_subtitles(segments, audio_total):
            if not s["text"].strip():
                continue
            start_us = int(round(s["start"] * SEC))
            dur_us = max(1, int(round((s["end"] - s["start"]) * SEC)))
            tseg = TextSegment(s["text"], Timerange(start_us, dur_us))
            script.add_segment(tseg, "subtitle")

    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{draft_name}.json"
    script.dump(str(draft_path))
    return {"draft_path": str(draft_path), "duration": round(audio_total, 2),
            "dropped_images": dropped}


def _try_add_zoom(seg, draft):
    """尝试给图片片段加一个轻微放大入场动画，失败则跳过（不阻断）。"""
    try:
        from pyJianYingDraft import IntroType
        seg.add_animation(IntroType.放大)
    except Exception:
        pass
