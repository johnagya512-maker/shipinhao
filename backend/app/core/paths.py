"""任务产物存储根目录解析。

settings.storage_dir 是启动时定的默认值（桌面端 AppData）；
用户可在配置页设置 task_storage_dir 改到大盘。运行时优先读 DB 配置。
"""
from pathlib import Path
from app.core.config import settings


def storage_root(db) -> Path:
    """返回当前生效的任务存储根目录。DB 配了 task_storage_dir 就用它，否则用默认。"""
    from app.models import Config
    cfg = db.get(Config, 1)
    custom = (cfg.task_storage_dir or "").strip() if cfg else ""
    root = Path(custom) if custom else settings.storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root
