"""模块 G 触发服务：用户上传音频后，组装图片+分段并产出成片。

两种输出模式：
- jianying（默认）：生成剪映草稿，秒级，用户可二次编辑
- mp4：直接合成视频，慢（约 4 分钟/30s），不可编辑
"""
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Task, ModuleResult, Asset, Config
from app.core.config import settings
from app.core.paths import storage_root
from app.modules import video_module as vm
from app.modules import jianying
import uuid


def _get_output(db: Session, task_id: str, module: str):
    mr = db.query(ModuleResult).filter_by(task_id=task_id, module=module, status="success").first()
    return mr.output if mr else None


def _load_assets(db: Session, task_id: str):
    """取 E、F、T 产物，返回 (image_paths, weights, segments, seg_durations)。
    字幕/配音分段【优先用 T 产物的分镜分段】(seg_texts，与图片同源 → 图-字-音三轨一一对齐)，
    并带上每个分镜的真实配音时长 seg_durations（让图片轨/字幕轨共用同一套分镜时间）。
    T 缺失或无分镜分段（老任务/手动上传音频）时回退 F 的 segments、seg_durations 为 None。"""
    e_out = _get_output(db, task_id, "E")
    f_out = _get_output(db, task_id, "F")
    if not e_out or not f_out:
        raise ValueError("配图或分段未完成，无法合成")
    images = e_out["images"]
    image_paths = [img["path"] for img in images]
    weights = [float(img.get("suggested_duration", 5)) for img in images]

    t_out = _get_output(db, task_id, "T")
    seg_texts = (t_out or {}).get("seg_texts") if t_out else None
    seg_durations = (t_out or {}).get("seg_durations") if t_out else None
    if seg_texts and (t_out or {}).get("seg_source") == "scene":
        # 分镜源：字幕分段=分镜 cap，与图片一一对应。
        segments = [{"text": t} for t in seg_texts]
    else:
        # 回退老路：F 的口播分段（手动上传音频、SB 失败等）。
        segments = f_out["segments"]
        seg_durations = None
    return image_paths, weights, segments, seg_durations


def compose_video(db: Session, task_id: str, audio_path: str,
                  enable_subtitles=True, enable_animations=True,
                  output_mode="jianying", bgm_path: str | None = None) -> dict:
    """产出成片。output_mode: jianying（草稿，默认）/ mp4（合成）。
    bgm_path 仅 mp4 合成时混音；剪映草稿模式由用户在剪映里加 BGM。"""
    task = db.get(Task, task_id)
    if not task:
        raise ValueError("任务不存在")

    image_paths, weights, segments, seg_durations = _load_assets(db, task_id)

    if output_mode == "jianying":
        return _compose_jianying(db, task, task_id, image_paths, weights, segments,
                                 audio_path, enable_subtitles, enable_animations, seg_durations)
    return _compose_mp4(db, task, task_id, image_paths, weights, segments,
                        audio_path, enable_subtitles, enable_animations, bgm_path)


def _finish(db, task, task_id, asset_type, file_path, mime, meta, output):
    size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
    db.add(Asset(id=f"asset_{uuid.uuid4().hex[:12]}", task_id=task_id, type=asset_type,
                 file_path=str(file_path), file_size=size, mime_type=mime,
                 meta={**meta, "size": size}))
    mr = db.query(ModuleResult).filter_by(task_id=task_id, module="G").first()
    if not mr:
        mr = ModuleResult(task_id=task_id, module="G")
        db.add(mr)
    mr.status = "success"
    mr.output = output
    task.status = "completed"
    db.commit()
    return {**output, "file_size": size}


def _compose_jianying(db, task, task_id, image_paths, weights, segments,
                      audio_path, subs, anim, seg_durations=None) -> dict:
    draft_dir = storage_root(db) / task_id / "jianying"
    cfg = db.get(Config, 1)
    jianying_dir = (cfg.jianying_draft_dir or "").strip() if cfg else ""
    # 草稿名可读化（抄竞品）：{标题}_{任务后缀}，剪映里一眼能找到。
    title = (task.title or "").strip()
    draft_name = f"{title}_{task_id[-6:]}" if title else task_id
    draft_name = _safe_name(draft_name)
    # 动画模板 + 任务派生种子（草稿可复现：同任务重生成动画序列不变）
    template = getattr(task, "draft_template", None) or "classic"
    seed = int(task_id[-6:], 36) if task_id else 0

    def _try_build(name):
        return jianying.build_draft(image_paths, weights, audio_path, segments, draft_dir,
                                    draft_name=name, enable_subtitles=subs,
                                    enable_animations=anim,
                                    jianying_dir=jianying_dir or None,
                                    template=template, seed=seed,
                                    seg_durations=seg_durations,
                                    aspect_ratio=getattr(task, "aspect_ratio", None))
    try:
        r = _try_build(draft_name)
    except Exception as e:
        # 剪映正开着同名草稿导致无法覆盖时，自动换带序号的新名重试（最多 3 次），
        # 避免用户必须先去关剪映；其它错误照常抛出。
        r = None
        msg = str(e)
        is_locked = ("剪映中打开" in msg or "无法覆盖" in msg or "PermissionError" in msg)
        if is_locked:
            for n in range(2, 5):
                try:
                    r = _try_build(_safe_name(f"{draft_name}_{n}"))
                    break
                except Exception as e2:
                    e = e2
                    continue
        if r is None:
            task.status = "failed"
            task.error_code = "E5001"
            task.error_message = f"剪映草稿生成失败: {e}"[:500]
            db.commit()
            raise
    return _finish(db, task, task_id, "jianying_draft", r["draft_path"],
                   "application/json",
                   {"duration": r["duration"], "dropped_images": r["dropped_images"],
                    "in_jianying": r.get("in_jianying", False)},
                   {"draft_path": r["draft_path"], "duration": r["duration"],
                    "in_jianying": r.get("in_jianying", False),
                    "output_mode": "jianying"})


def _safe_name(name: str) -> str:
    """剪映草稿名去掉文件系统非法字符，限长。"""
    import re
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    return name[:60] or "draft"


def _compose_mp4(db, task, task_id, image_paths, weights, segments,
                 audio_path, subs, anim, bgm_path=None) -> dict:
    out_path = storage_root(db) / task_id / "video" / "output.mp4"
    try:
        result = vm.compose(image_paths, weights, audio_path, segments, out_path,
                            enable_subtitles=subs, enable_animations=anim, bgm_path=bgm_path)
    except Exception as e:
        task.status = "failed"
        task.error_code = "E5001"
        task.error_message = f"视频渲染失败: {e}"[:500]
        db.commit()
        raise
    return _finish(db, task, task_id, "video", out_path, "video/mp4",
                   {"duration": result["duration"], "dropped_images": result["dropped_images"]},
                   {"video_path": str(out_path), "duration": result["duration"],
                    "output_mode": "mp4"})
