"""SQLAlchemy 数据模型。对应 PRD 第⑧章。"""
from datetime import datetime
from sqlalchemy import String, Text, Integer, BigInteger, Boolean, DateTime, Numeric, JSON, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

# SQLite 仅对 INTEGER PRIMARY KEY 自增；BIGINT 不行。其他库仍用 BIGINT。
AutoBigInt = BigInteger().with_variant(Integer, "sqlite")


def _now() -> datetime:
    return datetime.utcnow()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), default="user_001")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    transcript: Mapped[str] = mapped_column(Text)
    keyword: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    modules: Mapped[list] = mapped_column(JSON, default=list)
    target_audience: Mapped[str] = mapped_column(String(30), default="50+女性")
    track: Mapped[str] = mapped_column(String(30), default="character_story")
    monetization_mode: Mapped[str] = mapped_column(String(20), default="revenue_share")
    image_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cost_limit: Mapped[float] = mapped_column(Numeric(6, 2), default=1.0)
    time_limit: Mapped[int] = mapped_column(Integer, default=900)
    enable_subtitles: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_animations: Mapped[bool] = mapped_column(Boolean, default=True)
    total_cost: Mapped[float] = mapped_column(Numeric(8, 4), default=0)
    error_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ModuleResult(Base):
    __tablename__ = "module_results"

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    module: Mapped[str] = mapped_column(String(1))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[float] = mapped_column(Numeric(8, 4), default=0)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    type: Mapped[str] = mapped_column(String(20))
    sub_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Config(Base):
    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    llm_provider: Mapped[str] = mapped_column(String(20), default="deepseek")
    llm_model: Mapped[str] = mapped_column(String(50), default="deepseek-chat")
    llm_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    image_provider: Mapped[str] = mapped_column(String(20), default="doubao")
    image_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    daily_cost_cap: Mapped[float] = mapped_column(Numeric(8, 2), default=100)
    concurrency: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CostLog(Base):
    __tablename__ = "cost_logs"

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    module: Mapped[str] = mapped_column(String(1))
    provider: Mapped[str] = mapped_column(String(20))
    cost: Mapped[float] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
