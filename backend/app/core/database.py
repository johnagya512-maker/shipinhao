"""数据库连接与会话。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        """多任务并行时 SQLite 默认整库锁会导致 database is locked。
        WAL 让读写不互斥；busy_timeout 把瞬时锁冲突改为短暂等待而非立即报错。"""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表。导入模型后调用。"""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns():
    """轻量补列：无 alembic 的场景下，为已存在的旧库补上新增字段，避免读取报错。"""
    from sqlalchemy import inspect, text
    # 新增列清单：(表, 列, 建列 SQL 片段)
    pending = [
        ("tasks", "aspect_ratio", "VARCHAR(10) DEFAULT '9:16'"),
        ("tasks", "rewrite_strength", "VARCHAR(10) DEFAULT 'medium'"),
        ("tasks", "narrative_perspective", "VARCHAR(10) DEFAULT 'auto'"),
        ("tasks", "voice_speed", "NUMERIC(3,2) DEFAULT 1.0"),
        ("tasks", "voice", "VARCHAR(120)"),
        ("tasks", "reference_image", "VARCHAR(500)"),
        ("tasks", "bgm", "VARCHAR(120)"),
        ("tasks", "processing_mode", "VARCHAR(12) DEFAULT 'full_auto'"),
        ("tasks", "pause_mode", "VARCHAR(12) DEFAULT 'none'"),
        ("tasks", "pause_steps", "JSON"),
        ("tasks", "paused_at", "VARCHAR(2)"),
        ("tasks", "draft_template", "VARCHAR(20) DEFAULT 'classic'"),
        ("tasks", "creation_mode", "VARCHAR(16) DEFAULT 'same_topic'"),
        ("tasks", "long_title", "VARCHAR(200)"),
        ("tasks", "hashtags", "JSON"),
        ("configs", "task_storage_dir", "VARCHAR(500)"),
        ("configs", "bgm_dir", "VARCHAR(500)"),
        ("configs", "vision_model", "VARCHAR(80) DEFAULT 'doubao-seed-1-6-250615'"),
        ("configs", "max_concurrent_tasks", "INTEGER DEFAULT 3"),
        ("configs", "tts_favorites", "JSON"),
        ("configs", "proxy_url", "VARCHAR(200)"),
    ]
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, col, ddl in pending:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
