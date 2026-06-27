"""配置导出导入工具 - 避免重装后重填API密钥"""
import json
import sys
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, text


def get_db_path():
    """获取数据库路径"""
    import os
    # 优先使用生产库
    prod_db = Path.home() / "AppData/Roaming/shipinhao-desktop/data/app.db"
    if prod_db.exists():
        return str(prod_db)

    # 否则使用开发库
    dev_db = Path(__file__).parent / "app.db"
    if dev_db.exists():
        return str(dev_db)

    raise FileNotFoundError("未找到数据库文件")


def export_config(output_file: str = None):
    """导出配置到JSON文件（加密的密钥会导出为加密状态）

    Args:
        output_file: 输出文件路径，默认为 config_backup_YYYYMMDD_HHMMSS.json
    """
    db_path = get_db_path()
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM configs WHERE id=1"))
        row = result.fetchone()

        if not row:
            print("未找到配置记录")
            return

        # 获取列名
        columns = result.keys()

        # 转换为字典
        config = {col: row[i] for i, col in enumerate(columns)}

        # 处理二进制数据（加密的密钥）
        for key in ['llm_api_key_enc', 'image_api_key_enc', 'collect_api_key_enc',
                    'asr_api_key_enc', 'tts_api_key_enc']:
            if config.get(key):
                # 转换为hex字符串，便于JSON序列化
                config[key] = config[key].hex()

        # 处理JSON字段
        for key in ['image_presets', 'tts_favorites', 'pause_steps', 'source_meta']:
            if isinstance(config.get(key), str):
                try:
                    config[key] = json.loads(config[key])
                except:
                    pass

        # 生成输出文件名
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"config_backup_{timestamp}.json"

        # 保存到文件
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2, default=str)

        print(f"[OK] 配置已导出到: {output_file}")
        print(f"  数据库: {db_path}")
        print(f"  包含内容:")
        print(f"    - LLM配置: {config.get('llm_provider')}/{config.get('llm_model')}")
        print(f"    - 图片配置: {config.get('image_provider')}/{config.get('image_model')}")
        print(f"    - TTS配置: {config.get('tts_provider')}")

        presets = config.get('image_presets', [])
        if presets:
            print(f"    - 配图预设: {len(presets)}个")

        # 检查是否有加密的密钥
        has_keys = any(config.get(k) for k in ['llm_api_key_enc', 'image_api_key_enc',
                                                  'collect_api_key_enc', 'asr_api_key_enc',
                                                  'tts_api_key_enc'])
        if has_keys:
            print(f"    - API密钥: 已加密保存")
            print()
            print("[!] 注意: 密钥以加密形式保存，只能在相同的APP_ENCRYPTION_KEY下恢复")

        return output_file


def import_config(input_file: str, target_db: str = None):
    """从JSON文件导入配置

    Args:
        input_file: 输入文件路径
        target_db: 目标数据库路径，默认为当前数据库
    """
    if not Path(input_file).exists():
        print(f"文件不存在: {input_file}")
        return

    # 读取配置
    with open(input_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 获取目标数据库
    if not target_db:
        target_db = get_db_path()

    engine = create_engine(f"sqlite:///{target_db}")

    with engine.connect() as conn:
        # 检查是否已有配置
        result = conn.execute(text("SELECT id FROM configs WHERE id=1"))
        exists = result.fetchone() is not None

        # 处理加密密钥（hex字符串转回bytes）
        for key in ['llm_api_key_enc', 'image_api_key_enc', 'collect_api_key_enc',
                    'asr_api_key_enc', 'tts_api_key_enc']:
            if config.get(key) and isinstance(config[key], str):
                config[key] = bytes.fromhex(config[key])

        # 处理JSON字段
        for key in ['image_presets', 'tts_favorites', 'pause_steps']:
            if config.get(key) and isinstance(config[key], (list, dict)):
                config[key] = json.dumps(config[key], ensure_ascii=False)

        # 构建SQL
        if exists:
            # 更新现有配置
            set_clause = ", ".join([f"{k} = :{k}" for k in config.keys() if k != 'id'])
            sql = f"UPDATE configs SET {set_clause} WHERE id = 1"
        else:
            # 插入新配置
            columns = ", ".join(config.keys())
            placeholders = ", ".join([f":{k}" for k in config.keys()])
            sql = f"INSERT INTO configs ({columns}) VALUES ({placeholders})"

        conn.execute(text(sql), config)
        conn.commit()

        print(f"[OK] 配置已导入到: {target_db}")
        print(f"  操作: {'更新' if exists else '新建'}")

        # 验证
        result = conn.execute(text("SELECT llm_model, image_model FROM configs WHERE id=1"))
        row = result.fetchone()
        if row:
            print(f"  验证: LLM={row[0]}, Image={row[1]}")


def main():
    """命令行入口"""
    if len(sys.argv) < 2:
        print("用法:")
        print("  导出配置: python export_config.py export [output_file]")
        print("  导入配置: python export_config.py import <input_file> [target_db]")
        print()
        print("示例:")
        print("  python export_config.py export")
        print("  python export_config.py export my_config.json")
        print("  python export_config.py import config_backup_20260627.json")
        return

    action = sys.argv[1].lower()

    if action == 'export':
        output_file = sys.argv[2] if len(sys.argv) > 2 else None
        export_config(output_file)

    elif action == 'import':
        if len(sys.argv) < 3:
            print("错误: 请指定输入文件")
            return
        input_file = sys.argv[2]
        target_db = sys.argv[3] if len(sys.argv) > 3 else None
        import_config(input_file, target_db)

    else:
        print(f"未知操作: {action}")
        print("支持的操作: export, import")


if __name__ == "__main__":
    main()
