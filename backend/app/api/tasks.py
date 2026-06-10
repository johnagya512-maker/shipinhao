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
                             TaskListOut, TaskListItem, ScenesPatch,
                             ImageRetryRequest, StepRerunRequest)
from app.models import Task, Config, ModuleResult
from app.services import cost as cost_svc
from app.services import orchestrator, compose
from app.services.scheduler import scheduler

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])

# 单图重试可并发（前端最多 3 张同时跑），但回写 E 产物的 images 数组是
# read-modify-write，并发会丢失更新。用 per-task 锁保护「重读→改单张→写回」临界区，
# 生图本身（慢、不碰共享数据）留在锁外并行。
import threading
_retry_locks: dict[str, threading.Lock] = {}
_retry_locks_guard = threading.Lock()


def _get_retry_lock(task_id: str) -> threading.Lock:
    with _retry_locks_guard:
        lk = _retry_locks.get(task_id)
        if lk is None:
            lk = threading.Lock()
            _retry_locks[task_id] = lk
        return lk


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
        draft_template=body.draft_template or "classic",
        creation_mode=body.creation_mode or "same_topic",
        processing_mode=body.processing_mode, pause_mode=body.pause_mode,
        pause_steps=body.pause_steps or None,
        status="pending",
    )
    db.add(task)
    db.commit()
    scheduler.submit(task.id, _run_pipeline_bg, task.id)
    return task


@router.post("/tasks/analyze-structure")
def analyze_structure(body: dict, db: Session = Depends(get_db)):
    """创建任务前预览二创结果（不创建任务、不入库）：
    拆解爆款结构骨架 → 按骨架改写出成品文案，两个一起返回。
    入参 {text, track?, target_audience?, title?, monetization_mode?,
          rewrite_strength?, narrative_perspective?, creation_mode?}。
    返回 {structure, script}。文案过短或未配 LLM 时返回明确错误。"""
    from app.core.security import decrypt
    from app.modules import text_modules as tm
    text = (body.get("text") or "").strip()
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="文案太短，至少 20 字才能拆解二创")
    cfg = db.get(Config, 1)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    if not llm_key:
        raise HTTPException(status_code=400, detail="未配置大模型 API Key，无法生成")
    prov, model = cfg.llm_provider, cfg.llm_model
    # creation_mode=none 时不拆结构，直接改写；否则先拆骨架再按骨架改写
    structure = {}
    try:
        if (body.get("creation_mode") or "same_topic") != "none":
            s_out, _s = tm.run_structure(prov, model, llm_key, text)
            structure = s_out.get("structure") or {}
        b_out, _b = tm.run_rewrite(
            prov, model, llm_key, text,
            target_audience=body.get("target_audience") or "50+女性",
            title=body.get("title"),
            track=body.get("track") or "character_story",
            monetization_mode=body.get("monetization_mode") or "revenue_share",
            rewrite_strength=body.get("rewrite_strength") or "medium",
            narrative_perspective=body.get("narrative_perspective") or "auto",
            structure_guide=structure or None)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"二创生成失败：{e}"[:200])
    return {"structure": structure, "script": b_out.get("script", "")}


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


@router.get("/tasks-queue/stats")
def queue_stats():
    """调度器并发状态：运行中/排队中数量与并发上限。供列表页展示。"""
    return scheduler.stats()


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
    scheduler.submit(task_id, _compose_bg, task_id, str(audio_path),
                     task.enable_subtitles, task.enable_animations, output_mode)
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/recompose", response_model=TaskOut)
def recompose(task_id: str, output_mode: str = "jianying", db: Session = Depends(get_db)):
    """用最新的配图重新合成视频（剪映草稿/MP4），复用上次上传的配音音频，无需重传。
    适用：改图/重试图后，把更新后的图重新生成成片。找不到历史音频时提示去上传。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if output_mode not in ("jianying", "mp4"):
        raise HTTPException(400, detail="output_mode 仅支持 jianying / mp4")
    if task.status == "processing":
        raise HTTPException(400, detail="任务正在处理中，请稍后再试")
    # 找历史音频：upload_audio 落盘在 <storage>/<task_id>/audio/audio.<ext>
    audio_dir = storage_root(db) / task_id / "audio"
    audio_file = None
    if audio_dir.is_dir():
        for f in sorted(audio_dir.glob("audio.*")):
            if f.is_file():
                audio_file = f
                break
    if not audio_file:
        raise HTTPException(400, detail="未找到历史配音音频，请用「上传音频生成成片」先上传一次")
    task.status = "processing"
    db.commit()
    scheduler.submit(task_id, _compose_bg, task_id, str(audio_file),
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


@router.patch("/tasks/{task_id}/title", response_model=TaskOut)
def update_title(task_id: str, body: dict, db: Session = Depends(get_db)):
    """手动修改任务标题。下次（重新）生成视频时用作草稿名/下载文件名。"""
    from app.services.compose import _safe_name
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    new_title = _safe_name(str(body.get("title") or "").strip())
    if not new_title:
        raise HTTPException(400, detail="标题不能为空")
    task.title = new_title
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(400, detail="任务已处于终态")
    # 若仍在排队（未开跑），从调度队列移除，避免轮到时空跑。
    scheduler.cancel_queued(task_id)
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
    scheduler.submit(task.id, _resume_pipeline_bg, task.id)
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

    def _duration(r):
        if r.started_at and r.finished_at:
            return round((r.finished_at - r.started_at).total_seconds(), 1)
        return None

    def _enrich_output(r):
        """E 模块：对每张图补算 fallback（历史任务没存标记时靠看图识别纯色占位）。"""
        if r.module != "E" or not r.output or "images" not in r.output:
            return r.output
        from app.services.image import is_placeholder_image
        out = dict(r.output)
        imgs = []
        for im_ in out["images"]:
            d = dict(im_)
            if not d.get("fallback"):
                d["fallback"] = is_placeholder_image(d.get("path", ""))
            imgs.append(d)
        out["images"] = imgs
        return out

    return {
        "task": {
            "id": task.id, "status": task.status, "total_cost": float(task.total_cost),
            "transcript": task.transcript, "title": task.title, "author": task.author,
            "keyword": task.keyword, "track": task.track, "modules": task.modules,
            "target_audience": task.target_audience, "monetization_mode": task.monetization_mode,
            "enable_subtitles": task.enable_subtitles, "enable_animations": task.enable_animations,
            "draft_template": getattr(task, "draft_template", "classic"),
            "creation_mode": getattr(task, "creation_mode", "same_topic"),
            "processing_mode": task.processing_mode, "pause_mode": task.pause_mode,
            "pause_steps": task.pause_steps, "paused_at": task.paused_at,
            "aspect_ratio": task.aspect_ratio, "reference_image": task.reference_image,
            "error_code": task.error_code, "error_message": task.error_message,
            "created_at": task.created_at, "updated_at": task.updated_at,
        },
        "modules": [
            {"module": r.module, "status": r.status, "output": _enrich_output(r),
             "cost": float(r.cost), "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
             "retry_count": r.retry_count, "duration": _duration(r)}
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
    scheduler.submit(task.id, _run_pipeline_bg, task.id)
    db.refresh(task)
    return task


# 下游依赖关系：改了某步，其下游产物失效需一并清掉再重跑。
# 文案链 A→H→B→F；配图链 D→CP→SB→P→E；成片 G/T 收尾。
_DOWNSTREAM = {
    "A": ["A", "H", "B", "F", "D", "CP", "SB", "P", "E", "T", "G"],
    "B": ["B", "F", "D", "CP", "SB", "P", "E", "T", "G"],
    "D": ["D", "CP", "SB", "P", "E", "T", "G"],
    "CP": ["CP", "SB", "P", "E", "T", "G"],
    "SB": ["SB", "P", "E", "T", "G"],
    "P": ["P", "E", "T", "G"],
    "E": ["E", "T", "G"],
}


@router.patch("/tasks/{task_id}/scenes", response_model=TaskOut)
def patch_scenes(task_id: str, body: ScenesPatch, db: Session = Depends(get_db)):
    """逐句编辑保存：覆盖 SB 与 P 产物里的分镜列表（cap/desc_prompt/has_character）。
    只改提示词文本，不触发生图——用户改完可再单独点单图重试或单步重跑 E。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    scenes = [{"id": s.id or (i + 1), "cap": s.cap,
               "desc_prompt": s.desc_prompt, "has_character": s.has_character}
              for i, s in enumerate(body.scenes)]
    # 写回 SB
    sb = db.query(ModuleResult).filter_by(task_id=task_id, module="SB").first()
    if sb:
        out = dict(sb.output or {})
        out["scenes"] = scenes
        sb.output = out
    # 写回 P（同步 prompts 里 content 项的 prompt 文本，保持顺序：cover, content*, cta）
    p = db.query(ModuleResult).filter_by(task_id=task_id, module="P").first()
    if p and p.output:
        out = dict(p.output)
        out["scenes"] = scenes
        prompts = out.get("prompts") or []
        ci = 0
        for item in prompts:
            if item.get("sub_type") == "content" and ci < len(scenes):
                item["prompt"] = scenes[ci]["desc_prompt"]
                ci += 1
        out["prompts"] = prompts
        p.output = out
    db.commit()
    db.refresh(task)
    return task


# 可编辑的文本产物白名单：module -> {字段名: 类型校验函数}。
# 「编辑」只覆盖这些字段、不调 AI、不触发下游——对齐竞品「编辑/重跑分开」。
def _is_str(v): return isinstance(v, str)
def _is_list(v): return isinstance(v, list)

_EDITABLE_FIELDS: dict[str, dict] = {
    "A": {"cleaned_text": _is_str},
    "B": {"script": _is_str},
    "D": {"title": _is_str, "author": _is_str, "category": _is_str},
    "CP": {"profile": _is_str},
    "F": {"segments": _is_list},
}


@router.patch("/tasks/{task_id}/modules/{module}/output", response_model=TaskOut)
def edit_module_output(task_id: str, module: str, body: dict, db: Session = Depends(get_db)):
    """直接编辑某步文本产物（清洗稿/改写稿/分句/图书/人物特征），
    只覆盖白名单字段，不调 AI、不扣费、不触发下游。要重算下游用「从此步重跑」。
    分镜(SB/P)请用 PATCH /scenes。"""
    module = module.upper()
    allow = _EDITABLE_FIELDS.get(module)
    if not allow:
        raise HTTPException(400, detail=f"该步骤不支持直接编辑（可编辑：{', '.join(_EDITABLE_FIELDS)}）")
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    fields = body.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(400, detail="缺少 fields")
    # 校验字段名与类型
    clean: dict = {}
    for k, v in fields.items():
        if k not in allow:
            raise HTTPException(400, detail=f"字段 {k} 不可编辑")
        if not allow[k](v):
            raise HTTPException(400, detail=f"字段 {k} 类型不正确")
        clean[k] = v
    # F 分句：统一成 [{text, ...}] 结构，并同步 segment_count
    if module == "F" and "segments" in clean:
        norm = []
        for seg in clean["segments"]:
            if isinstance(seg, str):
                norm.append({"text": seg})
            elif isinstance(seg, dict) and "text" in seg:
                norm.append(seg)
            else:
                raise HTTPException(400, detail="分句格式错误：每句应为文本或含 text 的对象")
        clean["segments"] = norm

    with _get_retry_lock(task_id):
        mr = db.query(ModuleResult).filter_by(task_id=task_id, module=module).first()
        if not mr or not mr.output:
            raise HTTPException(404, detail="该步骤尚无产物，无法编辑")
        out = dict(mr.output)
        out.update(clean)
        if module == "F":
            out["segment_count"] = len(out.get("segments") or [])
        mr.output = out
        db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/images/{index}/retry")
def retry_image(task_id: str, index: int, body: ImageRetryRequest,
                db: Session = Depends(get_db)):
    """单张图重试：只重生成第 index 张（E 产物 images 数组下标），其它图不动。
    可带新 prompt 覆盖该图提示词。复用流水线同款逻辑：提示词先净化(柔化敏感意象)，
    再带退避重试(应对内容审核误判的随机性)；仍失败则透出真实原因到该图。"""
    from app.core.security import decrypt
    from app.services.image import ImageError
    from app.modules.image_module import _gen_with_fallback, _sanitize_imagery, _wrap
    from app.modules import tracks
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    cfg = db.query(Config).first()
    e = db.query(ModuleResult).filter_by(task_id=task_id, module="E").first()
    p = db.query(ModuleResult).filter_by(task_id=task_id, module="P").first()
    sb = db.query(ModuleResult).filter_by(task_id=task_id, module="SB").first()
    if not e or not e.output or "images" not in e.output:
        raise HTTPException(400, detail="该任务尚无配图产物，无法单图重试")
    images = list(e.output["images"])
    if index < 0 or index >= len(images):
        raise HTTPException(400, detail=f"图片下标越界（0~{len(images)-1}）")
    img = dict(images[index])
    sub_type = img.get("sub_type", "content")
    # 该任务的统一画风（三层包裹），保证重试图与其它图风格一致。
    style = tracks.get_style(task.image_style, task.track)
    sidx = index - 1  # 内容图在 scenes 里的下标 = 图片下标 - 1（封面占 0）

    # 取「裸主体」(不含风格包裹)：优先请求里的新词，否则用 SB.scenes 的 desc_prompt。
    # 注意 P.prompts[i].prompt 是 wrap 过的完整提示词，不能直接当主体（会双重套风格）。
    subject = (body.prompt or "").strip()
    if not subject and sb and sb.output:
        scs = sb.output.get("scenes") or []
        if 0 <= sidx < len(scs):
            subject = str(scs[sidx].get("desc_prompt", "")).strip()
    if not subject:
        # 兜底：从 wrap 过的 P.prompt 里剥掉风格前后缀，取回主体
        if p and p.output:
            pl = p.output.get("prompts") or []
            if index < len(pl):
                full = pl[index].get("prompt", "")
                pre, suf = style.get("prefix", ""), style.get("suffix", "")
                subject = full[len(pre):len(full) - len(suf)] if full.startswith(pre) else full
    if not subject:
        raise HTTPException(400, detail="缺少提示词，无法生成")

    # 净化裸主体（柔化敏感意象），再套统一风格包裹 → 最终提示词
    subject = _sanitize_imagery(subject)
    img_key = decrypt(cfg.image_api_key_enc) if cfg and cfg.image_api_key_enc else ""
    out_path = Path(img["path"])

    def _gen(subj):
        """裸主体 → 套风格 → 生成。返回 result。空镜补无人物兜底由调用方传入。"""
        return _gen_with_fallback(cfg.image_provider if cfg else "mock", img_key,
                                  _wrap(style, subj),
                                  sub_type, out_path, img.get("suggested_duration", 6),
                                  model=cfg.image_model if cfg else None,
                                  aspect_ratio=task.aspect_ratio or "9:16")

    try:
        result = _gen(subject)
    except ImageError as ex:
        raise HTTPException(502, detail=f"配图失败：{ex}")

    failed = bool(result.meta.get("fallback"))
    reason = result.meta.get("reason") if failed else None
    rewritten = False

    def _is_audit(rsn):
        return rsn and ("sensitive" in rsn.lower() or "审核" in rsn or "拒绝" in rsn)

    # 若失败原因是内容审核拦截：自动用 LLM 把「主体」改写成安全版本，再套同一风格生成。
    # 这才是真正的「重新生成提示词并重试」——只重试同一句对被审核拦的词没用。
    # 最多改写 3 次，一次比一次激进（逐步抛弃地图/政治/暴力等触发元素）。
    if failed and _is_audit(reason):
        from app.modules import text_modules as tm
        llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
        base = subject
        for attempt in range(1, 4):
            try:
                safe_subj, _ = tm.run_safe_rewrite(cfg.llm_provider, cfg.llm_model,
                                                   llm_key, base, attempt=attempt)
                safe_subj = _sanitize_imagery(safe_subj)
            except Exception as ex:
                reason = reason or f"提示词安全改写失败：{ex}"
                break
            if not safe_subj or safe_subj == subject:
                base = safe_subj or base
                continue
            result = _gen(safe_subj)
            subject = safe_subj
            rewritten = True
            failed = bool(result.meta.get("fallback"))
            reason = result.meta.get("reason") if failed else None
            if not failed or not _is_audit(reason):
                break  # 成功，或换成了非审核类错误，停止改写
            base = safe_subj  # 仍被审核拦，基于这版继续更激进改写

    # 回存：裸主体写回 SB.scenes 和 P.scenes（画廊读这个显示）；
    # wrap 过的完整提示词写回 P.prompts（流水线/再生成读这个）。保持两处一致。
    # 并发安全：多张图可同时重试，回写 E/P/SB 是 read-modify-write，必须在 per-task 锁内
    # 重新读取最新产物再改单张，否则后提交的请求会覆盖其它图刚写入的结果（丢失更新）。
    final_img = dict(img)
    final_img["path"] = result.path
    final_img["fallback"] = failed
    if reason:
        final_img["fail_reason"] = reason
    elif "fail_reason" in final_img:
        del final_img["fail_reason"]

    with _get_retry_lock(task_id):
        # 锁内重新读取，拿到其它并发重试已写入的最新值
        e2 = db.query(ModuleResult).filter_by(task_id=task_id, module="E").first()
        p2 = db.query(ModuleResult).filter_by(task_id=task_id, module="P").first()
        sb2 = db.query(ModuleResult).filter_by(task_id=task_id, module="SB").first()
        if sb2 and sb2.output:
            sbo = dict(sb2.output)
            scs = list(sbo.get("scenes") or [])
            if 0 <= sidx < len(scs):
                scs[sidx] = {**scs[sidx], "desc_prompt": subject}
                sbo["scenes"] = scs
                sb2.output = sbo
        if p2 and p2.output:
            po = dict(p2.output)
            pl = list(po.get("prompts") or [])
            if index < len(pl):
                pl[index] = {**pl[index], "prompt": _wrap(style, subject)}
                po["prompts"] = pl
            scs = list(po.get("scenes") or [])
            if 0 <= sidx < len(scs):
                scs[sidx] = {**scs[sidx], "desc_prompt": subject}
                po["scenes"] = scs
            p2.output = po
        if e2 and e2.output and "images" in e2.output:
            eo = dict(e2.output)
            imgs = list(eo["images"])
            if 0 <= index < len(imgs):
                imgs[index] = final_img
                eo["images"] = imgs
                e2.output = eo
        db.commit()
        img = final_img

    # 返回：是否仍失败、原因、是否做过安全改写、改写后的主体（前端回填到输入框）
    return {"index": index, "image": img, "failed": failed, "reason": reason,
            "rewritten": rewritten, "new_prompt": subject if rewritten else None}


@router.post("/tasks/{task_id}/step/{module}/rerun", response_model=TaskOut)
def rerun_step(task_id: str, module: str, bg: BackgroundTasks,
               db: Session = Depends(get_db)):
    """单步重跑：清掉该模块及其下游产物，从该步重新执行（复用断点续跑，上游走缓存）。"""
    module = module.upper()
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if module not in _DOWNSTREAM:
        raise HTTPException(400, detail=f"不支持从 {module} 单步重跑")
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(402, detail="E2003: 每日成本上限已达")
    # 清掉该步及其下游产物，上游保留 → resume 时上游命中缓存、从该步真正重算
    db.query(ModuleResult).filter(
        ModuleResult.task_id == task_id,
        ModuleResult.module.in_(_DOWNSTREAM[module])).delete(synchronize_session=False)
    task.status = "pending"
    task.error_code = None
    task.error_message = None
    db.commit()
    scheduler.submit(task.id, _run_pipeline_bg, task.id)
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}/image")
def get_task_image(task_id: str, name: str = Query(...), db: Session = Depends(get_db)):
    """返回任务 images 目录下的单张配图（供前端画廊预览）。
    只允许访问该任务 images 目录内的文件，按文件名取，防目录穿越。"""
    img_dir = (storage_root(db) / task_id / "images").resolve()
    # 只取纯文件名，剥掉任何路径分隔，杜绝 ../ 穿越
    fname = Path(name).name
    target = (img_dir / fname).resolve()
    if img_dir not in target.parents or not target.is_file():
        raise HTTPException(404, detail="图片不存在")
    return FileResponse(str(target), media_type="image/png")


@router.get("/tasks/{task_id}/download")
def download_result(task_id: str, db: Session = Depends(get_db)):
    """下载成片产物：剪映草稿 JSON 或合成 MP4，按实际生成的为准。
    文件名用可读标题（{标题}_{后缀}），下载下来一眼能认出是哪条。"""
    from app.services.compose import _safe_name
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    title = (task.title or "").strip()
    base = _safe_name(f"{title}_{task_id[-6:]}") if title else task_id
    jy_dir = storage_root(db) / task_id / "jianying"
    # bare 模式草稿名是 {draft_name}.json（不一定等于 task_id），取目录里实际的 json。
    draft_path = jy_dir / f"{task_id}.json"
    if not draft_path.exists() and jy_dir.is_dir():
        jsons = sorted(jy_dir.glob("*.json"))
        draft_path = jsons[0] if jsons else draft_path
    video_path = storage_root(db) / task_id / "video" / "output.mp4"
    if draft_path.exists():
        return FileResponse(str(draft_path), media_type="application/json",
                            filename=f"{base}_草稿.json")
    if video_path.exists():
        return FileResponse(str(video_path), media_type="video/mp4",
                            filename=f"{base}.mp4")
    raise HTTPException(404, detail="成片尚未生成")
