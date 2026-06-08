"""任务编排引擎。串联各模块，管理状态机、成本与超时（PRD 5.3/9.x/11.x）。

执行顺序：A 清洗 → B 改写 → H 合规 → (D 识别) → E 配图 → F 分段。
G 视频合成在用户上传音频后单独触发（见 services/compose）。
"""
import time
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

from app.models import Task, ModuleResult, Config
from app.core.config import settings
from app.core.paths import storage_root
from app.core.security import decrypt
from app.services import cost as cost_svc
from app.services import collect as collect_svc
from app.services import asr as asr_svc
from app.services import tts as tts_svc
from app.modules import text_modules as tm
from app.modules import image_module as im
from app.modules.retry import with_retry

LLM_RETRY = 2
IMAGE_RETRY = 2


class TaskAborted(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _get_result(db: Session, task_id: str, module: str) -> ModuleResult | None:
    return db.query(ModuleResult).filter_by(task_id=task_id, module=module).first()


def _save_result(db: Session, task_id: str, module: str, status: str,
                 output=None, cost=0.0, tokens_in=None, tokens_out=None, retry=0):
    mr = _get_result(db, task_id, module)
    if not mr:
        mr = ModuleResult(task_id=task_id, module=module)
        db.add(mr)
    mr.status = status
    mr.output = output
    mr.cost = cost
    mr.tokens_in = tokens_in
    mr.tokens_out = tokens_out
    mr.retry_count = retry
    mr.finished_at = datetime.utcnow()
    db.commit()
    return mr


def _check_limits(db: Session, task: Task, started: float):
    """超时与成本上限检查（PRD 11.2）。"""
    if time.time() - started > task.time_limit:
        raise TaskAborted("TIMEOUT", "处理超时")
    if float(task.total_cost) > float(task.cost_limit):
        raise TaskAborted("COST_EXCEEDED", "成本超过上限")


def _llm_step(db, task, cfg, llm_key, module, fn, started):
    """执行一个 LLM 模块：断点复用 + 重试 + 计费 + 限额检查。返回 output。"""
    existing = _get_result(db, task.id, module)
    if existing and existing.status == "success":
        return existing.output  # 断点续跑：复用已成功结果，不重复扣费

    def _do():
        return fn()

    (output, llm_result), attempts = with_retry(_do, LLM_RETRY)
    c = cost_svc.actual_llm_cost(llm_result.tokens_in, llm_result.tokens_out, cfg.llm_provider)
    cost_svc.record_cost(db, task.id, module, cfg.llm_provider, c)
    task.total_cost = float(task.total_cost) + c
    db.commit()
    _save_result(db, task.id, module, "success", output=output, cost=c,
                 tokens_in=llm_result.tokens_in, tokens_out=llm_result.tokens_out, retry=attempts)
    _check_limits(db, task, started)
    return output


def run_pipeline(db: Session, task_id: str):
    """执行文案+配图流水线（不含 G，G 在音频上传后触发）。"""
    task = db.get(Task, task_id)
    cfg = db.get(Config, 1)
    if not task or not cfg:
        return
    if cost_svc.daily_cap_reached(db):
        _fail(db, task, "E2003", "每日成本上限已达")
        return

    llm_key = decrypt(cfg.llm_api_key_enc) if cfg.llm_api_key_enc else ""
    img_key = decrypt(cfg.image_api_key_enc) if cfg.image_api_key_enc else ""
    started = time.time()
    task.status = "processing"
    db.commit()

    try:
        # 前置：抖音采集 + ASR（仅当提供了 douyin_url）。
        # 无对应 Key 时降级——要求 transcript 已手填，否则失败提示。
        if task.douyin_url and not (task.transcript or "").strip():
            _run_collect_asr(db, task, cfg, started)

        if not (task.transcript or "").strip():
            raise TaskAborted("E6001", "无逐字稿：请填写抖音链接（已配采集/ASR Key）或手动粘贴逐字稿")

        # 链接模式下提交时按占位长度估算；ASR 拿到真稿后按真实长度复估，
        # 超限则在烧 A/B 等 LLM 成本前快速失败（避免半途触发运行时闸门）。
        if task.douyin_url:
            real_est = cost_svc.estimate_cost(task.transcript, task.modules, None,
                                              cfg.llm_provider, cfg.image_provider)
            if real_est > float(task.cost_limit):
                raise TaskAborted("E2002",
                                  f"采集转写后预估成本 {real_est} 元超过上限 {task.cost_limit} 元")

        # A 清洗
        a_out = _llm_step(db, task, cfg, llm_key, "A",
                          lambda: tm.run_clean(cfg.llm_provider, cfg.llm_model, llm_key,
                                               task.transcript, task.keyword, task.title, task.author),
                          started)
        cleaned = a_out["cleaned_text"]

        # keyword 兜底提取（PRD 9.2）：未填则用 A 输出首句近似
        if not task.keyword:
            task.keyword = cleaned[:8]
            db.commit()

        # B 改写
        b_out = _llm_step(db, task, cfg, llm_key, "B",
                          lambda: tm.run_rewrite(cfg.llm_provider, cfg.llm_model, llm_key,
                                                 cleaned, task.target_audience, task.title,
                                                 track=task.track,
                                                 monetization_mode=task.monetization_mode,
                                                 rewrite_strength=task.rewrite_strength,
                                                 narrative_perspective=task.narrative_perspective),
                          started)
        script = b_out["script"]

        # H 合规闸门（强制，按赛道词库）
        h_out = _llm_step(db, task, cfg, llm_key, "H",
                          lambda: tm.run_compliance(cfg.llm_provider, cfg.llm_model, llm_key,
                                                    script, track=task.track),
                          started)
        if not h_out["passed"]:
            task.status = "blocked"
            task.error_code = "E4002"
            task.error_message = "合规检查未通过"
            db.commit()
            return

        # F 分段（必选）
        f_out = _llm_step(db, task, cfg, llm_key, "F",
                          lambda: tm.run_split(cfg.llm_provider, cfg.llm_model, llm_key,
                                               script, task.keyword, task.title),
                          started)
        segments = f_out["segments"]

        # D 识别（可选，失败跳过）
        book_info = None
        if "D" in task.modules:
            try:
                d_out = _llm_step(db, task, cfg, llm_key, "D",
                                  lambda: tm.run_identify(cfg.llm_provider, cfg.llm_model, llm_key, script),
                                  started)
                book_info = im.pick_main_book(d_out["books"])
            except Exception as e:
                _save_result(db, task.id, "D", "failed", output={"error": str(e)})

        # E 配图（必选）
        if "E" in task.modules:
            out_dir = storage_root(db) / task.id / "images"
            existing_e = _get_result(db, task.id, "E")
            if not (existing_e and existing_e.status == "success"):
                # 张数按改写后文案字数自动匹配（约 5 字/秒口播、6 秒/张），
                # 不依赖 F 分段数（其段数/估时波动大），保证节奏稳定。
                est_dur = len(script) / cost_svc.CHARS_PER_SECOND
                n_images = im.count_for_duration(est_dur)
                images, _ = with_retry(
                    lambda: im.run_images(cfg.image_provider, img_key, book_info, segments, out_dir,
                                          image_count=n_images,
                                          track=task.track, image_style=task.image_style,
                                          model=cfg.image_model,
                                          concurrency=cfg.concurrency,
                                          aspect_ratio=task.aspect_ratio,
                                          reference_image=task.reference_image),
                    IMAGE_RETRY)
                img_cost = cost_svc.IMAGE_PRICE.get(cfg.image_provider, 0.1) * len(images)
                cost_svc.record_cost(db, task.id, "E", cfg.image_provider, img_cost)
                task.total_cost = float(task.total_cost) + img_cost
                db.commit()
                _save_result(db, task.id, "E", "success",
                             output={"images": [{"path": r.path, "sub_type": r.sub_type,
                                                 "suggested_duration": r.suggested_duration} for r in images]},
                             cost=img_cost)
                _check_limits(db, task, started)

        # F 分段完成后：尝试自动 TTS 配音 → 自动成片。
        # 无 TTS Key 时降级为 awaiting_audio，等用户手动上传音频。
        tts_key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
        audio_path = None
        if tts_key:
            try:
                audio_dir = storage_root(db) / task.id / "audio"
                existing_tts = _get_result(db, task.id, "T")
                if existing_tts and existing_tts.status == "success":
                    audio_path = existing_tts.output.get("audio_path")
                else:
                    r = tts_svc.synthesize(segments, cfg.tts_provider, tts_key,
                                           audio_dir, voice=(task.voice or cfg.tts_voice),
                                           appid=cfg.tts_appid,
                                           speed=float(task.voice_speed or 1.0))
                    audio_path = r.audio_path
                    _save_result(db, task.id, "T", "success",
                                 output={"audio_path": r.audio_path, "duration": r.duration,
                                         "segment_count": r.segment_count})
            except tts_svc.TTSUnavailable:
                audio_path = None
            except Exception as e:
                _save_result(db, task.id, "T", "failed", output={"error": str(e)})
                audio_path = None

        if audio_path:
            # 自动成片（剪映草稿模式）。compose 内部会把 task 置为 completed。
            from app.services import compose as compose_svc
            # BGM 路径：task.bgm（文件名）+ cfg.bgm_dir（目录）拼成绝对路径，供 mp4 混音。
            bgm_path = None
            if task.bgm and (cfg.bgm_dir or "").strip():
                from pathlib import Path as _P
                cand = _P(cfg.bgm_dir) / task.bgm
                if cand.is_file():
                    bgm_path = str(cand)
            compose_svc.compose_video(db, task.id, audio_path,
                                      task.enable_subtitles, task.enable_animations,
                                      output_mode="jianying", bgm_path=bgm_path)
        else:
            # 降级：文案+配图完成，等待音频上传触发 G
            task.status = "awaiting_audio"
            db.commit()

    except TaskAborted as e:
        _fail(db, task, e.code, e.message)
    except Exception as e:
        _fail(db, task, "E5001", str(e))


def _run_collect_asr(db: Session, task: Task, cfg: Config, started: float):
    """前置采集 + ASR：抖音链接 → 元数据 + 原始逐字稿，写入 task。

    采集/ASR 任一未配 Key 时静默降级（不抛错），让后续逻辑回落到
    "要求手填 transcript"。仅在真正调用且失败时记错误结果。
    """
    collect_key = decrypt(cfg.collect_api_key_enc) if cfg.collect_api_key_enc else ""
    asr_key = decrypt(cfg.asr_api_key_enc) if cfg.asr_api_key_enc else ""

    # 采集：拿元数据 + 无水印视频地址
    video_url = ""
    try:
        cr = collect_svc.fetch_douyin(task.douyin_url, cfg.collect_provider, collect_key)
        video_url = cr.video_url
        task.source_meta = {"title": cr.title, "author": cr.author,
                            "play_count": cr.play_count, "digg_count": cr.digg_count,
                            **cr.raw_meta}
        if cr.title and not task.title:
            task.title = cr.title[:200]
        if cr.author and not task.author:
            task.author = cr.author[:100]
        db.commit()
        _save_result(db, task.id, "S", "success",
                     output={"title": cr.title, "author": cr.author,
                             "play_count": cr.play_count, "video_url": bool(video_url)})
    except collect_svc.CollectUnavailable:
        return  # 未配采集 Key，降级
    except Exception as e:
        _save_result(db, task.id, "S", "failed", output={"error": str(e)})
        return

    # ASR：视频 → 逐字稿
    try:
        ar = asr_svc.transcribe_url(video_url, cfg.asr_provider, asr_key)
        if ar.text.strip():
            task.transcript = ar.text.strip()
            db.commit()
            _save_result(db, task.id, "R", "success",
                         output={"chars": len(ar.text), "preview": ar.text[:100]})
    except asr_svc.ASRUnavailable:
        return  # 未配 ASR Key，降级
    except Exception as e:
        _save_result(db, task.id, "R", "failed", output={"error": str(e)})


def _fail(db, task, code, message):
    # 上一步异常可能使会话处于待回滚状态，先回滚再写失败状态。
    try:
        db.rollback()
    except Exception:
        pass
    task = db.get(Task, task.id)
    if not task:
        return
    task.status = "failed"
    task.error_code = code
    task.error_message = message[:500]
    db.commit()
