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
    # 图片太少铺不满音频时（n×15秒上限 < 音频），循环复用图片，避免最后一张被拉成
    # 几百秒的定格长镜头。扩展后每张仍在 [2,15] 秒内，画面持续切换。
    image_paths, image_weights = _expand_images_to_fill(
        image_paths, image_weights, audio_total)
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

    # 字幕轨：优先按各段配音的真实时长对齐（字幕与语音逐段对准），
    # 取不到分段时长时回退字符比例。样式按模板，无样式则剪映默认。
    # 注意：seg_xxx.mp3 只对应「可朗读段」（合成时已过滤纯标点碎片段），
    # 故字幕也必须用同样过滤后的段，否则段数与音频段对不上、错位。
    if enable_subtitles and segments:
        import re as _re
        sub_segs = [s for s in segments
                    if s.get("text", "").strip() and _re.search(r"[\w一-鿿]", s["text"])]
        seg_durs = _read_seg_durations(audio_path, len(sub_segs))
        style, border = _subtitle_style(draft, tpl.subtitle_style(tmpl))
        for s in align_subtitles(sub_segs, audio_total, seg_durs):
            if not s["text"].strip():
                continue
            seg_start = s["start"]
            seg_dur = max(0.001, s["end"] - s["start"])
            # 一屏只显示几个字：把整段文字按 ≤12 字切成多条短字幕，
            # 在该段时间区间内按字数比例平分时长，依次显示。
            caps = _split_caption(s["text"], max_chars=12)
            total_chars = sum(len(c) for c in caps) or 1
            cum = 0
            for cap in caps:
                cstart = seg_start + seg_dur * (cum / total_chars)
                cum += len(cap)
                cend = seg_start + seg_dur * (cum / total_chars)
                st = int(round(cstart * SEC))
                du = max(1, int(round((cend - cstart) * SEC)))
                kw = {}
                if style is not None:
                    kw["style"] = style
                if border is not None:
                    kw["border"] = border
                script.add_segment(TextSegment(cap, Timerange(st, du), **kw), "subtitle")

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
