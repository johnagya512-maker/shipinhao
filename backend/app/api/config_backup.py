"""配置备份/恢复API - 用户界面调用"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pathlib import Path
import json

from app.core.database import get_db
from app.core.config import _DATA_DIR
from app.api.auth import require_auth

router = APIRouter(prefix="/api/v1/config-backup", dependencies=[Depends(require_auth)])


@router.post("/export")
def export_config(db: Session = Depends(get_db)):
    """导出配置到JSON文件

    Returns:
        下载文件的路径信息
    """
    from app.core.config import settings
    from app.core.config_manager import ConfigManager
    from datetime import datetime

    try:
        db_url = str(settings.database_url)
        if not db_url.startswith("sqlite:///"):
            raise HTTPException(400, detail="仅支持SQLite数据库导出")

        db_path = db_url.replace("sqlite:///", "")
        manager = ConfigManager(db_path, Path(_DATA_DIR))

        # 导出配置（不自动清理，由前端下载后用户决定）
        from sqlalchemy import create_engine, text
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM configs WHERE id=1"))
            config = result.fetchone()

            if not config:
                raise HTTPException(404, detail="未找到配置")

            columns = result.keys()
            config_dict = {col: config[i] for i, col in enumerate(columns)}

            # 导出
            backup_file = manager._export_config(config_dict, columns)

            return {
                "ok": True,
                "file": str(backup_file),
                "filename": backup_file.name,
                "message": "配置已导出，正在准备下载..."
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"导出失败: {str(e)}")


@router.get("/download/{filename}")
def download_config(filename: str):
    """下载导出的配置文件"""
    backup_dir = Path(_DATA_DIR) / "config_backups"
    file_path = backup_dir / filename

    if not file_path.exists():
        raise HTTPException(404, detail="文件不存在")

    # 安全检查：确保文件在备份目录内
    if not file_path.resolve().is_relative_to(backup_dir.resolve()):
        raise HTTPException(403, detail="非法路径")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/json"
    )


@router.post("/import")
def import_config(body: dict, db: Session = Depends(get_db)):
    """从JSON导入配置

    Args:
        body: {"config": {配置JSON对象}}
    """
    from app.core.config import settings
    from app.core.config_manager import ConfigManager

    try:
        if "config" not in body:
            raise HTTPException(400, detail="缺少config字段")

        config = body["config"]

        db_url = str(settings.database_url)
        if not db_url.startswith("sqlite:///"):
            raise HTTPException(400, detail="仅支持SQLite数据库导入")

        db_path = db_url.replace("sqlite:///", "")
        manager = ConfigManager(db_path, Path(_DATA_DIR))

        # 导入配置
        manager._import_config(config)

        return {
            "ok": True,
            "message": "配置已导入，部分设置需要重启应用生效"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"导入失败: {str(e)}")


@router.get("/list")
def list_backups():
    """列出所有配置备份"""
    backup_dir = Path(_DATA_DIR) / "config_backups"

    if not backup_dir.exists():
        return {"backups": []}

    backups = []
    for file in sorted(backup_dir.glob("config_backup_*.json"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True):
        stat = file.stat()
        backups.append({
            "filename": file.name,
            "size": stat.st_size,
            "created_at": stat.st_mtime,
            "date": file.name.replace("config_backup_", "").replace(".json", "")
        })

    return {"backups": backups[:20]}  # 最多返回最近20个
