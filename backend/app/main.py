"""FastAPI 主入口。"""
import logging
import logging.handlers
from pathlib import Path
from fastapi import FastAPI
from app.core.config import settings, _DATA_DIR
from app.core.database import init_db
from app.api import tasks, config as config_router, config_backup

logger = logging.getLogger("uvicorn")


def _setup_file_logging():
    """把后端日志(含未捕获异常堆栈)落到数据目录 logs/backend.log，轮转保存。
    桌面端关掉后也能事后翻查任务失败/接口报错的完整堆栈。"""
    log_dir = Path(_DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "backend.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh.setLevel(logging.INFO)
    # 挂到 root，覆盖 uvicorn / sqlalchemy / app 各 logger，500 堆栈也会落盘。
    root = logging.getLogger()
    root.addHandler(fh)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    logger.info("文件日志已启用: %s", log_dir / "backend.log")


_setup_file_logging()

app = FastAPI(title="视频号图书带货AI工作流系统", version="1.1")


@app.on_event("startup")
def _startup():
    # 数据库健康检查与自动备份
    from app.core.db_health import init_database_health_check
    init_database_health_check()

    init_db()

    # 配置自动备份（用户无感知）
    from app.core.config_manager import auto_backup_config, restore_config_if_empty
    auto_backup_config()
    restore_config_if_empty()

    _recover_orphan_tasks()
    _init_scheduler()
    # 开发便利：把 .env 里的种子 Key 写进数据库（仅填空项），免去每次测试重输。
    from app.core.security import seed_keys_from_env
    seed_keys_from_env()
    if not settings.encryption_key:
        logger.warning("未设置 APP_ENCRYPTION_KEY，使用临时开发密钥，重启后已存密钥将无法解密。"
                       "生产环境请通过环境变量注入。")
    if settings.host == "0.0.0.0" and not settings.access_token:
        logger.warning("服务监听 0.0.0.0 但未设置 APP_ACCESS_TOKEN，接口将无鉴权暴露，存在风险。")
    _check_decryptable_keys()


@app.on_event("shutdown")
def _shutdown():
    """应用关闭时安全清理"""
    from app.core.db_health import shutdown_database_safely
    shutdown_database_safely()


def _recover_orphan_tasks():
    """启动自愈：把卡在 pending/processing 的「僵尸任务」标记为 failed。
    这两种状态依赖后台进程存活，后端被强杀（崩溃/重启/更新）时进程没了，
    任务却永远停在运行中，前端既不能重跑也无法继续。重启时一次性回收，
    标成 failed 后用户即可直接重跑（已完成步骤走缓存，不重复扣费）。
    awaiting_audio/awaiting_confirm 是等用户操作的持久态，重启后仍可继续，不动。"""
    from app.core.database import SessionLocal
    from app.models import Task
    db = SessionLocal()
    try:
        orphans = db.query(Task).filter(Task.status.in_(("pending", "processing"))).all()
        for t in orphans:
            t.status = "failed"
            t.error_code = "E_INTERRUPTED"
            t.error_message = "任务执行被中断（后端重启或崩溃），可点击重新运行继续。"
        if orphans:
            db.commit()
            logger.warning("启动自愈：回收 %d 个被中断的任务为 failed，可重跑。", len(orphans))
    except Exception:
        logger.exception("启动自愈回收僵尸任务失败")
        db.rollback()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


def _check_decryptable_keys():
    """启动自检：检测「密文字段非空但解密为空」的密钥。
    换机器/重装/加密主密钥(APP_ENCRYPTION_KEY)变更会导致旧密文解不开，decrypt 静默
    返回空串——表现为配置看似已填、实际所有 API 调用(LLM/生图/TTS)都拿到空 Key 而失败。
    这里启动时一次性告警，避免等用户点试听/跑任务才暴露。"""
    from app.core.database import SessionLocal
    from app.models import Config
    from app.core.security import decrypt
    fields = {
        "llm_api_key_enc": "文案模型(LLM)",
        "image_api_key_enc": "AI 绘图",
        "collect_api_key_enc": "素材采集",
        "asr_api_key_enc": "语音识别(ASR)",
        "tts_api_key_enc": "TTS 配音",
    }
    db = SessionLocal()
    try:
        cfg = db.query(Config).first()
        if not cfg:
            return
        broken = [name for f, name in fields.items()
                  if getattr(cfg, f, None) and not decrypt(getattr(cfg, f))]
        if broken:
            logger.warning(
                "检测到 %d 个密钥解密失败：%s。可能更换了加密主密钥(APP_ENCRYPTION_KEY)或迁移了数据库，"
                "旧密文已无法解开，相关功能不可用。请到配置页重新填写这些 Key。",
                len(broken), "、".join(broken))
    except Exception:
        logger.exception("启动自检密钥解密状态失败")
    finally:
        db.close()


def _init_scheduler():
    """启动时把任务并发上限按配置校正（默认 3）。"""
    from app.core.database import SessionLocal
    from app.models import Config
    from app.services.scheduler import scheduler
    db = SessionLocal()
    try:
        cfg = db.get(Config, 1)
        n = getattr(cfg, "max_concurrent_tasks", None) if cfg else None
        if n:
            scheduler.set_max(int(n))
            logger.info("任务并发上限初始化为 %d", int(n))
    except Exception:
        logger.exception("初始化任务调度器并发上限失败")
    finally:
        db.close()


app.include_router(tasks.router)
app.include_router(config_router.router)
app.include_router(config_backup.router)


@app.exception_handler(Exception)
async def _log_unhandled(request, exc):
    """未捕获异常落盘完整堆栈，再返回 500。否则桌面端只看到 Internal Server Error，
    看不到真正原因（如本次的 no such column）。"""
    from fastapi.responses import JSONResponse
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
