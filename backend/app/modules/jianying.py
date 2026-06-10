"""剪映草稿导出。用 pyJianYingDraft 生成 draft，秒级渲染替代 240s 视频合成。

两种落盘方式：
- jianying_dir 提供时：用 DraftFolder 写入用户本地剪映"草稿存放位置"，
  生成 draft_content.json + draft_meta_info.json，剪映重启即可打开编辑（已实测可用）。
- 否则：退回 storage 内的裸 json（仅供下载，剪映无法直接识别）。
轨道：图片轨（按时长对账）+ 音频轨 + 字幕轨（复用 align_subtitles）。时间单位：微秒。
"""
from pathlib import Path
from app.modules.video_module import reconcile_durations, align_subtitles, get_audio_duration

SEC = 1_000_000  # 1 秒 = 1e6 微秒
WIDTH, HEIGHT = 1080, 1920
FPS = 30


def _populate(script, image_paths, image_weights, audio_path, segments,
              enable_subtitles, enable_animations, template="classic", seed=0):
    """把图片/音频/字幕三轨填进 script。返回 (duration_sec, dropped_images)。

    template/seed 控制动画模板：每镜头按种子从模板入场池选动画、相邻加转场、
    字幕套模板样式。enable_animations=False 时不加任何视觉动效（等价 none 模板）。
    """
    import pyJianYingDraft as draft
    from pyJianYingDraft import (VideoMaterial, AudioMaterial, VideoSegment,
                                 AudioSegment, TextSegment, Timerange, TrackType)
    from app.modules import draft_templates as tpl

    tmpl = template if enable_animations else "none"

    # 以剪映库自己解码出的素材时长为唯一基准（微秒），避免与 moviepy 读数
    # 存在毫秒级偏差导致"片段范围超出素材"。
    audio_mat = AudioMaterial(audio_path)
    audio_us = int(audio_mat.duration)
    audio_total = audio_us / SEC
    durs = reconcile_durations(image_weights, audio_total)
    dropped = max(0, len(image_paths) - len(durs))
    used_images = image_paths[:len(durs)]

    script.add_track(TrackType.video, "main_video")
    script.add_track(TrackType.audio, "main_audio")
    if enable_subtitles and segments:
        script.add_track(TrackType.text, "subtitle", relative_index=999)

    # 图片轨：按对账时长依次排布。最后一张兜底贴齐音频末尾，吸收累加误差。
    # 动画/转场按模板逐镜头选（种子确定→可复现）；取不到的枚举名跳过不阻断。
    cursor = 0
    last = len(used_images) - 1
    for idx, (img, d) in enumerate(zip(used_images, durs)):
        dur_us = max(1, audio_us - cursor) if idx == last else int(round(d * SEC))
        seg = VideoSegment(VideoMaterial(img), Timerange(cursor, dur_us))
        _apply_intro(seg, draft, tpl.pick_intro(tmpl, seed, idx))
        # 转场加在「前一段」尾部衔接下一段，故除首段外按上一镜头的转场设置
        if idx > 0:
            _apply_transition(seg, draft, tpl.pick_transition(tmpl, seed, idx - 1))
        script.add_segment(seg, "main_video")
        cursor += dur_us

    # 音频轨：用素材真实时长铺满，不超界
    script.add_segment(AudioSegment(audio_mat, Timerange(0, audio_us)), "main_audio")

    # 字幕轨：复用字符比例对齐。样式按模板（白字黑描边等），无样式则剪映默认。
    if enable_subtitles and segments:
        style, border = _subtitle_style(draft, tpl.subtitle_style(tmpl))
        for s in align_subtitles(segments, audio_total):
            if not s["text"].strip():
                continue
            st = int(round(s["start"] * SEC))
            du = max(1, int(round((s["end"] - s["start"]) * SEC)))
            kw = {}
            if style is not None:
                kw["style"] = style
            if border is not None:
                kw["border"] = border
            script.add_segment(TextSegment(s["text"], Timerange(st, du), **kw), "subtitle")

    return round(audio_total, 2), dropped


def build_draft(image_paths: list[str], image_weights: list[float], audio_path: str,
                segments: list[dict], draft_dir: Path, draft_name: str = "draft",
                enable_subtitles: bool = True, enable_animations: bool = True,
                jianying_dir: str | None = None,
                template: str = "classic", seed: int = 0) -> dict:
    """生成剪映草稿。返回 {draft_path, duration, dropped_images, in_jianying}。

    jianying_dir 提供时写入用户剪映草稿目录（DraftFolder，剪映可直接打开）；
    否则退回 storage 内裸 json（仅供下载）。
    template/seed 选动画模板并保证草稿可复现。
    """
    if jianying_dir:
        return _build_into_jianying(image_paths, image_weights, audio_path, segments,
                                    jianying_dir, draft_name, enable_subtitles,
                                    enable_animations, template, seed)
    return _build_bare_json(image_paths, image_weights, audio_path, segments,
                            draft_dir, draft_name, enable_subtitles, enable_animations,
                            template, seed)


def _build_into_jianying(image_paths, image_weights, audio_path, segments,
                         jianying_dir, draft_name, enable_subtitles, enable_animations,
                         template="classic", seed=0):
    """用 DraftFolder 写入剪映草稿目录，生成完整结构（实测剪映可打开）。"""
    from pyJianYingDraft import DraftFolder
    folder = DraftFolder(jianying_dir)
    if folder.has_draft(draft_name):
        _remove_draft(Path(jianying_dir) / draft_name, draft_name, folder)
    script = folder.create_draft(draft_name, WIDTH, HEIGHT, FPS, allow_replace=True)
    duration, dropped = _populate(script, image_paths, image_weights, audio_path,
                                  segments, enable_subtitles, enable_animations,
                                  template, seed)
    script.save()
    # 写封面缩略图，否则剪映草稿列表显示黑图 + 00:00（数据完整，仅列表预览缺失）。
    _write_cover(Path(jianying_dir) / draft_name, image_paths)
    return {"draft_path": str(Path(jianying_dir) / draft_name), "duration": duration,
            "dropped_images": dropped, "in_jianying": True}


def _remove_draft(draft_path: Path, draft_name: str, folder):
    """删旧草稿。剪映正开着该草稿时会锁住文件（WinError 32），
    给出可读提示而非抛底层错误。"""
    import shutil
    try:
        shutil.rmtree(draft_path)
    except PermissionError:
        raise RuntimeError(
            f"草稿「{draft_name}」正在剪映中打开，无法覆盖。请先在剪映里关闭该草稿后重试。")
    except FileNotFoundError:
        pass


def _write_cover(draft_path: Path, image_paths: list[str]):
    """用首图生成 draft_cover.jpg（剪映草稿列表封面）。失败不阻断。"""
    if not image_paths:
        return
    try:
        from PIL import Image
        src = next((p for p in image_paths if Path(p).exists()), None)
        if not src:
            return
        img = Image.open(src).convert("RGB")
        img.thumbnail((WIDTH // 2, HEIGHT // 2))
        img.save(draft_path / "draft_cover.jpg", "JPEG", quality=85)
    except Exception:
        pass


def _build_bare_json(image_paths, image_weights, audio_path, segments,
                     draft_dir, draft_name, enable_subtitles, enable_animations,
                     template="classic", seed=0):
    """退回方案：storage 内裸 draft_content.json（仅供下载，剪映不直接识别）。"""
    from pyJianYingDraft import ScriptFile
    script = ScriptFile(WIDTH, HEIGHT, FPS, maintrack_adsorb=True)
    duration, dropped = _populate(script, image_paths, image_weights, audio_path,
                                  segments, enable_subtitles, enable_animations,
                                  template, seed)
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{draft_name}.json"
    script.dump(str(draft_path))
    return {"draft_path": str(draft_path), "duration": duration,
            "dropped_images": dropped, "in_jianying": False}


def _apply_intro(seg, draft, name):
    """给片段加入场动画（按枚举名）。名为空或库里无此枚举则跳过，不阻断。"""
    if not name:
        return
    try:
        anim = getattr(draft.IntroType, name, None)
        if anim is not None:
            seg.add_animation(anim)
    except Exception:
        pass


def _apply_transition(seg, draft, name):
    """给片段加转场（按枚举名）。名为空或库里无此枚举则跳过，不阻断。"""
    if not name:
        return
    try:
        tr = getattr(draft.TransitionType, name, None)
        if tr is not None:
            seg.add_transition(tr)
    except Exception:
        pass


def _subtitle_style(draft, conf):
    """模板字幕样式 dict → (TextStyle|None, TextBorder|None)。conf 为空返回 (None,None)。"""
    if not conf:
        return None, None
    style = border = None
    try:
        skw = {}
        if "size" in conf:
            skw["size"] = float(conf["size"])
        if "color" in conf:
            skw["color"] = tuple(conf["color"])
        if conf.get("bold"):
            skw["bold"] = True
        # 字幕默认居中更像竖屏短视频
        skw["align"] = conf.get("align", 1)
        style = draft.TextStyle(**skw)
    except Exception:
        style = None
    try:
        if "border" in conf:
            border = draft.TextBorder(color=tuple(conf["border"]))
    except Exception:
        border = None
    return style, border
