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
    """取 E、F 产物，返回 (image_paths, weights, segments)。"""
    e_out = _get_output(db, task_id, "E")
    f_out = _get_output(db, task_id, "F")
    if not e_out or not f_out:
        raise ValueError("配图或分段未完成，无法合成")
    images = e_out["images"]
    image_paths = [img["path"] for img in images]
    weights = [float(img.get("suggested_duration", 5)) for img in images]
    return image_paths, weights, f_out["segments"]


def compose_video(db: Session, task_id: str, audio_path: str,
                  enable_subtitles=True, enable_animations=True,
                  output_mode="jianying", bgm_path: str | None = None) -> dict:
    """产出成片。output_mode: jianying（草稿，默认）/ mp4（合成）。
    bgm_path 仅 mp4 合成时混音；剪映草稿模式由用户在剪映里加 BGM。"""
    task = db.get(Task, task_id)
    if not task:
        raise ValueError("任务不存在")

    image_paths, weights, segments = _load_assets(db, task_id)

    if output_mode == "jianying":
        return _compose_jianying(db, task, task_id, image_paths, weights, segments,
                                 audio_path, enable_subtitles, enable_animations)
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
                      audio_path, subs, anim) -> dict:
    draft_dir = storage_root(db) / task_id / "jianying"
    cfg = db.get(Config, 1)
    jianying_dir = (cfg.jianying_draft_dir or "").strip() if cfg else ""
    # 草稿名可读化（抄竞品）：{标题}_{任务后缀}，剪映里一眼能找到。
    title = (task.title or "").strip()
    draft_name = f"{title}_{task_id[-6:]}" if title else task_id
    draft_name = _safe_name(draft_name)
    try:
        r = jianying.build_draft(image_paths, weights, audio_path, segments, draft_dir,
                                 draft_name=draft_name, enable_subtitles=subs,
                                 enable_animations=anim,
                                 jianying_dir=jianying_dir or None)
    except Exception as e:
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
