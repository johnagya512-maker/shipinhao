#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试 Apimart API Key 是否有效"""
import httpx
from app.core.database import engine
from app.core.crypto import decrypt
from sqlalchemy import text

def test_apimart_key():
    """测试当前保存的 Apimart Key"""
    conn = engine.connect()

    # 获取配置
    result = conn.execute(text('''
        SELECT image_api_key_enc, image_model, image_base_url
        FROM configs WHERE id=1
    ''')).fetchone()

    if not result or not result[0]:
        print('[ERROR] 未找到 API Key')
        return

    api_key = decrypt(result[0])
    model = result[1] or 'gpt-image-2'
    base_url = result[2] or 'https://api.apimart.ai/v1/images/generations'

    print(f'[INFO] 配置:')
    print(f'  Model: {model}')
    print(f'  Base URL: {base_url}')
    print(f'  API Key: {api_key[:8]}...{api_key[-4:]}')
    print()

    # 发送测试请求
    print('[TEST] 发送测试请求...')
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': model,
        'prompt': 'A simple red circle on white background',
        'size': '1024x1024',
        'n': 1
    }

    try:
        resp = httpx.post(base_url, json=payload, headers=headers, timeout=30.0)
        print(f'[RESPONSE] Status: {resp.status_code}')
        print(f'[RESPONSE] Body: {resp.text[:500]}')

        if resp.status_code == 200:
            data = resp.json().get('data', [])
            if data:
                print('[SUCCESS] API Key 有效，返回了图片数据')
            else:
                print('[WARNING] API 返回 200 但 data 为空（可能是余额不足或其他问题）')
        elif resp.status_code == 401:
            print('[ERROR] API Key 无效')
        elif resp.status_code == 402:
            print('[ERROR] 余额不足')
        elif resp.status_code == 429:
            print('[ERROR] 请求过于频繁，被限流')
        else:
            print(f'[ERROR] 请求失败: {resp.status_code}')
    except Exception as e:
        print(f'[ERROR] 请求异常: {e}')

    conn.close()

if __name__ == '__main__':
    test_apimart_key()
