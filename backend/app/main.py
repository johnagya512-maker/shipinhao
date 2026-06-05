"""FastAPI 主入口。"""
import logging
from fastapi import FastAPI
from app.core.config import settings
from app.core.database import init_db
from app.api import tasks, config as config_router

logger = logging.getLogger("uvicorn")

app = FastAPI(title="视频号图书带货AI工作流系统", version="1.1")


@app.on_event("startup")
def _startup():
    init_db()
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
