"""修复数据库schema - 删除废弃的comment_cta列"""
import sqlite3

def fix_schema(db_path='app.db'):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print(f"Fixing schema in {db_path}...")

    # 检查comment_cta列是否存在
    cursor.execute("PRAGMA table_info(tasks)")
    columns = cursor.fetchall()
    has_comment_cta = any(col[1] == 'comment_cta' for col in columns)

    if not has_comment_cta:
        print("  comment_cta column not found, nothing to fix")
        conn.close()
        return

    print("  Found comment_cta column, recreating table without it...")

    # SQLite不支持直接删除列,需要重建表
    # 1. 创建新表(不含comment_cta)
    cursor.execute('''
        CREATE TABLE tasks_new (
            id VARCHAR(40) PRIMARY KEY,
            user_id VARCHAR(40),
            status VARCHAR(20),
            douyin_url VARCHAR(500),
            source_meta JSON,
            transcript TEXT,
            keyword VARCHAR(100),
            title VARCHAR(200),
            long_title VARCHAR(200),
            short_title VARCHAR(50),
            hashtags JSON,
            author VARCHAR(100),
            modules JSON,
            target_audience VARCHAR(30),
            track VARCHAR(30),
            monetization_mode VARCHAR(20),
            image_style VARCHAR(30),
            aspect_ratio VARCHAR(10),
            layout VARCHAR(16),
            rewrite_strength VARCHAR(10),
            narrative_perspective VARCHAR(10),
            voice_speed NUMERIC(3, 2),
            voice VARCHAR(120),
            reference_image VARCHAR(500),
            bgm VARCHAR(120),
            cost_limit NUMERIC(6, 2),
            time_limit INTEGER,
            enable_subtitles BOOLEAN,
            enable_animations BOOLEAN,
            draft_template VARCHAR(20),
            creation_mode VARCHAR(16),
            image_gen_mode VARCHAR(12),
            processing_mode VARCHAR(12),
            pause_mode VARCHAR(12),
            pause_steps JSON,
            paused_at VARCHAR(2),
            total_cost NUMERIC(8, 4),
            error_code VARCHAR(20),
            error_message TEXT,
            batch_id VARCHAR(40),
            created_at DATETIME,
            updated_at DATETIME
        )
    ''')

    # 2. 复制数据(排除comment_cta)
    cursor.execute('''
        INSERT INTO tasks_new SELECT
            id, user_id, status, douyin_url, source_meta, transcript, keyword, title,
            long_title, short_title, hashtags, author, modules, target_audience, track,
            monetization_mode, image_style, aspect_ratio, layout, rewrite_strength,
            narrative_perspective, voice_speed, voice, reference_image, bgm, cost_limit,
            time_limit, enable_subtitles, enable_animations, draft_template, creation_mode,
            image_gen_mode, processing_mode, pause_mode, pause_steps, paused_at,
            total_cost, error_code, error_message, batch_id, created_at, updated_at
        FROM tasks
    ''')

    # 3. 删除旧表
    cursor.execute('DROP TABLE tasks')

    # 4. 重命名新表
    cursor.execute('ALTER TABLE tasks_new RENAME TO tasks')

    # 5. 重建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_tasks_created_at ON tasks(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_tasks_batch_id ON tasks(batch_id)')

    conn.commit()
    conn.close()

    print("  [OK] Schema fixed successfully")

if __name__ == '__main__':
    import sys
    import os
    from pathlib import Path

    # 开发数据库
    if os.path.exists('app.db'):
        fix_schema('app.db')

    # 生产数据库(如果存在)
    prod_db = Path.home() / 'AppData/Local/shipinhao-test/data/app.db'
    if prod_db.exists():
        print(f"\nAlso fixing production database at {prod_db}")
        fix_schema(str(prod_db))

    print("\nDone!")
