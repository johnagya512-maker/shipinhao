"""配置自动备份与迁移管理器 - 用户无感知"""
import json
import shutil
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, text
import logging

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器：自动备份、迁移、恢复"""

    def __init__(self, db_path: str, data_dir: Path):
        self.db_path = db_path
        self.data_dir = Path(data_dir)
        self.backup_dir = self.data_dir / "config_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def auto_backup_on_startup(self) -> str | None:
        """应用启动时自动备份配置

        Returns:
            备份文件路径，失败返回None
        """
        try:
            # 检查是否有配置需要备份
            engine = create_engine(f"sqlite:///{self.db_path}")
            with engine.connect() as conn:
                result = conn.execute(text("SELECT * FROM configs WHERE id=1"))
                config = result.fetchone()

                if not config:
                    logger.info("未找到配置，跳过备份")
                    return None

                # 检查是否有API密钥配置（判断是否值得备份）
                columns = result.keys()
                config_dict = {col: config[i] for i, col in enumerate(columns)}

                has_keys = any(config_dict.get(k) for k in [
                    'llm_api_key_enc', 'image_api_key_enc',
                    'collect_api_key_enc', 'asr_api_key_enc', 'tts_api_key_enc'
                ])

                if not has_keys:
                    logger.info("未配置API密钥，跳过备份")
                    return None

                # 导出配置
                backup_file = self._export_config(config_dict, columns)

                # 清理旧备份（保留最近10个）
                self._cleanup_old_backups(keep=10)

                logger.info(f"配置已自动备份: {backup_file.name}")
                return str(backup_file)

        except Exception as e:
            logger.error(f"配置自动备份失败: {e}", exc_info=True)
            return None

    def _export_config(self, config_dict: dict, columns) -> Path:
        """导出配置到JSON"""
        # 处理二进制数据（加密密钥）
        for key in ['llm_api_key_enc', 'image_api_key_enc', 'collect_api_key_enc',
                    'asr_api_key_enc', 'tts_api_key_enc']:
            if config_dict.get(key):
                config_dict[key] = config_dict[key].hex()

        # 处理JSON字段
        for key in ['image_presets', 'tts_favorites', 'pause_steps']:
            if isinstance(config_dict.get(key), str):
                try:
                    config_dict[key] = json.loads(config_dict[key])
                except:
                    pass

        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"config_backup_{timestamp}.json"

        # 保存
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2, default=str)

        return backup_file

    def _cleanup_old_backups(self, keep: int = 10):
        """清理旧备份，保留最近N个"""
        backups = sorted(
            self.backup_dir.glob("config_backup_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for old_backup in backups[keep:]:
            try:
                old_backup.unlink()
                logger.debug(f"已删除旧备份: {old_backup.name}")
            except Exception as e:
                logger.warning(f"删除旧备份失败 {old_backup}: {e}")

    def restore_latest_backup(self) -> bool:
        """恢复最新的配置备份

        Returns:
            是否恢复成功
        """
        try:
            backups = sorted(
                self.backup_dir.glob("config_backup_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if not backups:
                logger.warning("未找到配置备份")
                return False

            latest_backup = backups[0]
            logger.info(f"准备恢复配置: {latest_backup.name}")

            # 读取备份
            with open(latest_backup, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # 导入到数据库
            self._import_config(config)

            logger.info("配置恢复成功")
            return True

        except Exception as e:
            logger.error(f"配置恢复失败: {e}", exc_info=True)
            return False

    def _import_config(self, config: dict):
        """导入配置到数据库"""
        engine = create_engine(f"sqlite:///{self.db_path}")

        with engine.connect() as conn:
            # 处理加密密钥（hex转bytes）
            for key in ['llm_api_key_enc', 'image_api_key_enc', 'collect_api_key_enc',
                        'asr_api_key_enc', 'tts_api_key_enc']:
                if config.get(key) and isinstance(config[key], str):
                    config[key] = bytes.fromhex(config[key])

            # 处理JSON字段
            for key in ['image_presets', 'tts_favorites', 'pause_steps']:
                if config.get(key) and isinstance(config[key], (list, dict)):
                    config[key] = json.dumps(config[key], ensure_ascii=False)

            # 检查是否已有配置
            result = conn.execute(text("SELECT id FROM configs WHERE id=1"))
            exists = result.fetchone() is not None

            if exists:
                # 更新
                set_clause = ", ".join([f"{k} = :{k}" for k in config.keys() if k != 'id'])
                sql = f"UPDATE configs SET {set_clause} WHERE id = 1"
            else:
                # 插入
                columns = ", ".join(config.keys())
                placeholders = ", ".join([f":{k}" for k in config.keys()])
                sql = f"INSERT INTO configs ({columns}) VALUES ({placeholders})"

            conn.execute(text(sql), config)
            conn.commit()

    def migrate_from_old_version(self, old_data_dir: Path) -> bool:
        """从旧版本迁移配置

        Args:
            old_data_dir: 旧版本的数据目录

        Returns:
            是否迁移成功
        """
        try:
            old_db = old_data_dir / "app.db"
            if not old_db.exists():
                logger.info("未找到旧版本数据库")
                return False

            logger.info(f"检测到旧版本数据库: {old_db}")

            # 读取旧配置
            old_engine = create_engine(f"sqlite:///{old_db}")
            with old_engine.connect() as conn:
                result = conn.execute(text("SELECT * FROM configs WHERE id=1"))
                old_config = result.fetchone()

                if not old_config:
                    logger.warning("旧数据库中无配置")
                    return False

                columns = result.keys()
                config_dict = {col: old_config[i] for i, col in enumerate(columns)}

            # 导入到新数据库
            self._import_config(config_dict)

            logger.info("配置迁移成功")
            return True

        except Exception as e:
            logger.error(f"配置迁移失败: {e}", exc_info=True)
            return False


def auto_backup_config():
    """启动时自动备份配置（集成到app.main.py的startup）"""
    from app.core.config import settings, _DATA_DIR

    try:
        db_url = str(settings.database_url)
        if db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "")
            manager = ConfigManager(db_path, Path(_DATA_DIR))
            backup_file = manager.auto_backup_on_startup()

            if backup_file:
                logger.info(f"✓ 配置已自动备份到: {Path(backup_file).name}")
    except Exception as e:
        logger.warning(f"配置自动备份失败（不影响启动）: {e}")


def restore_config_if_empty():
    """如果当前无配置，尝试从备份恢复（应对误删除）"""
    from app.core.config import settings, _DATA_DIR
    from app.core.database import SessionLocal
    from app.models import Config

    try:
        db = SessionLocal()
        config = db.get(Config, 1)
        db.close()

        # 检查是否有API密钥
        if config:
            has_keys = any([
                config.llm_api_key_enc,
                config.image_api_key_enc,
                config.tts_api_key_enc
            ])

            if has_keys:
                return  # 配置完整，无需恢复

        # 尝试恢复
        logger.warning("检测到配置缺失，尝试从备份恢复...")

        db_url = str(settings.database_url)
        if db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "")
            manager = ConfigManager(db_path, Path(_DATA_DIR))

            if manager.restore_latest_backup():
                logger.info("✓ 配置已从备份自动恢复")
            else:
                logger.warning("未找到可用的配置备份")

    except Exception as e:
        logger.warning(f"配置自动恢复失败: {e}")
