"""应用配置。从环境变量读取，密钥不落代码库。"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py 位于 backend/app/core/，上溯三级到 backend 根目录。
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    # 数据库
    database_url: str = f"sqlite:///{BASE_DIR / 'app.db'}"

    # 存储根目录
    storage_dir: Path = BASE_DIR / "storage"

    # 加密主密钥（Fernet）。生产环境必须从环境变量注入。
    # 未设置时启动生成临时密钥并告警（重启后已存密钥将无法解密）。
    encryption_key: str = ""

    # 系统访问令牌。为空表示仅本机模式（绑定 127.0.0.1，可豁免）。
    access_token: str = ""

    # 服务绑定地址，默认仅本机
    host: str = "127.0.0.1"
    port: int = 8000

    # 文件大小上限（字节）
    max_image_bytes: int = 5 * 1024 * 1024
    max_audio_bytes: int = 50 * 1024 * 1024
    max_video_bytes: int = 200 * 1024 * 1024


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
