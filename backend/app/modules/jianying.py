"""剪映草稿导出。用 pyJianYingDraft 生成 draft，秒级渲染替代 240s 视频合成。

两种落盘方式：
- jianying_dir 提供时：用 DraftFolder 写入用户本地剪映"草稿存放位置"，
  生成 draft_content.json + draft_meta_info.json，剪映重启即可打开编辑（已实测可用）。
- 否则：退回 storage 内的裸 json（仅供下载，剪映无法直接识别）。
轨道：图片轨（按时长对账）+ 音频轨 + 字幕轨（复用 align_subtitles）。时间单位：微秒。
"""
from pathlib import Path
import re
from app.modules.video_module import reconcile_durations, align_subtitles, get_audio_duration

SEC = 1_000_000  # 1 秒 = 1e6 微秒
WIDTH, HEIGHT = 1080, 1920
FPS = 30


def _populate(script, image_paths, image_weights, audio_path, segments,
              enable_subtitles, enable_animations, template="classic", seed=0,
              seg_durations=None):
    """把图片/音频/字幕三轨填进 script。返回 (duration_sec, dropped_images)。

    template/seed 控制动画模板：每镜头按种子从模板入场池选动画、相邻加转场、
    字幕套模板样式。enable_animations=False 时不加任何视觉动效（等价 none 模板）。

    seg_durations 非空时启用【分镜对齐模式】：图片、字幕、配音三轨共用这一套分镜时长
    （seg_durations[i] = 第 i 个分镜的真实配音时长，长度 == 图数 == 字幕段数），
    第 i 张图的时间区间 == 第 i 段字幕区间 == 第 i 段配音 → 图文音严格对齐。
    为空时回退老逻辑（图片按权重对账、字幕按各段配音时长另算）。
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

    # 分镜对齐模式：图数 == 字幕段数 == len(seg_durations)，三轨共用分镜时长。
    aligned = bool(seg_durations) and len(seg_durations) == len(image_paths) and len(image_paths) > 0

    # 本应对齐（传了 seg_durations）却因数量不等没对上：打警告，便于发现对齐回归，
    # 不会静默退回错位逻辑而无人知晓。
    if seg_durations and not aligned:
        import logging
        logging.getLogger("uvicorn").warning(
            "草稿对齐降级：seg_durations 段数(%d) != 图数(%d)，退回按权重对账（图文可能错位）",
            len(seg_durations), len(image_paths))

    if aligned:
        # 图片每张时长 = 该分镜配音时长（按音频总长归一化吸收累计误差）。不循环复用、不丢图。
        # 给每段设 0.3s 地板，防纯标点段 0 时长产出零长片段（剪映会报 SegmentOverlap）。
        floored = [max(0.3, float(d)) for d in seg_durations]
        durs = _scale_to_audio(floored, audio_total)
        used_images = image_paths
        dropped = 0
    else:
        # 老逻辑：图片太少铺不满音频时循环复用，避免最后一张被拉成几百秒定格。
        image_paths, image_weights = _expand_images_to_fill(
            image_paths, image_weights, audio_total)
        durs = reconcile_durations(image_weights, audio_total)
        dropped = max(0, len(image_paths) - len(durs))
        used_images = image_paths[:len(durs)]

    script.add_track(TrackType.video, "main_video")
    script.add_track(TrackType.audio, "main_audio")
    if enable_subtitles and segments:
        script.add_track(TrackType.text, "subtitle", relative_index=999)

    # 图片轨：按时长依次排布。最后一张兜底贴齐音频末尾，吸收累加误差。
    # 动画/转场按模板逐镜头选（种子确定→可复现）；取不到的枚举名跳过不阻断。
    cursor = 0
    last = len(used_images) - 1
    img_starts = []  # 记录每张图的 (start_us, end_us)，分镜对齐时字幕轨直接复用
    for idx, (img, d) in enumerate(zip(used_images, durs)):
        dur_us = max(1, audio_us - cursor) if idx == last else int(round(d * SEC))
        img_starts.append((cursor, cursor + dur_us))
        seg = VideoSegment(VideoMaterial(img), Timerange(cursor, dur_us))
        _apply_intro(seg, draft, tpl.pick_intro(tmpl, seed, idx))
        # 转场加在「前一段」尾部衔接下一段，故除首段外按上一镜头的转场设置
        if idx > 0:
            _apply_transition(seg, draft, tpl.pick_transition(tmpl, seed, idx - 1))
        script.add_segment(seg, "main_video")
        cursor += dur_us

    # 音频轨：用素材真实时长铺满，不超界
    script.add_segment(AudioSegment(audio_mat, Timerange(0, audio_us)), "main_audio")

    # 字幕轨
    if enable_subtitles and segments:
        import re as _re
        style, border, sub_extra = _subtitle_style(draft, tpl.subtitle_style(tmpl))
        if aligned:
            # 分镜对齐：第 i 段字幕直接用第 i 张图的时间区间（== 第 i 段配音）。
            # 每段文字按 ≤12 字切成多条短字幕，在该分镜区间内按字数比例平分。
            for idx, seg in enumerate(segments):
                if idx >= len(img_starts):
                    break
                text = (seg.get("text") or "").strip()
                if not text:
                    continue
                seg_start_us, seg_end_us = img_starts[idx]
                _emit_caption_chunks(script, text, seg_start_us, seg_end_us,
                                     style, border, sub_extra)
        else:
            # 老逻辑：字幕按各段配音真实时长对齐（与图片轨各算各的）。
            sub_segs = [s for s in segments
                        if s.get("text", "").strip() and _re.search(r"[\w一-鿿]", s["text"])]
            seg_durs = _read_seg_durations(audio_path, len(sub_segs))
            for s in align_subtitles(sub_segs, audio_total, seg_durs):
                if not s["text"].strip():
                    continue
                seg_start_us = int(round(s["start"] * SEC))
                seg_end_us = int(round(s["end"] * SEC))
                if seg_end_us <= seg_start_us:
                    seg_end_us = seg_start_us + 1
                _emit_caption_chunks(script, s["text"], seg_start_us, seg_end_us,
                                     style, border, sub_extra)

    return round(audio_total, 2), dropped


def _emit_caption_chunks(script, text, seg_start_us, seg_end_us, style, border, extra=None):
    """把一段字幕文字按 ≤12 字切成多条短字幕，在 [seg_start_us, seg_end_us] 区间内
    按字数比例平分。用整数微秒累进、下一条紧接上一条结束，避免取整后端点重叠
    （剪映字幕轨不允许重叠，会抛 SegmentOverlap）。
    extra: {font, position_y} —— 字体与垂直位置（统一默认，免得每次在剪映手动调）。"""
    from pyJianYingDraft import TextSegment, Timerange
    extra = extra or {}
    # 垂直位置：把 position_y 包成 ClipSettings(transform_y)。剪映 y 负=下移。
    clip = None
    if extra.get("position_y") is not None:
        try:
            from pyJianYingDraft import ClipSettings
            clip = ClipSettings(transform_y=float(extra["position_y"]))
        except Exception:
            clip = None
    font = extra.get("font")
    caps = _split_caption(text, max_chars=12)
    total_chars = sum(len(c) for c in caps) or 1
    span = seg_end_us - seg_start_us
    cum = 0
    cur = seg_start_us
    for ci, cap in enumerate(caps):
        cum += len(cap)
        nxt = seg_end_us if ci == len(caps) - 1 else seg_start_us + span * cum // total_chars
        du = max(1, nxt - cur)
        kw = {}
        if style is not None:
            kw["style"] = style
        if border is not None:
            kw["border"] = border
        if font is not None:
            kw["font"] = font
        if clip is not None:
            kw["clip_settings"] = clip
        script.add_segment(TextSegment(cap, Timerange(cur, du), **kw), "subtitle")
        cur = nxt


def _scale_to_audio(durs: list[float], audio_total: float) -> list[float]:
    """把分镜时长列表按比例缩放到音频总长，使总和严格等于 audio_total（吸收 TTS 拼接、
    去静音等导致的累计误差）。分镜时长本就来自各段真实配音，比例已基本正确，这里只做归一。"""
    s = sum(durs)
    if s <= 0 or audio_total <= 0:
        return durs
    k = audio_total / s
    return [d * k for d in durs]


def build_draft(image_paths: list[str], image_weights: list[float], audio_path: str,
                segments: list[dict], draft_dir: Path, draft_name: str = "draft",
                enable_subtitles: bool = True, enable_animations: bool = True,
                jianying_dir: str | None = None,
                template: str = "classic", seed: int = 0,
                seg_durations: list[float] | None = None) -> dict:
    """生成剪映草稿。返回 {draft_path, duration, dropped_images, in_jianying}。

    jianying_dir 提供时写入用户剪映草稿目录（DraftFolder，剪映可直接打开）；
    否则退回 storage 内裸 json（仅供下载）。
    template/seed 选动画模板并保证草稿可复现。
    seg_durations 非空时启用分镜对齐模式（图/字/音三轨共用分镜时长）。
    """
    if jianying_dir:
        return _build_into_jianying(image_paths, image_weights, audio_path, segments,
                                    jianying_dir, draft_name, enable_subtitles,
                                    enable_animations, template, seed, seg_durations)
    return _build_bare_json(image_paths, image_weights, audio_path, segments,
                            draft_dir, draft_name, enable_subtitles, enable_animations,
                            template, seed, seg_durations)


def _build_into_jianying(image_paths, image_weights, audio_path, segments,
                         jianying_dir, draft_name, enable_subtitles, enable_animations,
                         template="classic", seed=0, seg_durations=None):
    """用 DraftFolder 写入剪映草稿目录，生成完整结构（实测剪映可打开）。"""
    from pyJianYingDraft import DraftFolder
    folder = DraftFolder(jianying_dir)
    if folder.has_draft(draft_name):
        _remove_draft(Path(jianying_dir) / draft_name, draft_name, folder)
    script = folder.create_draft(draft_name, WIDTH, HEIGHT, FPS, allow_replace=True)
    duration, dropped = _populate(script, image_paths, image_weights, audio_path,
                                  segments, enable_subtitles, enable_animations,
                                  template, seed, seg_durations)
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
                     template="classic", seed=0, seg_durations=None):
    """退回方案：storage 内裸 draft_content.json（仅供下载，剪映不直接识别）。"""
    from pyJianYingDraft import ScriptFile
    script = ScriptFile(WIDTH, HEIGHT, FPS, maintrack_adsorb=True)
    duration, dropped = _populate(script, image_paths, image_weights, audio_path,
                                  segments, enable_subtitles, enable_animations,
                                  template, seed, seg_durations)
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{draft_name}.json"
    script.dump(str(draft_path))
    return {"draft_path": str(draft_path), "duration": duration,
            "dropped_images": dropped, "in_jianying": False}


def _expand_images_to_fill(image_paths, image_weights, audio_total):
    """图片按 15 秒上限仍铺不满音频时，循环复用图片列表使总容量 >= 音频时长。
    返回扩展后的 (paths, weights)。图片足够则原样返回。"""
    from app.modules.video_module import MAX_DUR
    n = len(image_paths)
    if n == 0:
        return image_paths, image_weights
    need = int(audio_total // MAX_DUR) + 1   # 至少需要这么多张才能铺满
    if n >= need:
        return image_paths, image_weights
    reps = (need + n - 1) // n               # 循环几轮
    paths = (list(image_paths) * reps)[:need]
    ws = list(image_weights) if image_weights else [1.0] * n
    weights = (ws * reps)[:need]
    return paths, weights


def _split_caption(text: str, max_chars: int = 12) -> list:
    """把一段字幕文字切成多条 ≤max_chars 的短字幕（一屏只显示几个字，像短视频）。
    优先在标点处断，标点间仍超长则按字数硬切。返回短句列表（去掉句末多余标点）。"""
    text = text.strip()
    if not text:
        return []
    # 先按标点切成自然小句（保留标点判断，但显示时去掉行尾标点更清爽）
    pieces = re.split(r"(?<=[，,。．.！!？?；;、：:])", text)
    caps, buf = [], ""
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) <= max_chars:
            buf += p
        else:
            if buf:
                caps.append(buf)
            while len(p) > max_chars:
                caps.append(p[:max_chars])
                p = p[max_chars:]
            buf = p
    if buf:
        caps.append(buf)
    # 去掉每条行尾的标点（短视频字幕惯例），保留内部
    return [c.rstrip("，,。．.！!？?；;、：: ") or c for c in caps]


def _read_seg_durations(audio_path: str, n: int):
    """从 audio.mp3 同目录读 seg_000.mp3.. 的真实时长（秒），供字幕精确对齐。
    数量与段数 n 不符或读取失败则返回 None（回退字符比例对齐）。"""
    try:
        from app.modules.video_module import get_audio_duration
        ad = Path(audio_path).parent
        segs = sorted(ad.glob("seg_*.mp3"))
        if len(segs) != n:
            return None
        durs = [round(get_audio_duration(str(p)), 3) for p in segs]
        return durs if sum(durs) > 0 else None
    except Exception:
        return None


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
    """模板字幕样式 dict → (TextStyle|None, TextBorder|None, extra)。conf 为空返回 (None,None,{})。
    extra 携带 font(FontType|None) 和 position_y(float|None)，由调用方构造 TextSegment 时用。"""
    if not conf:
        return None, None, {}
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
    # 字体：按中文枚举名取 FontType，缺字体/版本差异时回退默认（不阻断）。
    extra = {}
    fname = conf.get("font")
    if fname:
        try:
            extra["font"] = getattr(draft.FontType, fname)
        except Exception:
            extra["font"] = None
    if conf.get("position_y") is not None:
        extra["position_y"] = float(conf["position_y"])
    return style, border, extra