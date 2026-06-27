"""数据库健康检查与自动备份模块"""
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from sqlalchemy import text
from app.core.database import engine


def check_database_integrity(db_path: str) -> tuple[bool, str]:
    """检查数据库完整性

    Returns:
        (is_healthy, message)
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # SQLite内置的完整性检查
        cursor.execute('PRAGMA integrity_check')
        result = cursor.fetchone()

        conn.close()

        if result and result[0] == 'ok':
            return True, "数据库完整性检查通过"
        else:
            return False, f"数据库损坏: {result}"
    except Exception as e:
        return False, f"检查失败: {e}"


def auto_backup_database(db_path: str, max_backups: int = 5) -> str:
    """自动备份数据库，保留最近N个备份

    Args:
        db_path: 数据库文件路径
        max_backups: 保留的备份数量

    Returns:
        备份文件路径
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    # 先检查完整性
    is_healthy, msg = check_database_integrity(db_path)
    if not is_healthy:
        raise ValueError(f"数据库已损坏，拒绝备份: {msg}")

    # 备份文件名：app.db.backup_YYYYMMDD_HHMMSS
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"

    # 复制数据库文件
    shutil.copy2(db_path, backup_path)

    # 清理旧备份（保留最近N个）
    cleanup_old_backups(db_path, max_backups)

    return backup_path


def cleanup_old_backups(db_path: str, keep: int = 5):
    """清理旧备份，只保留最近N个

    Args:
        db_path: 数据库文件路径
        keep: 保留的备份数量
    """
    db_dir = os.path.dirname(db_path)
    db_name = os.path.basename(db_path)

    # 查找所有备份文件
    backups = []
    for file in os.listdir(db_dir):
        if file.startswith(f"{db_name}.backup_"):
            backup_path = os.path.join(db_dir, file)
            backups.append((os.path.getmtime(backup_path), backup_path))

    # 按时间排序，删除多余的旧备份
    backups.sort(reverse=True)  # 最新的在前
    for _, backup_path in backups[keep:]:
        try:
            os.remove(backup_path)
            print(f"已删除旧备份: {os.path.basename(backup_path)}")
        except Exception as e:
            print(f"删除备份失败 {backup_path}: {e}")


def safe_checkpoint() -> bool:
    """安全的WAL检查点（将WAL内容合并回主数据库）

    在应用关闭前调用，确保数据写入主数据库文件
    """
    try:
        with engine.connect() as conn:
            # PRAGMA wal_checkpoint(TRUNCATE) 会：
            # 1. 将WAL文件内容写入主数据库
            # 2. 重置WAL文件
            # 3. 确保数据持久化
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.commit()
        return True
    except Exception as e:
        print(f"WAL checkpoint失败: {e}")
        return False


def init_database_health_check():
    """应用启动时的数据库健康检查"""
    from app.core.config import settings

    # 获取数据库路径
    db_url = str(engine.url)
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")

        # 检查完整性
        is_healthy, msg = check_database_integrity(db_path)

        if is_healthy:
            print(f"[OK] {msg}")

            # 自动备份（每次启动）
            try:
                backup_path = auto_backup_database(db_path, max_backups=7)
                print(f"[OK] 已自动备份数据库: {os.path.basename(backup_path)}")
            except Exception as e:
                print(f"[WARN] 自动备份失败: {e}")
        else:
            print(f"[FAIL] {msg}")
            print("[WARN] 检测到数据库损坏！请尝试从备份恢复。")
            raise RuntimeError("数据库损坏，无法启动应用")


def shutdown_database_safely():
    """应用关闭时安全清理数据库连接"""
    print("正在安全关闭数据库...")

    # 执行WAL checkpoint
    if safe_checkpoint():
        print("[OK] WAL checkpoint完成")

    # 关闭所有连接
    engine.dispose()
    print("[OK] 数据库连接已关闭")
