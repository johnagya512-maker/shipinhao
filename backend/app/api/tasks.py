"""任务相关路由。"""
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.paths import storage_root
from app.api.auth import require_auth
from app.api.schemas import (TaskCreate, TaskOut, EstimateOut, RerunRequest,
                             TaskListOut, TaskListItem)
from app.models import Task, Config, ModuleResult
from app.services import cost as cost_svc
from app.services import orchestrator, compose

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])

REQUIRED = {"A", "B", "G"}
ALLOWED_AUDIO = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/x-m4a"}
ALLOWED_IMAGE = {"image/jpeg", "image/png", "image/webp"}
MAX_REFERENCE_BYTES = 20 * 1024 * 1024  # 主角参考图最大 20MB(前端先压缩,此为兜底)
# 音频魔数（文件头）：用于校验真实类型，防止伪装扩展名（PRD 12.3）
AUDIO_MAGIC = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"RIFF", b"\x00\x00\x00")
AUDIO_MIN_SEC, AUDIO_MAX_SEC = 30, 300  # PRD 5.4 E3002


def _run_pipeline_bg(task_id: str):
    db = SessionLocal()
    try:
        orchestrator.run_pipeline(db, task_id)
    finally:
        db.close()


def _resume_pipeline_bg(task_id: str):
    db = SessionLocal()
    try:
        orchestrator.resume_pipeline(db, task_id)
    finally:
        db.close()


@router.post("/tasks", response_model=TaskOut)
def create_task(body: TaskCreate, bg: BackgroundTasks, db: Session = Depends(get_db)):
    modules = set(body.modules)
    if not REQUIRED.issubset(modules):
        raise HTTPException(400, detail="E1003: modules 必须包含 A、B、G")
    cfg = db.get(Config, 1)
    if not cfg or not cfg.llm_api_key_enc:
        raise HTTPException(400, detail="E2001: 未配置 LLM API Key")
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(402, detail="E2003: 每日成本上限已达")

    # 入口二选一：填了抖音链接（自动采集+ASR）或手填逐字稿，至少其一。
    transcript = (body.transcript or "").strip()
    douyin_url = (body.douyin_url or "").strip()
    if not transcript and not douyin_url:
        raise HTTPException(400, detail="E1001: 请填写抖音链接或粘贴逐字稿")
    has_collect = bool(cfg.collect_api_key_enc and cfg.asr_api_key_enc)
    if not transcript and douyin_url and not has_collect:
        raise HTTPException(400, detail="E6001: 未配置采集/ASR Key，无法自动提取逐字稿，请手动粘贴")

    # 成本预估校验（无逐字稿时按链接采集后的估值留待运行时校验，提交时用占位长度）
    est = cost_svc.estimate_cost(transcript or "x" * 500, body.modules, None,
                                 cfg.llm_provider, cfg.image_provider)
    if est > body.cost_limit:
        raise HTTPException(402, detail=f"预估成本 {est} 元超过上限 {body.cost_limit} 元")

    task = Task(
        id=f"task_{uuid.uuid4().hex[:12]}",
        douyin_url=douyin_url or None,
        transcript=transcript, keyword=body.keyword, title=body.title, author=body.author,
        modules=body.modules, target_audience=body.target_audience,
        track=body.track, monetization_mode=body.monetization_mode, image_style=body.image_style,
        aspect_ratio=body.aspect_ratio,
        voice=body.voice, voice_speed=body.voice_speed, bgm=body.bgm or None,
        reference_image=(body.reference_image or None),
        cost_limit=body.cost_limit, time_limit=body.time_limit,
        enable_subtitles=body.enable_subtitles, enable_animations=body.enable_animations,
        processing_mode=body.processing_mode, pause_mode=body.pause_mode,
        pause_steps=body.pause_steps or None,
        status="pending",
    )
    db.add(task)
    db.commit()
    bg.add_task(_run_pipeline_bg, task.id)
    return task


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    status: str | None = Query(default=None, description="按状态筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """任务列表（PRD 6.1）。支持状态筛选 + 分页，按创建时间倒序。"""
    q = db.query(Task)
    if status:
        q = q.filter(Task.status == status)
    total = q.count()
    rows = (q.order_by(Task.created_at.desc())
             .offset((page - 1) * page_size).limit(page_size).all())
    items = [
        TaskListItem(
            id=t.id, status=t.status, total_cost=float(t.total_cost),
            track=t.track, target_audience=t.target_audience,
            transcript_preview=(t.transcript or "")[:50],
            error_code=t.error_code, created_at=t.created_at, updated_at=t.updated_at,
        )
        for t in rows
    ]
    return TaskListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    return task


@router.post("/tasks/estimate", response_model=EstimateOut)
def estimate(body: TaskCreate, db: Session = Depends(get_db)):
    cfg = db.get(Config, 1)
    provider_llm = cfg.llm_provider if cfg else "deepseek"
    provider_img = cfg.image_provider if cfg else "doubao"
    # transcript 可选：未填（走链接采集）时按占位长度估算上限。
    text = (body.transcript or "").strip() or "x" * 500
    est = cost_svc.estimate_cost(text, body.modules, None, provider_llm, provider_img)
    return EstimateOut(estimated_cost=est, daily_cap_reached=cost_svc.daily_cap_reached(db))


@router.post("/tasks/collect-preview")
def collect_preview(body: dict, db: Session = Depends(get_db)):
    """采集预览：贴抖音链接先拿元数据展示（不创建任务）。
    未配采集 Key 时返回 available=false，前端引导手填。"""
    from app.services import collect as collect_svc
    from app.core.security import decrypt
    url = (body.get("douyin_url") or "").strip()
    if not url:
        raise HTTPException(400, detail="E6001: 请填写抖音链接")
    cfg = db.get(Config, 1)
    key = decrypt(cfg.collect_api_key_enc) if cfg and cfg.collect_api_key_enc else ""
    try:
        cr = collect_svc.fetch_douyin(url, cfg.collect_provider if cfg else "tikhub", key)
        return {"available": True, "title": cr.title, "author": cr.author,
                "play_count": cr.play_count, "digg_count": cr.digg_count,
                "has_video": bool(cr.video_url)}
    except collect_svc.CollectUnavailable:
        return {"available": False, "reason": "未配置采集 API Key，请在配置页填写或手动粘贴逐字稿"}
    except collect_svc.CollectError as e:
        raise HTTPException(400, detail=str(e))


def _compose_bg(task_id: str, audio_path: str, subs: bool, anim: bool, output_mode: str):
    db = SessionLocal()
    try:
        compose.compose_video(db, task_id, audio_path, subs, anim, output_mode=output_mode)
    except Exception:
        pass  # 失败状态已在 compose 内写库
    finally:
        db.close()


@router.post("/tasks/{task_id}/audio", response_model=TaskOut)
async def upload_audio(task_id: str, bg: BackgroundTasks, file: UploadFile = File(...),
                       output_mode: str = "jianying", db: Session = Depends(get_db)):
    """上传配音音频，自动触发成片（PRD 6.5）。
    output_mode: jianying（剪映草稿，秒级，默认）/ mp4（合成视频，较慢）。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status not in ("awaiting_audio", "completed", "failed"):
        raise HTTPException(400, detail="任务尚未到可上传音频阶段")
    if output_mode not in ("jianying", "mp4"):
        raise HTTPException(400, detail="output_mode 仅支持 jianying / mp4")
    if file.content_type not in ALLOWED_AUDIO:
        raise HTTPException(400, detail="E3001: 仅支持 MP3/WAV/M4A")

    data = await file.read()
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(400, detail="E3002: 音频文件过大")

    ext = Path(file.filename or "audio.mp3").suffix or ".mp3"
    audio_dir = storage_root(db) / task_id / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"audio{ext}"
    audio_path.write_bytes(data)

    task.status = "processing"
    db.commit()
    bg.add_task(_compose_bg, task_id, str(audio_path),
                task.enable_subtitles, task.enable_animations, output_mode)
    db.refresh(task)
    return task


@router.post("/tasks/upload-reference")
async def upload_reference(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传主角参考图，存到暂存区，返回路径。创建任务时把该路径填到 reference_image，
    生图时作为角色一致性参考喂给绘图模型（best-effort，模型不支持则自动退回纯文生图）。"""
    if file.content_type not in ALLOWED_IMAGE:
        raise HTTPException(400, detail="仅支持 JPG / PNG / WEBP")
    data = await file.read()
    if len(data) > MAX_REFERENCE_BYTES:
        raise HTTPException(400, detail="参考图过大（上限 8MB）")
    ext = Path(file.filename or "ref.png").suffix or ".png"
    ref_dir = storage_root(db) / "_reference_uploads"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_path = ref_dir / f"ref_{uuid.uuid4().hex[:12]}{ext}"
    ref_path.write_bytes(data)
    return {"reference_image": str(ref_path)}


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(400, detail="任务已处于终态")
    task.status = "cancelled"
    db.commit()
    return task


@router.post("/tasks/{task_id}/resume", response_model=TaskOut)
def resume_task(task_id: str, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """暂停确认后继续执行（处理模式/暂停确认功能）。仅 awaiting_confirm 可调。
    从暂停点续跑：已完成步骤走缓存不重算、不重复扣费。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status != "awaiting_confirm":
        raise HTTPException(400, detail="任务当前不处于待确认状态")
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(402, detail="E2003: 每日成本上限已达")
    task.status = "pending"
    db.commit()
    bg.add_task(_resume_pipeline_bg, task.id)
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}/results")
def get_task_results(task_id: str, db: Session = Depends(get_db)):
    """任务详情：各模块产物（PRD 6.3）。供详情页展示清洗/改写/分段/合规/配图结果。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    rows = (db.query(ModuleResult)
              .filter_by(task_id=task_id)
              .order_by(ModuleResult.module).all())
    return {
        "task": {
            "id": task.id, "status": task.status, "total_cost": float(task.total_cost),
            "transcript": task.transcript, "title": task.title, "author": task.author,
            "keyword": task.keyword, "track": task.track, "modules": task.modules,
            "target_audience": task.target_audience, "monetization_mode": task.monetization_mode,
            "enable_subtitles": task.enable_subtitles, "enable_animations": task.enable_animations,
            "processing_mode": task.processing_mode, "pause_mode": task.pause_mode,
            "pause_steps": task.pause_steps, "paused_at": task.paused_at,
            "error_code": task.error_code, "error_message": task.error_message,
            "created_at": task.created_at, "updated_at": task.updated_at,
        },
        "modules": [
            {"module": r.module, "status": r.status, "output": r.output,
             "cost": float(r.cost), "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
             "retry_count": r.retry_count}
            for r in rows
        ],
    }


@router.post("/tasks/{task_id}/rerun", response_model=TaskOut)
def rerun_task(task_id: str, body: RerunRequest, bg: BackgroundTasks,
               db: Session = Depends(get_db)):
    """blocked/failed 任务改文案后重跑（PRD 5.3）。改文案则清掉旧模块结果重新生成。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status not in ("blocked", "failed", "cancelled"):
        raise HTTPException(400, detail="仅 blocked/failed/cancelled 任务可重跑")
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(402, detail="E2003: 每日成本上限已达")
    if body.transcript and body.transcript.strip():
        task.transcript = body.transcript.strip()
        # 改了文案：清空已有模块结果，从头重跑（旧产物已失效）
        db.query(ModuleResult).filter_by(task_id=task_id).delete()
    task.status = "pending"
    task.error_code = None
    task.error_message = None
    db.commit()
    bg.add_task(_run_pipeline_bg, task.id)
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}/download")
def download_result(task_id: str, db: Session = Depends(get_db)):
    """下载成片产物：剪映草稿 JSON 或合成 MP4，按实际生成的为准。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    draft_path = storage_root(db) / task_id / "jianying" / f"{task_id}.json"
    video_path = storage_root(db) / task_id / "video" / "output.mp4"
    if draft_path.exists():
        return FileResponse(str(draft_path), media_type="application/json",
                            filename=f"{task_id}_draft.json")
    if video_path.exists():
        return FileResponse(str(video_path), media_type="video/mp4",
                            filename=f"{task_id}.mp4")
    raise HTTPException(404, detail="成片尚未生成")
