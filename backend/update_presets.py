#!/usr/bin/env python3
"""更新配图预设：添加 Apimart 和豆包官方预设"""
import sqlite3
import json

# 新的完整预设列表（5个）
NEW_PRESETS = [
    # 兔子中转站
    {"name": "豆包 Seedream（兔子）", "model": "doubao-seedream-4-5-251128",
     "base_url": "https://api.tu-zi.com/v1/images/generations", "unit_price": 0.25},
    {"name": "gpt-image-2（兔子）", "model": "gpt-image-2",
     "base_url": "https://api.tu-zi.com/v1/images/generations", "unit_price": 0.058},

    # Apimart 中转站 - 价格最优（异步协议）
    {"name": "豆包 Seedream（Apimart）", "model": "doubao-seedream-4-5-251128",
     "base_url": "https://api.apib.ai/v1/images/generations", "unit_price": 0.058},
    {"name": "gpt-image-2（Apimart）", "model": "gpt-image-2",
     "base_url": "https://api.apib.ai/v1/images/generations", "unit_price": 0.042},

    # 豆包官方
    {"name": "豆包 Seedream（官方）", "model": "doubao-seedream-4-5-251128",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3/images/generations", "unit_price": 0.20},
]

def update_presets():
    """更新数据库中的预设"""
    conn = sqlite3.connect('app.db')
    cur = conn.cursor()

    # 读取当前配置
    cur.execute('SELECT id, image_presets FROM configs LIMIT 1')
    row = cur.fetchone()

    if not row:
        print("❌ 没有找到配置记录，请先启动应用创建配置")
        return

    config_id, old_presets_json = row
    old_presets = json.loads(old_presets_json) if old_presets_json else []

    print(f"[INFO] 当前预设数量: {len(old_presets)}")
    for p in old_presets:
        print(f"  - {p['name']}: {p['model']} @ 单价{p['unit_price']}")

    # 更新预设
    new_presets_json = json.dumps(NEW_PRESETS, ensure_ascii=False)
    cur.execute('UPDATE configs SET image_presets = ? WHERE id = ?',
                (new_presets_json, config_id))
    conn.commit()

    print(f"\n[SUCCESS] 已更新为 {len(NEW_PRESETS)} 个预设:")
    for p in NEW_PRESETS:
        print(f"  - {p['name']}: {p['model']} @ 单价{p['unit_price']}")

    print("\n[TIP] 刷新配置页面即可看到新预设")

    conn.close()

if __name__ == '__main__':
    update_presets()
