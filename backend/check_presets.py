#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查预设是否保存了 API Key"""
from app.core.database import engine
from sqlalchemy import text
import json

conn = engine.connect()
result = conn.execute(text('SELECT image_presets FROM configs LIMIT 1')).fetchone()
presets = json.loads(result[0]) if result and result[0] else []

print('=' * 60)
print('预设列表（检查是否包含 API Key）')
print('=' * 60)
print()

for i, p in enumerate(presets, 1):
    name = p.get('name', '未命名')
    has_key = 'api_key' in p and p.get('api_key')

    print(f'{i}. {name}')
    if has_key:
        key = p.get('api_key', '')
        key_preview = key[:8] + '...' + key[-4:] if len(key) > 12 else key
        print(f'   API Key: {key_preview} [已保存]')
    else:
        print(f'   API Key: [未保存]')
    print()

conn.close()

print('=' * 60)
if all('api_key' in p and p.get('api_key') for p in presets):
    print('状态: 所有预设都已保存 Key，可以正常切换')
else:
    print('状态: 部分预设未保存 Key，需要重新保存')
print('=' * 60)
