"""配置中心路由。API Key 加密存储，返回掩码。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import encrypt, decrypt, mask
from app.api.auth import require_auth
from app.api.schemas import ConfigUpdate, ConfigOut
from app.models import Config
from app.services.llm import call_llm, LLMError
from app.services import tts as tts_svc

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
        image_model=cfg.image_model,
        image_api_key_mask=mask(decrypt(cfg.image_api_key_enc)) if cfg.image_api_key_enc else "",
        collect_provider=cfg.collect_provider,
        collect_api_key_mask=mask(decrypt(cfg.collect_api_key_enc)) if cfg.collect_api_key_enc else "",
        asr_provider=cfg.asr_provider,
        asr_api_key_mask=mask(decrypt(cfg.asr_api_key_enc)) if cfg.asr_api_key_enc else "",
        tts_provider=cfg.tts_provider,
        tts_api_key_mask=mask(decrypt(cfg.tts_api_key_enc)) if cfg.tts_api_key_enc else "",
        tts_voice=cfg.tts_voice or "",
        tts_appid=cfg.tts_appid or "",
        daily_cost_cap=float(cfg.daily_cost_cap),
        concurrency=cfg.concurrency,
        jianying_draft_dir=cfg.jianying_draft_dir or "",
        task_storage_dir=cfg.task_storage_dir or "",
        bgm_dir=cfg.bgm_dir or "",
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
    if body.image_model is not None:
        cfg.image_model = body.image_model
    if body.image_api_key:
        cfg.image_api_key_enc = encrypt(body.image_api_key)
    if body.collect_provider is not None:
        cfg.collect_provider = body.collect_provider
    if body.collect_api_key:
        cfg.collect_api_key_enc = encrypt(body.collect_api_key)
    if body.asr_provider is not None:
        cfg.asr_provider = body.asr_provider
    if body.asr_api_key:
        cfg.asr_api_key_enc = encrypt(body.asr_api_key)
    if body.tts_provider is not None:
        cfg.tts_provider = body.tts_provider
    if body.tts_api_key:
        cfg.tts_api_key_enc = encrypt(body.tts_api_key)
    if body.tts_voice is not None:
        cfg.tts_voice = body.tts_voice
    if body.tts_appid is not None:
        cfg.tts_appid = body.tts_appid
    if body.daily_cost_cap is not None:
        cfg.daily_cost_cap = body.daily_cost_cap
    if body.concurrency is not None:
        cfg.concurrency = body.concurrency
    if body.jianying_draft_dir is not None:
        cfg.jianying_draft_dir = body.jianying_draft_dir.strip() or None
    if body.task_storage_dir is not None:
        cfg.task_storage_dir = body.task_storage_dir.strip() or None
    if body.bgm_dir is not None:
        cfg.bgm_dir = body.bgm_dir.strip() or None
    db.commit()
    return _to_out(cfg)


@router.get("/bgm-list")
def bgm_list(db: Session = Depends(get_db)):
    """列出 BGM 目录下的音频文件名（mp3/wav/m4a）。未配置目录则返回空列表。"""
    from pathlib import Path
    cfg = _get_or_create(db)
    d = (cfg.bgm_dir or "").strip()
    if not d:
        return {"dir": "", "files": []}
    p = Path(d)
    if not p.is_dir():
        return {"dir": d, "files": []}
    exts = {".mp3", ".wav", ".m4a", ".aac"}
    files = sorted([f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in exts])
    return {"dir": d, "files": files}


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


@router.post("/test-tts")
def test_tts(db: Session = Depends(get_db)):
    """测试 TTS 配置连通性：用当前 TTS Key 合成一句短文本验证。"""
    cfg = _get_or_create(db)
    if not cfg.tts_api_key_enc:
        raise HTTPException(400, detail="E6200: 未配置 TTS API Key")
    key = decrypt(cfg.tts_api_key_enc)
    try:
        size = tts_svc.test_connectivity(cfg.tts_provider, key,
                                         voice=cfg.tts_voice, appid=cfg.tts_appid)
        return {"ok": True, "provider": cfg.tts_provider, "audio_bytes": size}
    except tts_svc.TTSUnavailable as e:
        raise HTTPException(400, detail=f"E6200: {e}")
    except tts_svc.TTSError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/preview-tts")
def preview_tts(body: dict | None = None, db: Session = Depends(get_db)):
    """试听：用指定音色/语速合成一句短文本，直接返回 mp3 音频流供前端播放。
    body 可选 {voice, speed}，缺省用配置中的音色与正常语速。"""
    from fastapi import Response
    cfg = _get_or_create(db)
    if not cfg.tts_api_key_enc:
        raise HTTPException(400, detail="E6200: 未配置 TTS API Key")
    key = decrypt(cfg.tts_api_key_enc)
    body = body or {}
    voice = body.get("voice") or cfg.tts_voice
    speed = body.get("speed", 1.0)
    try:
        audio = tts_svc.synth_preview(cfg.tts_provider, key, voice=voice,
                                      appid=cfg.tts_appid, speed=speed)
        return Response(content=audio, media_type="audio/mpeg")
    except tts_svc.TTSUnavailable as e:
        raise HTTPException(400, detail=f"E6200: {e}")
    except tts_svc.TTSError as e:
        raise HTTPException(400, detail=str(e))
