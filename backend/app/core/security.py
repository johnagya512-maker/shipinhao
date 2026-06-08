"""对称加密：API Key 落库前加密，不存明文。"""
import base64
import hashlib
from cryptography.fernet import Fernet
from app.core.config import settings


def _derive_key() -> bytes:
    """从配置主密钥派生 Fernet 密钥。主密钥应来自环境变量。"""
    raw = settings.encryption_key
    if not raw:
        # 未配置时生成临时密钥（仅开发用，重启失效，会有告警）
        raw = "DEV_ONLY_INSECURE_KEY_CHANGE_ME"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def encrypt(plaintext: str) -> bytes:
    if plaintext is None:
        plaintext = ""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    if not token:
        return ""
    return _fernet.decrypt(token).decode("utf-8")


def mask(secret: str) -> str:
    """前端展示用掩码，如 sk-****1234。"""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "****"
    return f"{secret[:3]}****{secret[-4:]}"


def seed_keys_from_env() -> None:
    """开发便利：把 .env 里的 seed_*_api_key 写进数据库（仅当该项当前为空）。

    解决“每次测试都要重输 Key”——把 Key 放 .env（已被 git 忽略），
    启动时自动种子。已在 DB 里配过的项不会被覆盖。
    """
    from app.core.database import SessionLocal
    from app.models import Config

    pairs = [
        (settings.seed_llm_api_key, "llm_api_key_enc"),
        (settings.seed_image_api_key, "image_api_key_enc"),
        (settings.seed_collect_api_key, "collect_api_key_enc"),
        (settings.seed_asr_api_key, "asr_api_key_enc"),
        (settings.seed_tts_api_key, "tts_api_key_enc"),
    ]
    if not any(v for v, _ in pairs):
        return
    db = SessionLocal()
    try:
        cfg = db.get(Config, 1)
        if not cfg:
            cfg = Config(id=1)
            db.add(cfg)
        changed = False
        for value, field in pairs:
            if value and not getattr(cfg, field):
                setattr(cfg, field, encrypt(value))
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()
