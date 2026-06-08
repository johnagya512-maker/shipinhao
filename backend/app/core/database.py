"""数据库连接与会话。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
        ("configs", "bgm_dir", "VARCHAR(500)"),
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
