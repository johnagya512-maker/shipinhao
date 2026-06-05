"""API 鉴权。本机模式豁免，配置 access_token 后校验 Bearer。"""
from fastapi import Header, HTTPException
from app.core.config import settings


def require_auth(authorization: str | None = Header(default=None)):
    """access_token 为空=本机模式，放行；否则校验 Bearer token。"""
    if not settings.access_token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    token = authorization.split(" ", 1)[1]
    if token != settings.access_token:
        raise HTTPException(status_code=401, detail="访问令牌无效")
