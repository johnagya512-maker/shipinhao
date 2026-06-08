"""FastAPI 主入口。"""
import logging
import logging.handlers
from pathlib import Path
from fastapi import FastAPI
from app.core.config import settings, _DATA_DIR
from app.core.database import init_db
from app.api import tasks, config as config_router

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
    init_db()
    # 开发便利：把 .env 里的种子 Key 写进数据库（仅填空项），免去每次测试重输。
    from app.core.security import seed_keys_from_env
    seed_keys_from_env()
    if not settings.encryption_key:
        logger.warning("未设置 APP_ENCRYPTION_KEY，使用临时开发密钥，重启后已存密钥将无法解密。"
                       "生产环境请通过环境变量注入。")
    if settings.host == "0.0.0.0" and not settings.access_token:
        logger.warning("服务监听 0.0.0.0 但未设置 APP_ACCESS_TOKEN，接口将无鉴权暴露，存在风险。")


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(tasks.router)
app.include_router(config_router.router)


@app.exception_handler(Exception)
async def _log_unhandled(request, exc):
    """未捕获异常落盘完整堆栈，再返回 500。否则桌面端只看到 Internal Server Error，
    看不到真正原因（如本次的 no such column）。"""
    from fastapi.responses import JSONResponse
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
