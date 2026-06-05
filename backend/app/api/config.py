"""配置中心路由。API Key 加密存储，返回掩码。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import encrypt, decrypt, mask
from app.api.auth import require_auth
from app.api.schemas import ConfigUpdate, ConfigOut
from app.models import Config
from app.services.llm import call_llm, LLMError

router = APIRouter(prefix="/api/v1/config", dependencies=[Depends(require_auth)])


def _get_or_create(db: Session) -> Config:
    cfg = db.get(Config, 1)
    if not cfg:
        cfg = Config(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def _to_out(cfg: Config) -> ConfigOut:
    return ConfigOut(
        llm_provider=cfg.llm_provider,
        llm_model=cfg.llm_model,
        llm_api_key_mask=mask(decrypt(cfg.llm_api_key_enc)) if cfg.llm_api_key_enc else "",
        image_provider=cfg.image_provider,
        image_api_key_mask=mask(decrypt(cfg.image_api_key_enc)) if cfg.image_api_key_enc else "",
        daily_cost_cap=float(cfg.daily_cost_cap),
        concurrency=cfg.concurrency,
    )


@router.get("", response_model=ConfigOut)
def get_config(db: Session = Depends(get_db)):
    return _to_out(_get_or_create(db))


@router.put("", response_model=ConfigOut)
def update_config(body: ConfigUpdate, db: Session = Depends(get_db)):
    cfg = _get_or_create(db)
    if body.llm_provider is not None:
        cfg.llm_provider = body.llm_provider
    if body.llm_model is not None:
        cfg.llm_model = body.llm_model
    if body.llm_api_key:
        cfg.llm_api_key_enc = encrypt(body.llm_api_key)
    if body.image_provider is not None:
        cfg.image_provider = body.image_provider
    if body.image_api_key:
        cfg.image_api_key_enc = encrypt(body.image_api_key)
    if body.daily_cost_cap is not None:
        cfg.daily_cost_cap = body.daily_cost_cap
    if body.concurrency is not None:
        cfg.concurrency = body.concurrency
    db.commit()
    return _to_out(cfg)


@router.post("/test-api")
def test_api(db: Session = Depends(get_db)):
    """测试 LLM API Key 连通性。"""
    cfg = _get_or_create(db)
    if not cfg.llm_api_key_enc:
        raise HTTPException(400, detail="E2001: 未配置 LLM API Key")
    key = decrypt(cfg.llm_api_key_enc)
    try:
        r = call_llm(cfg.llm_provider, cfg.llm_model, key, "回复 OK 两个字", timeout=15.0)
        return {"ok": True, "reply": r.text[:50]}
    except LLMError as e:
        raise HTTPException(400, detail=f"E2001: {e}")
