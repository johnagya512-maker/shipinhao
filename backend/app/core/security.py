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
