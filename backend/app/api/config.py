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
from app.services import voices as voices_svc

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
        vision_model=cfg.vision_model,
        collect_provider=cfg.collect_provider,
        collect_api_key_mask=mask(decrypt(cfg.collect_api_key_enc)) if cfg.collect_api_key_enc else "",
        asr_provider=cfg.asr_provider,
        asr_api_key_mask=mask(decrypt(cfg.asr_api_key_enc)) if cfg.asr_api_key_enc else "",
        tts_provider=cfg.tts_provider,
        tts_api_key_mask=mask(decrypt(cfg.tts_api_key_enc)) if cfg.tts_api_key_enc else "",
        tts_voice=cfg.tts_voice or "",
        tts_appid=cfg.tts_appid or "",
        tts_favorites=getattr(cfg, "tts_favorites", None) or [],
        daily_cost_cap=float(cfg.daily_cost_cap),
        concurrency=cfg.concurrency,
        max_concurrent_tasks=getattr(cfg, "max_concurrent_tasks", 3) or 3,
        jianying_draft_dir=cfg.jianying_draft_dir or "",
        task_storage_dir=cfg.task_storage_dir or "",
        bgm_dir=cfg.bgm_dir or "",
        proxy_url=getattr(cfg, "proxy_url", None) or "",
    )


@router.get("", response_model=ConfigOut)
def get_config(db: Session = Depends(get_db)):
    return _to_out(_get_or_create(db))


@router.get("/voices")
def list_voices(db: Session = Depends(get_db)):
    """音色库：返回分类元信息 + 候选音色清单，每个带 available（按当前火山凭证探活）。
    available: True=可用 / False=未授权 / None=尚未探出（首次访问后台异步探活，稍后重取即有）。
    可用性按火山账号授权而定，库是候选清单。"""
    cfg = _get_or_create(db)
    key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
    # 触发/复用当前凭证的后台探活；立即取已探出的结果合并进返回。
    voices_svc.ensure_probe(cfg.tts_provider, cfg.tts_appid, key)
    avail = voices_svc.availability(cfg.tts_appid, key)
    voices = [{**v, "available": avail.get(v["id"])} for v in voices_svc.VOICE_LIBRARY]
    return {"categories": voices_svc.CATEGORIES, "voices": voices,
            "probing": bool(key) and not avail}


@router.get("/draft-templates")
def list_draft_templates():
    """草稿动画模板清单：返回 [{key, name, desc}]，供创建页选择。"""
    from app.modules import draft_templates
    return {"templates": draft_templates.list_templates()}


@router.put("/favorites")
def update_favorites(body: dict, db: Session = Depends(get_db)):
    """收藏/取消收藏音色。body: {voice_id, action: 'add'|'remove'}。返回最新收藏列表。"""
    voice_id = (body or {}).get("voice_id")
    action = (body or {}).get("action")
    if not voice_id or action not in ("add", "remove"):
        raise HTTPException(400, detail="参数错误：需要 voice_id 和 action(add/remove)")
    cfg = _get_or_create(db)
    favs = list(getattr(cfg, "tts_favorites", None) or [])
    if action == "add":
        if voice_id not in favs:
            favs.append(voice_id)
    else:
        favs = [v for v in favs if v != voice_id]
    cfg.tts_favorites = favs
    db.commit()
    return {"favorites": favs}


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
    if body.vision_model is not None:
        cfg.vision_model = body.vision_model
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
    if body.max_concurrent_tasks is not None:
        cfg.max_concurrent_tasks = body.max_concurrent_tasks
        from app.services.scheduler import scheduler
        scheduler.set_max(body.max_concurrent_tasks)  # 立即生效，不用重启
    if body.jianying_draft_dir is not None:
        cfg.jianying_draft_dir = body.jianying_draft_dir.strip() or None
    if body.task_storage_dir is not None:
        cfg.task_storage_dir = body.task_storage_dir.strip() or None
    if body.bgm_dir is not None:
        cfg.bgm_dir = body.bgm_dir.strip() or None
    if body.proxy_url is not None:
        cfg.proxy_url = body.proxy_url.strip() or None
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
    key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
    if not key:
        # 密文字段可能非空但解密为空（换机器/重装/主密钥变更致旧密文失效）→ 需重填
        raise HTTPException(400, detail="E6200: 未配置或密钥已失效，请到配置页重新填写 TTS Access Token")
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
    key = decrypt(cfg.tts_api_key_enc) if cfg.tts_api_key_enc else ""
    if not key:
        # 密文字段可能非空但解密为空（换机器/重装/主密钥变更致旧密文失效）→ 需重填
        raise HTTPException(400, detail="E6200: 未配置或密钥已失效，请到配置页重新填写 TTS Access Token")
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
