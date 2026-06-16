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
    # 抖音来源链接（全自动入口）。为空表示手填逐字稿模式。
    douyin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 采集到的来源元数据（播放量/点赞/博主等），JSON 存储。
    source_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transcript: Mapped[str] = mapped_column(Text)
    keyword: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 长标题（发布说明文案首行用，比 title 更完整）+ 短标题（15字精炼，封面/标题用）+ 热门话题标签（JSON 数组，不含 #）
    long_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    short_title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    modules: Mapped[list] = mapped_column(JSON, default=list)
    target_audience: Mapped[str] = mapped_column(String(30), default="50+女性")
    track: Mapped[str] = mapped_column(String(30), default="character_story")
    monetization_mode: Mapped[str] = mapped_column(String(20), default="revenue_share")
    image_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="9:16")
    rewrite_strength: Mapped[str] = mapped_column(String(10), default="medium")
    narrative_perspective: Mapped[str] = mapped_column(String(10), default="auto")
    voice_speed: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)
    voice: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reference_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bgm: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cost_limit: Mapped[float] = mapped_column(Numeric(6, 2), default=5.0)
    time_limit: Mapped[int] = mapped_column(Integer, default=900)
    enable_subtitles: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_animations: Mapped[bool] = mapped_column(Boolean, default=True)
    # 草稿动画模板：none/classic/narration/lively/cinematic/random（见 draft_templates.py）
    draft_template: Mapped[str] = mapped_column(String(20), default="classic")
    # 二创方式：same_topic（拆爆款结构骨架 → 按骨架重写）/ none（不拆结构直接改写）
    creation_mode: Mapped[str] = mapped_column(String(16), default="same_topic")
    # 生图模式：per_image（逐张，画质优先）/ grid（九宫格省成本，一次出9张切割，省约89%）
    image_gen_mode: Mapped[str] = mapped_column(String(12), default="per_image")
    # 处理模式：full_auto（完整跑）/ semi_auto（不改写，仅分句）/ direct（不改写、机械切分）
    processing_mode: Mapped[str] = mapped_column(String(12), default="full_auto")
    # 暂停确认：none（不停）/ key_nodes（关键节点）/ every_step（每步）/ custom（自定义步骤）
    pause_mode: Mapped[str] = mapped_column(String(12), default="none")
    # 自定义暂停的步骤集合（pause_mode=custom 时生效），如 ["B","E"]
    pause_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 当前暂停在哪个 step（awaiting_confirm 时有值；恢复后清空）
    paused_at: Mapped[str | None] = mapped_column(String(2), nullable=True)
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
    image_model: Mapped[str] = mapped_column(String(80), default="doubao-seedream-4-5-251128")
    image_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 视觉模型（多模态 LLM）：反推主角参考图的人物特征文字。豆包视觉模型与绘图模型
    # 同在火山方舟，复用 image_provider + image_api_key，仅模型 id 不同。
    vision_model: Mapped[str] = mapped_column(String(80), default="doubao-seed-1-6-250615")
    # 抖音采集（TikHub 等）
    collect_provider: Mapped[str] = mapped_column(String(20), default="tikhub")
    collect_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 语音转写 ASR（硅基流动 SenseVoice 等）
    asr_provider: Mapped[str] = mapped_column(String(20), default="siliconflow")
    asr_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 配音 TTS（火山引擎 volcano / 硅基流动 siliconflow）
    tts_provider: Mapped[str] = mapped_column(String(20), default="volcano")
    tts_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tts_voice: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 收藏的音色 ID 列表（创建任务页优先展示）
    tts_favorites: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 火山 TTS 需要 appid（与 access_token 配对）
    tts_appid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 剪映草稿输出目录（用户本地剪映的"草稿存放位置"）。设置后 G 模块
    # 直接用 DraftFolder 写入此目录，剪映重启即可看到、可编辑。为空则退回
    # storage 内的裸 json（仅供下载，无法直接被剪映识别）。
    jianying_draft_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 任务存储目录（图片/音频/草稿等中间产物落盘位置）。为空则用默认（桌面端 AppData）。
    # 用户可改到大盘，避免 C 盘占满。
    task_storage_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 背景音乐目录：用户把 mp3 放进来，新建任务时可选作 BGM。为空则禁用 BGM。
    bgm_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 出站代理：访问境外接口（如 TikHub 采集）走代理。形如 http://127.0.0.1:7890。
    # 为空则直连。仅作用于采集/ASR 下载等出站请求，不影响国内接口。
    proxy_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 绘图接口地址覆盖：填了就用它替代豆包官方火山方舟地址，用于接 OpenAI 兼容的中转站
    # （如 APICore，单价更低）。形如 https://api.apicore.ai/v1/images/generations。空=官方。
    image_base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 配图单价（元/张/次请求）。写死的内置单价(豆包0.25等)只是缺省兜底；接中转站后
    # 实际单价不同(如兔子API约0.12)，填这里让成本核算/上限校验按真实单价算，不再虚高。
    # <=0 或空=用内置缺省价。九宫格按 ceil(张数/9) 折算请求数后再乘此单价。
    image_unit_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    daily_cost_cap: Mapped[float] = mapped_column(Numeric(8, 2), default=100)
    concurrency: Mapped[int] = mapped_column(Integer, default=3)
    # 任务级并发：同时执行的任务数上限（与 concurrency 的图片级并发相乘 ≈ 总图片请求量）。
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=3)
    # 新建任务默认生图模式：per_image（逐张）/ grid（九宫格省成本）
    default_image_gen_mode: Mapped[str] = mapped_column(String(12), default="per_image")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CostLog(Base):
    __tablename__ = "cost_logs"

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    module: Mapped[str] = mapped_column(String(1))
    provider: Mapped[str] = mapped_column(String(20))
    cost: Mapped[float] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
