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
from app.core.security import decrypt
from app.services import cost as cost_svc
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
                                                 monetization_mode=task.monetization_mode),
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
            out_dir = settings.storage_dir / task.id / "images"
            existing_e = _get_result(db, task.id, "E")
            if not (existing_e and existing_e.status == "success"):
                images, _ = with_retry(
                    lambda: im.run_images(cfg.image_provider, img_key, book_info, segments, out_dir,
                                          track=task.track, image_style=task.image_style),
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

        # 文案+配图完成，等待音频上传触发 G
        task.status = "awaiting_audio"
        db.commit()

    except TaskAborted as e:
        _fail(db, task, e.code, e.message)
    except Exception as e:
        _fail(db, task, "E5001", str(e))


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
