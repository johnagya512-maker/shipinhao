"""任务相关路由。"""
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.paths import storage_root
from app.api.auth import require_auth
from app.api.schemas import (TaskCreate, TaskOut, EstimateOut, RerunRequest,
                             TaskListOut, TaskListItem, ScenesPatch,
                             ImageRetryRequest, ImageBatchRetryRequest, StepRerunRequest)
from app.models import Task, Config, ModuleResult, CostLog
from app.services import cost as cost_svc
from app.services import orchestrator, compose
from app.services.lyrics_align import align_lyrics
from app.services.scheduler import scheduler

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])

# 单图重试可并发（前端最多 3 张同时跑），但回写 E 产物的 images 数组是
# read-modify-write，并发会丢失更新。用 per-task 锁保护「重读→改单张→写回」临界区，
# 生图本身（慢、不碰共享数据）留在锁外并行。
import threading
import weakref
_retry_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
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
_UPLOAD_STALE_HOURS = 24  # 暂存文件超过此时长且无任务引用则删除

# BUG-14: base_url 白名单，防止 SSRF。只允许空（跟随全局配置）或已知中转站域名。
_ALLOWED_BASE_URL_HOSTS = {
    "api.apimart.com",
    "api.apicore.ai",
    "api.tu-zi.com",
    "api.openai.com",
    "ark.cn-beijing.volces.com",
}


def _validate_base_url(base_url: str | None):
    """BUG-14: 校验 base_url 只能是白名单内的域名，防止 SSRF。"""
    if not base_url:
        return
    from urllib.parse import urlparse
    host = urlparse(base_url).hostname or ""
    if host not in _ALLOWED_BASE_URL_HOSTS:
        raise HTTPException(400, detail=f"不允许的 base_url 域名: {host}")


def _cleanup_stale_uploads(directory: Path, pattern: str, db: Session, stale_hours: int = _UPLOAD_STALE_HOURS):
    """清理暂存目录中超过 stale_hours 小时的孤儿文件（无对应任务引用）。BUG-13 修复。"""
    if not directory.exists():
        return
    import time as _time
    cutoff = _time.time() - stale_hours * 3600
    for f in directory.iterdir():
        if not f.is_file() or not f.match(pattern):
            continue
        if f.stat().st_mtime > cutoff:
            continue
        path_str = str(f)
        # 检查是否被任何任务引用（reference_image / reference_images / audio_file）
        in_use = db.query(Task).filter(
            (Task.reference_image == path_str) |
            (Task.audio_file == path_str)
        ).first()
        if in_use:
            continue
        try:
            f.unlink()
        except Exception:
            pass


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
    # 唱歌·MV模式：可以不传逐字稿（用上传的音频），但必须传 lyrics 作为字幕和分镜依据
    transcript = (body.transcript or "").strip()
    douyin_url = (body.douyin_url or "").strip()
    is_music = body.video_mode == "music"
    if is_music:
        lyrics = (body.lyrics or "").strip()
        if not lyrics:
            raise HTTPException(400, detail="E1005: 唱歌·MV模式必须填写歌词")
        if not body.audio_file:
            raise HTTPException(400, detail="E1006: 唱歌·MV模式必须上传音频文件")
    else:
        if not transcript and not douyin_url:
            raise HTTPException(400, detail="E1001: 请填写抖音链接或粘贴逐字稿")
        has_collect = bool(cfg.collect_api_key_enc and cfg.asr_api_key_enc)
        if not transcript and douyin_url and not has_collect:
            raise HTTPException(400, detail="E6001: 未配置采集/ASR Key，无法自动提取逐字稿，请手动粘贴")

    # 预览二创定稿：用户在预览框编辑确认的文案，作为最终成片文案直接使用，
    # 跳过 A 清洗 + B 改写（走 direct 模式），保证所见即所得、省两次 LLM。
    edited = (body.edited_script or "").strip()
    _processing_mode = body.processing_mode
    if edited:
        transcript = edited
        douyin_url = ""          # 已有定稿文案，无需再采集/ASR
        _processing_mode = "direct"

    # 成本预估校验（无逐字稿时按链接采集后的估值留待运行时校验，提交时用占位长度）
    # 固定张数模式：按实际指定张数估算成本
    _mode = body.image_count_mode or "auto"
    image_count = (body.fixed_image_count or 5) if _mode in ("fixed", "fixed_5") else None
    est = cost_svc.estimate_cost(transcript or "x" * 500, body.modules, image_count,
                                 cfg.llm_provider, cfg.image_provider,
                                 body.image_gen_mode or "per_image",
                                 getattr(cfg, "image_unit_price", None))
    if est > body.cost_limit:
        raise HTTPException(402, detail=f"预估成本 {est} 元超过上限 {body.cost_limit} 元")

    task = Task(
        id=f"task_{uuid.uuid4().hex[:12]}",
        douyin_url=douyin_url or None,
        transcript=transcript, keyword=body.keyword, title=body.title, author=body.author,
        modules=body.modules, target_audience=body.target_audience,
        track=body.track, monetization_mode=body.monetization_mode, image_style=body.image_style,
        aspect_ratio=body.aspect_ratio, layout=body.layout or "full",
        voice=body.voice, voice_speed=body.voice_speed, bgm=body.bgm or None,
        reference_image=(body.reference_image or None),
        reference_images=body.reference_images or None,  # 多参考图：[{"key","path"}, ...]
        cost_limit=body.cost_limit, time_limit=body.time_limit,
        enable_subtitles=body.enable_subtitles, enable_animations=body.enable_animations,
        draft_template=body.draft_template or "classic",
        video_mode=body.video_mode or "vlog",
        creation_mode=body.creation_mode or "same_topic",
        image_gen_mode=body.image_gen_mode or "per_image",
        image_count_mode=body.image_count_mode or "auto",
        fixed_image_count=body.fixed_image_count or 5,
        processing_mode=_processing_mode, pause_mode=body.pause_mode,
        pause_steps=body.pause_steps or None,
        status="pending",
    )
    db.add(task)
    db.commit()

    # 唱歌·MV模式：将上传的音频文件从暂存区移动到任务目录
    if is_music and body.audio_file:
        from shutil import move
        task_storage = storage_root(db) / task.id
        audio_dir = task_storage / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        src_path = Path(body.audio_file)
        if src_path.exists():
            ext = src_path.suffix or ".mp3"
            dst_path = audio_dir / f"audio{ext}"
            move(str(src_path), str(dst_path))
            task.audio_file = str(dst_path)
            task.lyrics = (body.lyrics or "").strip()
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
    from app.modules.retry import with_retry
    from app.services.llm import LLMError
    text = (body.get("text") or "").strip()
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="文案太短，至少 20 字才能拆解二创")
    cfg = db.get(Config, 1)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    if not llm_key:
        raise HTTPException(status_code=400, detail="未配置大模型 API Key，无法生成")
    prov, model = cfg.llm_provider, cfg.llm_model
    _mode = body.get("creation_mode") or "same_topic"
    # lite=手册轻量改写（不拆结构）；remix=中度仿写（不拆结构）；
    # book_remix=图书带货深度二创（不拆结构）；
    # none=不拆直接改写；其余=先拆爆款骨架再按骨架重写。
    # 接入 with_retry：第三方网关 504/超时自动重试，和正式任务一致。
    structure = {}
    try:
        if _mode == "lite":
            (b_out, _b), _ = with_retry(lambda: tm.run_rewrite(
                prov, model, llm_key, text,
                target_audience=body.get("target_audience") or "50+女性",
                title=body.get("title"), lite=True,
                monetization_mode=body.get("monetization_mode") or "revenue_share",
                keyword=body.get("keyword") or "", author=body.get("author") or ""), 2)
        elif _mode == "remix":
            (b_out, _b), _ = with_retry(lambda: tm.run_rewrite(
                prov, model, llm_key, text,
                target_audience=body.get("target_audience") or "50+女性",
                title=body.get("title"), remix=True,
                monetization_mode=body.get("monetization_mode") or "revenue_share",
                rewrite_strength=body.get("rewrite_strength") or "medium",
                narrative_perspective=body.get("narrative_perspective") or "auto",
                keyword=body.get("keyword") or "", author=body.get("author") or ""), 2)
        elif _mode == "book_remix":
            (b_out, _b), _ = with_retry(lambda: tm.run_rewrite(
                prov, model, llm_key, text,
                target_audience=body.get("target_audience") or "50+女性",
                title=body.get("title"), book_remix=True,
                monetization_mode=body.get("monetization_mode") or "revenue_share",
                rewrite_strength=body.get("rewrite_strength") or "medium",
                narrative_perspective=body.get("narrative_perspective") or "auto",
                keyword=body.get("keyword") or "", author=body.get("author") or ""), 2)
        else:
            if _mode != "none":
                (s_out, _s), _ = with_retry(
                    lambda: tm.run_structure(prov, model, llm_key, text), 2)
                structure = s_out.get("structure") or {}
            (b_out, _b), _ = with_retry(lambda: tm.run_rewrite(
                prov, model, llm_key, text,
                target_audience=body.get("target_audience") or "50+女性",
                title=body.get("title"),
                track=body.get("track") or "character_story",
                monetization_mode=body.get("monetization_mode") or "revenue_share",
                rewrite_strength=body.get("rewrite_strength") or "medium",
                narrative_perspective=body.get("narrative_perspective") or "auto",
                structure_guide=structure or None), 2)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"二创生成失败：{e}"[:200])
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
    # 固定张数模式：成本估算按实际指定张数，不按时长动态估算
    _mode = body.image_count_mode or "auto"
    image_count = (body.fixed_image_count or 5) if _mode in ("fixed", "fixed_5") else None
    est = cost_svc.estimate_cost(text, body.modules, image_count, provider_llm, provider_img,
                                 body.image_gen_mode or "per_image",
                                 getattr(cfg, "image_unit_price", None) if cfg else None)
    return EstimateOut(estimated_cost=est, daily_cap_reached=cost_svc.daily_cap_reached(db))


@router.post("/tasks/parse-transcript")
def parse_transcript(body: dict, db: Session = Depends(get_db)):
    """解析视频链接 → 出逐字稿（不创建任务）。
    贴分享链接/口令 → 采集拿无水印视频地址 → ASR 转写成文案，返回给前端展示。
    用户拿到文案后可手动改、可点二创预览，满意了再「开始生成」。
    入参 {douyin_url}。返回 {transcript, title, author, platform, play_count, digg_count}。
    未配采集/ASR Key 或链接无效时返回明确错误，引导手填。"""
    from app.services import collect as collect_svc
    from app.services import asr as asr_svc
    from app.core.security import decrypt
    url = (body.get("douyin_url") or "").strip()
    if not url:
        raise HTTPException(400, detail="E6001: 请填写视频链接")
    cfg = db.get(Config, 1)
    collect_key = decrypt(cfg.collect_api_key_enc) if cfg and cfg.collect_api_key_enc else ""
    asr_key = decrypt(cfg.asr_api_key_enc) if cfg and cfg.asr_api_key_enc else ""
    if not collect_key:
        raise HTTPException(400, detail="E6001: 未配置采集 API Key，请在配置页填写，或切到「粘贴文案」手填逐字稿")
    if not asr_key:
        raise HTTPException(400, detail="E6101: 未配置 ASR（语音转写）API Key，请在配置页填写，或切到「粘贴文案」手填逐字稿")
    proxy = (getattr(cfg, "proxy_url", None) or "").strip() or None

    # 采集：拿元数据 + 无水印视频地址
    try:
        cr = collect_svc.fetch_video(url, cfg.collect_provider if cfg else "tikhub", collect_key, proxy=proxy)
    except collect_svc.CollectUnavailable:
        raise HTTPException(400, detail="E6001: 未配置采集 API Key，请在配置页填写，或切到「粘贴文案」手填逐字稿")
    except collect_svc.CollectError as e:
        raise HTTPException(400, detail=str(e))
    if not cr.video_url:
        raise HTTPException(400, detail="E6009: 采集成功但未取到视频地址，无法转写；请手动粘贴逐字稿")

    # ASR：视频 → 逐字稿（传候选地址列表+时长，内部校验音频抽全、必要时换地址重试）
    try:
        ar = asr_svc.transcribe_url(cr.video_url_candidates or cr.video_url,
                                    cfg.asr_provider if cfg else "siliconflow", asr_key,
                                    proxy=proxy, expect_ms=cr.duration_ms)
    except asr_svc.ASRUnavailable:
        raise HTTPException(400, detail="E6101: 未配置 ASR API Key，请在配置页填写，或切到「粘贴文案」手填逐字稿")
    except asr_svc.ASRError as e:
        raise HTTPException(502, detail=str(e))
    text = (ar.text or "").strip()
    if not text:
        raise HTTPException(502, detail="E6107: 转写结果为空，可能该视频无人声；请手动粘贴逐字稿")
    return {"transcript": text, "title": cr.title, "author": cr.author,
            "platform": cr.platform, "play_count": cr.play_count, "digg_count": cr.digg_count}


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
        cr = collect_svc.fetch_douyin(url, cfg.collect_provider if cfg else "tikhub", key,
                                      proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None)
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
    except Exception as e:
        # BUG-G: _load_assets 抛出的 ValueError（配图/分段未就绪）会被静默吞掉，
        # 任务永久卡在 processing。在此兜底写 failed，确保状态可恢复。
        try:
            task = db.get(Task, task_id)
            if task and task.status == "processing":
                task.status = "failed"
                task.error_code = "E5001"
                task.error_message = str(e)[:500]
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/tasks/{task_id}/audio", response_model=TaskOut)
async def upload_audio(task_id: str, bg: BackgroundTasks, file: UploadFile = File(...),
                       output_mode: str = "jianying",
                       lyrics: str = Form(""),
                       db: Session = Depends(get_db)):
    """上传配音音频，自动触发成片（PRD 6.5）。
    output_mode: jianying（剪映草稿，秒级，默认）/ mp4（合成视频，较慢）。
    lyrics: 歌词文本（可选），提供后按歌词行对齐图片切换与字幕。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status == "processing":
        raise HTTPException(400, detail="任务正在处理中，请稍后再试")
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

    # 如果提供了歌词 → 做歌词对齐，写入 T 模块产物供合成使用
    if lyrics.strip():
        try:
            seg_texts, seg_durations, paragraph_breaks = align_lyrics(lyrics.strip(), str(audio_path))
            t_mr = db.query(ModuleResult).filter_by(task_id=task_id, module="T").first()
            if not t_mr:
                t_mr = ModuleResult(task_id=task_id, module="T")
                db.add(t_mr)
            t_mr.status = "success"
            t_mr.output = {
                "seg_texts": seg_texts,
                "seg_durations": seg_durations,
                "paragraph_breaks": paragraph_breaks,
                "seg_source": "scene",
                "aligned_by": "lyrics",
            }
            db.commit()
        except Exception as e:
            # 歌词对齐失败不打断主流程，记日志后继续
            import logging
            logging.getLogger("uvicorn").warning(
                "歌词对齐失败 task=%s: %s", task_id, e
            )
    else:
        # BUG-20: TTS 失败后用户手动上传音频，T 模块仍显示"失败"。
        # 上传成功即代表音频来源已由用户提供，将 T 状态更新为 success，
        # 让详情页展示正确状态（合成成片的逻辑走 _compose_bg，不依赖此字段）。
        t_mr = db.query(ModuleResult).filter_by(task_id=task_id, module="T").first()
        if t_mr and t_mr.status == "failed":
            t_mr.status = "success"
            t_mr.output = {"audio_path": str(audio_path), "seg_source": "manual_upload"}
            db.commit()

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
    _cleanup_stale_uploads(ref_dir, "ref_*", db)
    ref_path = ref_dir / f"ref_{uuid.uuid4().hex[:12]}{ext}"
    ref_path.write_bytes(data)
    return {"reference_image": str(ref_path)}


@router.post("/tasks/upload-reference-multi")
async def upload_reference_multi(files: list[UploadFile] = File(...), keys: str = "[]",
                                 db: Session = Depends(get_db)):
    """批量上传多参考图，返回前端需结合 keys 自行组装。
    
    Args:
        files: 参考图文件列表
        keys: JSON 数组字符串，每个文件名对应的 key（角色名/场景标识）。
              如 ["霍英东","张子强","空场景"]
    
    返回 {"reference_images": [{key, path, error?}, ...]}，与 files 一一对应（顺序、数量都不变，
    前端按下标对齐结果），单个文件不合法只置 error 不影响其它文件，绝不静默丢弃整条记录。
    """
    import json
    key_list = json.loads(keys) if keys else []
    results = []
    for i, f in enumerate(files):
        key = key_list[i] if i < len(key_list) else f"role_{i+1}"
        if f.content_type not in ALLOWED_IMAGE:
            results.append({"key": key, "path": None, "error": "仅支持 JPG / PNG / WEBP"})
            continue
        data = await f.read()
        if len(data) > MAX_REFERENCE_BYTES:
            results.append({"key": key, "path": None, "error": "参考图过大（上限 8MB）"})
            continue
        ext = Path(f.filename or "ref.png").suffix or ".png"
        ref_dir = storage_root(db) / "_reference_uploads"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_path = ref_dir / f"ref_{uuid.uuid4().hex[:12]}{ext}"
        ref_path.write_bytes(data)
        results.append({"key": key, "path": str(ref_path)})
    return {"reference_images": results}


@router.post("/tasks/upload-audio")
async def upload_audio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传音频文件（唱歌·MV模式），存到暂存区，返回路径。创建任务时把该路径填到 audio_file。"""
    if file.content_type not in ALLOWED_AUDIO:
        raise HTTPException(400, detail="仅支持 MP3 / WAV / M4A")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, detail="音频文件过大（上限 50MB）")
    ext = Path(file.filename or "audio.mp3").suffix or ".mp3"
    audio_dir = storage_root(db) / "_audio_uploads"
    audio_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_uploads(audio_dir, "audio_*", db)
    audio_path = audio_dir / f"audio_{uuid.uuid4().hex[:12]}{ext}"
    audio_path.write_bytes(data)
    return {"audio_file": str(audio_path)}


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


@router.post("/tasks/{task_id}/regenerate-titles", response_model=TaskOut)
def regenerate_titles(task_id: str, db: Session = Depends(get_db)):
    """只重新生成标题/标签，其他产物不动。"""
    from app.core.security import decrypt
    from app.modules import text_modules as tm
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    cfg = db.get(Config, 1)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    if not llm_key:
        raise HTTPException(400, detail="未配置大模型 API Key")
    # 取最终文案：优先 edited_script，其次 B 改写输出，再其次 A 清洗后正文，最后原始逐字稿
    script = ""
    b = db.query(ModuleResult).filter_by(task_id=task_id, module="B").first()
    if b and b.output and b.output.get("script"):
        script = b.output["script"]
    if not script:
        a = db.query(ModuleResult).filter_by(task_id=task_id, module="A").first()
        if a and a.output and a.output.get("cleaned_text"):
            script = a.output["cleaned_text"]
    if not script:
        script = task.transcript or ""
    if len(script.strip()) < 10:
        raise HTTPException(400, detail="文案太短，无法生成标题")
    try:
        (short_title, long_title, tags), _ = tm.run_gen_title_tags(
            cfg.llm_provider, cfg.llm_model, llm_key, script, task.keyword, track=task.track)
        if short_title:
            task.short_title = short_title
        if long_title:
            task.long_title = long_title
        if tags:
            task.hashtags = tags
        db.commit()
        db.refresh(task)
    except Exception as e:
        raise HTTPException(502, detail=f"标题生成失败：{e}"[:200])
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


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    """删除任务：仅终态（completed/failed/cancelled/blocked）可删。
    清理模块结果、配图目录、剪映草稿等产物，然后删除任务记录。"""
    import shutil
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status not in ("completed", "failed", "cancelled", "blocked"):
        raise HTTPException(400, detail="仅已完成/失败/已取消/已拦截的任务可删除")
    # 从调度队列移除（防边缘情况）
    scheduler.cancel_queued(task_id)
    # 先在事务内删除所有 DB 记录（ModuleResult + CostLog + Task），再清磁盘
    # BUG-8: 不删 CostLog 会导致已删任务的费用被计入每日上限，误拦新任务
    # BUG-6: 放同一事务，避免进程崩溃后 DB 和磁盘状态不一致
    db.query(ModuleResult).filter_by(task_id=task_id).delete()
    db.query(CostLog).filter_by(task_id=task_id).delete()
    db.delete(task)
    db.commit()
    # 事务提交后再清磁盘（即使失败也不影响 DB 一致性）
    storage = storage_root(db)
    for subdir in ("images", "jianying", "audio", "video"):
        d = storage / task_id / subdir
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    # BUG-H: 清理空父目录（只删空目录，有残留文件时静默跳过）
    try:
        (storage / task_id).rmdir()
    except Exception:
        pass
    return {"ok": True}


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
    # BUG-1: 原子 CAS——只有当前仍是 awaiting_confirm 才改为 pending，
    # 防止并发两个 resume 请求都通过检查后双跑 pipeline（双重生图扣费）。
    from sqlalchemy import update
    rows = db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status == "awaiting_confirm")
        .values(status="pending")
    ).rowcount
    db.commit()
    if rows == 0:
        raise HTTPException(400, detail="任务当前不处于待确认状态")
    scheduler.submit(task.id, _resume_pipeline_bg, task.id)
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/compliance-fix", response_model=TaskOut)
def compliance_fix(task_id: str, db: Session = Depends(get_db)):
    """一键 AI 合规改写：针对当前残余违规点让 LLM 定向软化一轮，改完重审、回写产物。
    任务保持 awaiting_confirm（停在确认页），用户看改后稿+新风险再决定。仅停在 H 时可调。"""
    from app.core.security import decrypt
    from app.modules import text_modules as tm
    from app.modules.retry import with_retry
    from app.services.llm import LLMError
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    if task.status != "awaiting_confirm" or task.paused_at != "H":
        raise HTTPException(400, detail="仅在合规确认关卡可一键改写")
    cfg = db.get(Config, 1)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    if not llm_key:
        raise HTTPException(400, detail="未配置大模型 API Key，无法改写")
    # 当前文案：full_auto 取 B 稿，否则取 A 清洗稿。
    wb = "B" if task.processing_mode == "full_auto" else "A"
    skey = "script" if wb == "B" else "cleaned_text"
    with _get_retry_lock(task_id):
        src = db.query(ModuleResult).filter_by(task_id=task_id, module=wb).first()
        script = (src.output or {}).get(skey, "") if src and src.output else ""
        h = db.query(ModuleResult).filter_by(task_id=task_id, module="H").first()
        violations = (h.output or {}).get("violations", []) if h and h.output else []
        if not script.strip():
            raise HTTPException(404, detail="无可改写的文案产物")
        try:
            (fix_out, _fr), _ = with_retry(lambda: tm.run_compliance_fix(
                cfg.llm_provider, cfg.llm_model, llm_key, script, violations,
                track=task.track), 2)
        except LLMError as e:
            raise HTTPException(502, detail=f"合规改写失败：{e}"[:200])
        new_script = (fix_out or {}).get("script", "").strip()
        if not new_script:
            raise HTTPException(502, detail="改写返回为空，请重试")
        if src:                                  # 回写改后稿
            o = dict(src.output or {}); o[skey] = new_script; src.output = o
        # 重审并更新 H 产物（保留 awaiting_user_confirm，仍停确认页）
        h_chk = tm.run_compliance(cfg.llm_provider, cfg.llm_model, llm_key,
                                  new_script, track=task.track)
        h_new = h_chk[0] if isinstance(h_chk, tuple) else h_chk
        h_new["awaiting_user_confirm"] = True
        h_new["auto_fixed"] = ((h.output or {}).get("auto_fixed", 0) if h and h.output else 0) + 1
        if h:
            h.output = h_new
        db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/originality-check")
def originality_check(task_id: str, db: Session = Depends(get_db)):
    """手动触发相似度检测：对比 A 清洗稿与 B 改写稿，返回检测结果并写入 O 模块产物。"""
    from app.modules import text_modules as tm
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    a = db.query(ModuleResult).filter_by(task_id=task_id, module="A").first()
    b = db.query(ModuleResult).filter_by(task_id=task_id, module="B").first()
    if not a or not b:
        raise HTTPException(400, detail="暂无可检测的文案产物（需完成 A/B 两步）")
    cleaned = (a.output or {}).get("cleaned_text", "")
    script = (b.output or {}).get("script", "")
    if not cleaned.strip() or not script.strip():
        raise HTTPException(400, detail="文案内容为空，无法检测")
    result = tm.check_originality(cleaned, script, max_similarity_ratio=0.40)
    mr = db.query(ModuleResult).filter_by(task_id=task_id, module="O").first()
    if not mr:
        mr = ModuleResult(task_id=task_id, module="O")
        db.add(mr)
    mr.status = "success"
    mr.output = result
    mr.finished_at = datetime.utcnow()
    db.commit()
    return result


@router.post("/tasks/{task_id}/originality-rewrite", response_model=TaskOut)
def originality_rewrite(task_id: str, db: Session = Depends(get_db)):
    """继续降重：对当前改写稿再做一次针对性降重，回写 B 产物并更新 O 检测结果。
    任务保持当前状态，不自动继续后续流程，让用户在详情页确认。"""
    from app.modules import text_modules as tm
    from app.core.security import decrypt
    from app.modules.retry import with_retry
    from app.services.llm import LLMError
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    a = db.query(ModuleResult).filter_by(task_id=task_id, module="A").first()
    b = db.query(ModuleResult).filter_by(task_id=task_id, module="B").first()
    if not a or not b:
        raise HTTPException(400, detail="暂无可改写的文案产物（需完成 A/B 两步）")
    cleaned = (a.output or {}).get("cleaned_text", "")
    script = (b.output or {}).get("script", "")
    if not cleaned.strip() or not script.strip():
        raise HTTPException(400, detail="文案内容为空，无法改写")
    cfg = db.get(Config, 1)
    llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    if not llm_key:
        raise HTTPException(400, detail="未配置大模型 API Key，无法改写")
    with _get_retry_lock(task_id):
        # 先检测当前稿
        check_result = tm.check_originality(cleaned, script, max_similarity_ratio=0.40)
        if check_result["passed"]:
            _save_o_result(db, task_id, check_result)
            db.refresh(task)
            return task
        try:
            (fix_out, _fr), _ = with_retry(lambda: tm.run_rewrite_decrease_similarity(
                cfg.llm_provider, cfg.llm_model, llm_key,
                cleaned, script, check_result), 2)
        except LLMError as e:
            raise HTTPException(502, detail=f"降重改写失败：{e}"[:200])
        new_script = (fix_out or {}).get("script", "").strip()
        if not new_script:
            raise HTTPException(502, detail="改写返回为空，请重试")
        # 回写 B 产物
        b.output = {**(b.output or {}), "script": new_script}
        # 记录降重成本（rebill 替换而非累加，防止多次调用叠加）
        c = cost_svc.actual_llm_cost(_fr.tokens_in, _fr.tokens_out, cfg.llm_provider)
        cost_svc.rebill_module(db, task, "O", cfg.llm_provider, c)
        # 复测并保存 O
        recheck = tm.check_originality(cleaned, new_script, max_similarity_ratio=0.40)
        _save_o_result(db, task_id, recheck)
        db.commit()
    db.refresh(task)
    return task


def _save_o_result(db: Session, task_id: str, result: dict):
    """辅助：把原创度检测结果写回 O 模块产物。"""
    mr = db.query(ModuleResult).filter_by(task_id=task_id, module="O").first()
    if not mr:
        mr = ModuleResult(task_id=task_id, module="O")
        db.add(mr)
    mr.status = "success"
    mr.output = result
    mr.finished_at = datetime.utcnow()


@router.get("/tasks/{task_id}/results")
def get_task_results(task_id: str, db: Session = Depends(get_db)):
    """任务详情：各模块产物（PRD 6.3）。供详情页展示清洗/改写/分段/合规/配图结果。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    rows = (db.query(ModuleResult)
              .filter_by(task_id=task_id)
              .order_by(ModuleResult.module).all())

    # 成本明细：按模块聚合该任务的真实记账（cost_logs），供前端"成本明细"展示。
    from app.models import CostLog
    from sqlalchemy import func as _func
    _MOD_NAME = {"A": "文案清洗", "B": "智能改写", "S2": "结构拆解", "D": "图书识别",
                 "CP": "人物反推", "SB": "画面脚本", "H": "合规审查", "E": "配图生成",
                 "T": "配音合成", "F": "分句分段", "G": "视频合成"}
    cost_rows = (db.query(CostLog.module, _func.sum(CostLog.cost))
                   .filter(CostLog.task_id == task_id)
                   .group_by(CostLog.module).all())
    cost_breakdown = [{"module": m, "name": _MOD_NAME.get(m, m), "cost": round(float(c or 0), 4)}
                      for m, c in cost_rows if (c or 0) > 0]
    cost_breakdown.sort(key=lambda x: -x["cost"])

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
            "video_mode": getattr(task, "video_mode", "vlog"),
            "creation_mode": getattr(task, "creation_mode", "same_topic"),
            "processing_mode": task.processing_mode, "pause_mode": task.pause_mode,
            "pause_steps": task.pause_steps, "paused_at": task.paused_at,
            "aspect_ratio": task.aspect_ratio, "layout": getattr(task, "layout", "full"),
            "reference_image": task.reference_image,
            "long_title": getattr(task, "long_title", None),
            "short_title": getattr(task, "short_title", None),
            "hashtags": getattr(task, "hashtags", None),
            "comment_cta": getattr(task, "comment_cta", None),
            "error_code": task.error_code, "error_message": task.error_message,
            "cost_breakdown": cost_breakdown,
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
        # 改了文案：清空旧模块结果和 CostLog，从头重跑（BUG-L: 不清 CostLog 会导致日限额虚高）
        db.query(ModuleResult).filter_by(task_id=task_id).delete()
        db.query(CostLog).filter_by(task_id=task_id).delete()
        task.total_cost = 0.0
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
    "H": ["H", "B", "F", "D", "CP", "SB", "P", "E", "T", "G"],
    "B": ["B", "F", "D", "CP", "SB", "P", "E", "T", "G"],
    "F": ["F", "D", "CP", "SB", "P", "E", "T", "G"],
    "D": ["D", "CP", "SB", "P", "E", "T", "G"],
    "CP": ["CP", "SB", "P", "E", "T", "G"],
    "SB": ["SB", "P", "E", "T", "G"],
    "P": ["P", "E", "T", "G"],
    "E": ["E", "T", "G"],
    "T": ["T", "G"],
    "G": ["G"],
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


def _resolve_subject(index, sidx, body_prompt, p, sb, style):
    """按图片下标取「裸主体」(不含风格包裹)：优先请求里的新词，否则用 SB.scenes 的
    desc_prompt，再兜底从 wrap 过的 P.prompt 里剥风格前后缀。找不到返回空串。"""
    subject = (body_prompt or "").strip()
    if not subject and sb and sb.output:
        scs = sb.output.get("scenes") or []
        if 0 <= sidx < len(scs):
            subject = str(scs[sidx].get("desc_prompt", "")).strip()
    if not subject and p and p.output:
        pl = p.output.get("prompts") or []
        if index < len(pl):
            full = pl[index].get("prompt", "")
            pre, suf = style.get("prefix", ""), style.get("suffix", "")
            subject = full[len(pre):len(full) - len(suf)] if full.startswith(pre) else full
    return subject


def _is_char_shot(index, sidx, p, sb):
    """该图是否人物镜头：优先看 P.prompts[i].has_char；其次 SB.scenes 的 has_character。
    图片与分镜一一对应(image[i]=scene[i])，按该分镜的标记决定，不再因"封面"特殊默认人物。
    决定重试是否带参考图保持主角一致。"""
    is_char = False
    if p and p.output:
        pl = p.output.get("prompts") or []
        if index < len(pl) and "has_char" in pl[index]:
            is_char = bool(pl[index]["has_char"])
    if not is_char and sb and sb.output:
        scs = sb.output.get("scenes") or []
        if 0 <= sidx < len(scs):
            is_char = bool(scs[sidx].get("has_character", False))
    return is_char


@router.post("/tasks/{task_id}/images/{index}/retry")
def retry_image(task_id: str, index: int, body: ImageRetryRequest,
                db: Session = Depends(get_db)):
    """单张图重试：只重生成第 index 张（E 产物 images 数组下标），其它图不动。
    可带新 prompt 覆盖该图提示词。复用流水线同款逻辑：提示词先净化(柔化敏感意象)，
    再带退避重试(应对内容审核误判的随机性)；仍失败则透出真实原因到该图。"""
    from app.core.security import decrypt
    from app.services.image import ImageError
    import logging
    from app.modules.image_module import _gen_with_fallback, _sanitize_imagery, _wrap, is_monochrome_style
    from app.modules import tracks

    logger = logging.getLogger("uvicorn")
    try:  # 整段包 try：任何未预期异常都转 HTTPException，不再裸抛 500
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, detail="任务不存在")
        # 单图重试同样消耗绘图额度，先校验每日成本上限（与初次生图一致）
        if cost_svc.daily_cap_reached(db):
            raise HTTPException(429, detail="E_DAILY_CAP: 今日成本已达上限，明日再试或上调上限")
        cfg = db.query(Config).first()
        # 单张重试 = 只重生这一张、按 1 张计费，不会放大成本，故豆包也放开（之前误屏蔽了）。
        # 想省成本、风格统一可改用「一起重新组图」；想精修某一张就用这里的单张换图。
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
        sidx = index  # 图片与分镜严格一一对应：image[i]=scene[i]（不再有独立封面占下标0）

        # 取「裸主体」(不含风格包裹)：优先请求里的新词，否则用 SB.scenes 的 desc_prompt。
        # 注意 P.prompts[i].prompt 是 wrap 过的完整提示词，不能直接当主体（会双重套风格）。
        subject = _resolve_subject(index, sidx, body.prompt, p, sb, style)
        if not subject:
            raise HTTPException(400, detail="缺少提示词，无法生成")

        # 净化裸主体（柔化敏感意象），再套统一风格包裹 → 最终提示词
        subject = _sanitize_imagery(subject)
        img_key = decrypt(cfg.image_api_key_enc) if cfg and cfg.image_api_key_enc else ""
        out_path = Path(img["path"])

        # 人物镜头重试也带参考图保持主角一致（同一个人、不同场景）。
        ref_uri = None
        is_char = _is_char_shot(index, sidx, p, sb)
        if is_char and task.reference_image and task.track == "character_story":
            try:
                from app.services.image import _encode_reference
                ref_uri = _encode_reference(task.reference_image)
            except Exception:
                ref_uri = None

        # 模型/地址/单价：本次请求显式指定的优先，否则跟随全局配置。
        _model = body.model or (cfg.image_model if cfg else None)
        _validate_base_url(body.base_url)
        _base_url = body.base_url or (getattr(cfg, "image_base_url", None) if cfg else None)
        _unit_price = body.unit_price if body.unit_price is not None else getattr(cfg, "image_unit_price", None)

        _grayscale = is_monochrome_style(style)
        # center_h 版式保留完整横图不裁切
        _no_crop = getattr(task, "layout", "full") == "center_h"
        logger.info("[retryImage] 单张重试: index=%d, image_style=%s, style_prefix=%s, grayscale=%s, no_crop=%s",
                    index, task.image_style, style.get("prefix", "None") if style else "None", _grayscale, _no_crop)

        def _gen(subj):
            """裸主体 → 套风格 → 生成。人物镜头带参考图图生图保持一致性。"""
            text = _wrap(style, subj)
            if ref_uri:
                text = _wrap(style, f"【重要】生成与参考图中完全相同的人物：保持此人的面部特征、五官比例、发型、肤色、性别、年龄完全不变，"
                                    f"与参考图是同一个人。仅改变以下部分——场景背景、服装、姿势、表情：{subj}。"
                                    f"绝对不要改变此人的面部外观和身份特征。")
            return _gen_with_fallback(cfg.image_provider if cfg else "mock", img_key,
                                      text,
                                      sub_type, out_path, img.get("suggested_duration", 6),
                                      model=_model,
                                      aspect_ratio=tracks.image_ratio_for(task),
                                      ref_uri=ref_uri,
                                      base_url=_base_url,
                                      proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None if cfg else None,
                                      grayscale=_grayscale,
                                      no_crop=_no_crop)

        try:
            result = _gen(subject)
        except ImageError as ex:
            raise HTTPException(502, detail=f"配图失败：{ex}")

        failed = bool(result.meta.get("fallback"))
        reason = result.meta.get("reason") if failed else None
        rewritten = False

        def _is_audit(rsn):
            low = rsn.lower() if rsn else ""
            return bool(rsn) and ("sensitive" in low or "审核" in rsn or "拒绝" in rsn
                                   or "moderation" in low or "safety" in low or "violate" in low)

        # 手动点重试 = 用户主动的一次操作，目标「点一次就出图」，但要尽量【保住人物和剧情】，
        # 不能一上来就降级成空镜（书桌/窗景那种没人物没情节、和文案脱节的画面）。
        # 故走【阶梯式】改写：第1级含蓄改写(保留人物/场景/情绪，只柔化敏感词) → 仍被拦升第2级
        # (换掉敏感物件，人物场景还在) → 最后才第3级(纯安全空镜兜底，几乎必过)。
        # 大多数被拦画面第1级就能过审且保住剧情；只有真·反复过不了审才退化成空镜。
        # 用户主动触发、最多3级有硬上限，不同于已砍掉的「后台偷偷反复烧」。
        if failed and _is_audit(reason):
            from app.modules import text_modules as tm
            llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
            if llm_key:
                base = subject
                for attempt in range(1, 4):  # 1→2→3 递进，保人物剧情优先，空镜兜底
                    try:
                        safe_subj, _ = tm.run_safe_rewrite(cfg.llm_provider, cfg.llm_model,
                                                           llm_key, base, attempt=attempt)
                        safe_subj = _sanitize_imagery(safe_subj)
                    except Exception as ex:
                        reason = reason or f"提示词安全改写失败：{ex}"
                        break
                    if not safe_subj or safe_subj == base:
                        base = safe_subj or base
                        continue
                    result = _gen(safe_subj)
                    subject = safe_subj
                    rewritten = True
                    failed = bool(result.meta.get("fallback"))
                    reason = result.meta.get("reason") if failed else None
                    if not failed or not _is_audit(reason):
                        break  # 成功，或换成非审核类错误，停止升级
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

        # 计费（重算而非累加）：单图重试替换该张图后，E 成本重算为当前完整 E 产物的实际成本，
        # 不叠加历史。这样反复重试同一张不会把 total_cost 越滚越高误判超限（成本=最终得到的图）。
        provider = cfg.image_provider if cfg else "mock"
        e_now = db.query(ModuleResult).filter_by(task_id=task_id, module="E").first()
        cur_imgs = (e_now.output or {}).get("images", []) if e_now else []
        e_cost = cost_svc.image_cost(cur_imgs, provider,
                                     getattr(cfg, "image_unit_price", None) if cfg else None,
                                     model=getattr(cfg, "image_model", None) if cfg else None)
        cost_svc.rebill_module(db, task, "E", provider, e_cost)

        # 返回：是否仍失败、原因、是否做过安全改写、改写后的主体（前端回填到输入框）
        return {"index": index, "image": img, "failed": failed, "reason": reason,
                "rewritten": rewritten, "new_prompt": subject if rewritten else None}

    except HTTPException:
        raise  # 已包装过的直接透传
    except Exception as ex:
        # 任何未预期异常（DB 连不上、decrypt 失败、下标越界、PIL 错误等）→ 500 变 4xx/明确错误
        logger.error("[retryImage] 单图重试异常: %s: %s", type(ex).__name__, ex)
        raise HTTPException(500, detail=f"单图重试内部错误：{type(ex).__name__}: {ex}")


@router.post("/tasks/{task_id}/images/batch-retry")
def batch_retry_images(task_id: str, body: ImageBatchRetryRequest,
                       db: Session = Depends(get_db)):
    """多张图一起重新组图：把选中的图（不满意的+失败占位的）合并成一次组图请求生成。
    相比逐张重试：省请求、同批生成→风格统一、人物镜头带参考图→主角一致。
    各图主体仍走净化+统一风格包裹；整批组图失败时该批回退逐张兜底。"""
    from app.core.security import decrypt
    from app.modules.image_module import (render_images_grouped, _sanitize_imagery,
                                          _wrap)
    from app.modules import tracks
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, detail="任务不存在")
    # 重试同样消耗绘图额度，先校验每日成本上限（与初次生图一致）
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(429, detail="E_DAILY_CAP: 今日成本已达上限，明日再试或上调上限")
    cfg = db.query(Config).first()
    e = db.query(ModuleResult).filter_by(task_id=task_id, module="E").first()
    p = db.query(ModuleResult).filter_by(task_id=task_id, module="P").first()
    sb = db.query(ModuleResult).filter_by(task_id=task_id, module="SB").first()
    if not e or not e.output or "images" not in e.output:
        raise HTTPException(400, detail="该任务尚无配图产物，无法组图重试")
    images = list(e.output["images"])
    # 去重 + 校验下标，按下标升序（组图分组依赖相邻顺序）
    sel = sorted({i for i in body.indices})
    if not sel:
        raise HTTPException(400, detail="未选择任何图片")
    if sel[0] < 0 or sel[-1] >= len(images):
        raise HTTPException(400, detail=f"图片下标越界（0~{len(images)-1}）")

    style = tracks.get_style(task.image_style, task.track)
    img_key = decrypt(cfg.image_api_key_enc) if cfg and cfg.image_api_key_enc else ""

    # 人物镜头参考图（同一张，供组图保持主角一致）
    ref_uri = None
    if task.reference_image and task.track == "character_story":
        try:
            from app.services.image import _encode_reference
            ref_uri = _encode_reference(task.reference_image)
        except Exception:
            ref_uri = None

    # 为每张选中图构造组图任务五元组 (prompt, sub_type, out_path, duration, ref_uri)。
    # 主体解析、人物判断复用单图重试同款 helper，保证两条路径行为一致。
    tasks, subjects = [], {}
    for index in sel:
        img = images[index]
        sidx = index  # 图片与分镜严格一一对应：image[i]=scene[i]
        subject = _resolve_subject(index, sidx, None, p, sb, style)
        if not subject:
            raise HTTPException(400, detail=f"第 {index} 张缺少提示词，无法生成")
        subject = _sanitize_imagery(subject)
        subjects[index] = subject
        is_char = _is_char_shot(index, sidx, p, sb)
        item_ref = ref_uri if is_char else None
        # 人物镜头走图生图：套「同一人物、不同画面」包裹再套风格；否则纯主体套风格。
        if item_ref:
            text = _wrap(style, f"【重要】生成与参考图中完全相同的人物：保持此人的面部特征、五官比例、发型、肤色、性别、年龄完全不变，"
                                f"与参考图是同一个人。仅改变以下部分——场景背景、服装、姿势、表情：{subject}。"
                                f"绝对不要改变此人的面部外观和身份特征。")
        else:
            text = _wrap(style, subject)
        tasks.append((text, img.get("sub_type", "content"), Path(img["path"]),
                      img.get("suggested_duration", 6), item_ref))

    # 组图生成（锁外，慢且不碰共享数据）。是否走九宫格尊重建任务时选的 image_gen_mode：
    # grid→九宫格(省成本)，per_image→逐张重组(画质优先/黑白等强约束更稳)。
    # 之前无脑按模型名强制九宫格，导致选了逐张的任务重组时被塞进九宫格、黑白失效又易连带失败。
    # 出图方式：本次请求显式指定的 gen_mode 优先，否则跟随建任务时选的 image_gen_mode。
    # 让用户在画廊当场切换——九宫格省钱、逐张画质优先(黑白等强约束更稳)。
    _mode = (body.gen_mode or getattr(task, "image_gen_mode", None) or "per_image")
    if _mode not in ("grid", "per_image"):
        _mode = "per_image"
    _grid = (_mode == "grid")
    # 手动重组 = 用户主动的一次操作，目标「点一次就出图」，但要尽量【保住人物和剧情】。
    # 九宫格/组图被审核拦截时，按 render_images_grouped 内部的阶梯(attempt=1→2)逐级改写：
    # 第1级含蓄改写保留人物场景 → 仍被拦才第2级换敏感物件。不一上来就降级成空镜，
    # 避免画面和文案脱节。有硬上限(内部最多2次改写)，是用户触发的救援，
    # 不同于已砍掉的「后台首次生成偷偷反复烧」。无 LLM Key 则不改写。
    from app.modules import text_modules as _tm
    _llm_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
    # 模型/地址/单价：本次请求显式指定的优先，否则跟随全局配置。
    _model = body.model or (cfg.image_model if cfg else None)
    _validate_base_url(body.base_url)
    _base_url = body.base_url or (getattr(cfg, "image_base_url", None) if cfg else None)
    _unit_price = body.unit_price if body.unit_price is not None else getattr(cfg, "image_unit_price", None)

    def _grid_rewrite(brief, attempt):
        try:
            # 用传入的真实 attempt，走阶梯式改写（保人物剧情优先），不一步跳到空镜。
            safe, _ = _tm.run_safe_rewrite(cfg.llm_provider, cfg.llm_model,
                                           _llm_key, brief, attempt=attempt)
            return safe or brief
        except Exception:
            return brief
    results = render_images_grouped(cfg.image_provider if cfg else "mock", img_key,
                                    tasks, model=_model,
                                    aspect_ratio=tracks.image_ratio_for(task),
                                    grid_mode=_grid, style=style,
                                    base_url=_base_url,
                                    proxy=(getattr(cfg, "proxy_url", None) or "").strip() or None if cfg else None,
                                    rewrite_fn=_grid_rewrite if _llm_key else None,
                                    no_crop=getattr(task, "layout", "full") == "center_h")

    # 回写：read-modify-write E/P/SB，必须在 per-task 锁内重读最新产物再改选中的几张，
    # 避免与并发的单图重试互相覆盖（丢失更新）。
    out = []
    with _get_retry_lock(task_id):
        e2 = db.query(ModuleResult).filter_by(task_id=task_id, module="E").first()
        p2 = db.query(ModuleResult).filter_by(task_id=task_id, module="P").first()
        sb2 = db.query(ModuleResult).filter_by(task_id=task_id, module="SB").first()
        eo = dict(e2.output) if e2 and e2.output else None
        imgs = list(eo["images"]) if eo and "images" in eo else None
        po = dict(p2.output) if p2 and p2.output else None
        sbo = dict(sb2.output) if sb2 and sb2.output else None
        for k, index in enumerate(sel):
            result = results[k]
            sidx = index
            subject = subjects[index]
            failed = bool(result.meta.get("fallback"))
            reason = result.meta.get("reason") if failed else None
            if imgs is not None and 0 <= index < len(imgs):
                fi = dict(imgs[index])
                fi["path"] = result.path
                fi["fallback"] = failed
                fi["grid"] = bool(result.meta.get("grid"))  # 供计费按 ceil/9 折算
                if reason:
                    fi["fail_reason"] = reason
                elif "fail_reason" in fi:
                    del fi["fail_reason"]
                imgs[index] = fi
            # 主体写回 SB.scenes / P.scenes；wrap 过的完整提示词写回 P.prompts
            if sbo is not None:
                scs = list(sbo.get("scenes") or [])
                if 0 <= sidx < len(scs):
                    scs[sidx] = {**scs[sidx], "desc_prompt": subject}
                    sbo["scenes"] = scs
            if po is not None:
                pl = list(po.get("prompts") or [])
                if index < len(pl):
                    pl[index] = {**pl[index], "prompt": _wrap(style, subject)}
                    po["prompts"] = pl
                pscs = list(po.get("scenes") or [])
                if 0 <= sidx < len(pscs):
                    pscs[sidx] = {**pscs[sidx], "desc_prompt": subject}
                    po["scenes"] = pscs
            out.append({"index": index, "failed": failed, "reason": reason})
        if eo is not None and imgs is not None:
            eo["images"] = imgs
            e2.output = eo
        if sb2 and sbo is not None:
            sb2.output = sbo
        if p2 and po is not None:
            p2.output = po
        db.commit()
        final_imgs = imgs if imgs is not None else images

    # 计费（重算而非累加）：E 成本始终等于当前完整产物 final_imgs 的实际成本，
    # 重新组图替换旧图、不叠加历史账，避免反复重组把 total_cost 越滚越高误判超限。
    # 九宫格按 grid 标记折算 ceil(张数/9)、一次请求出9张只算1张钱；逐张按实际张数。
    provider = cfg.image_provider if cfg else "mock"
    e_cost = cost_svc.image_cost(final_imgs, provider, _unit_price, model=_model)
    total = cost_svc.rebill_module(db, task, "E", provider, e_cost)

    for r in out:
        r["image"] = final_imgs[r["index"]]
    return {"results": out, "count": len(out), "cost": round(e_cost, 4), "total_cost": round(total, 4)}


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
    if task.status == "processing":
        raise HTTPException(400, detail="任务正在处理中，请稍后再试")
    if cost_svc.daily_cap_reached(db):
        raise HTTPException(402, detail="E2003: 每日成本上限已达")
    # 清掉该步及其下游产物和对应 CostLog，上游保留 → resume 时上游命中缓存、从该步真正重算
    # BUG-M: 不清 CostLog 会导致 total_cost 虚高，_check_limits 误判超支
    downstream_modules = _DOWNSTREAM[module]
    db.query(ModuleResult).filter(
        ModuleResult.task_id == task_id,
        ModuleResult.module.in_(downstream_modules)).delete(synchronize_session=False)
    db.query(CostLog).filter(
        CostLog.task_id == task_id,
        CostLog.module.in_(downstream_modules)).delete(synchronize_session=False)
    db.flush()
    from sqlalchemy import func as _func
    total = db.query(_func.coalesce(_func.sum(CostLog.cost), 0)).filter(
        CostLog.task_id == task_id).scalar()
    task.total_cost = float(total or 0)
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
