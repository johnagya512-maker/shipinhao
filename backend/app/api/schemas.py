"""API 请求/响应模型。"""
from datetime import datetime
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    # 约束不放在 Field 上：交给 create_task 手动校验，以返回 PRD 5.4 规定的错误码
    # （E1001/E1002/E1004），而非 Pydantic 默认的 422。
    # 入口二选一：填 douyin_url（自动采集+ASR）或手填 transcript。
    douyin_url: str | None = None
    transcript: str | None = None
    keyword: str | None = None
    title: str | None = None
    author: str | None = None
    modules: list[str] = Field(default_factory=lambda: ["A", "B", "E", "F", "G"])
    target_audience: str = "50+女性"
    track: str = "character_story"
    monetization_mode: str = "revenue_share"  # revenue_share | book_sales
    image_style: str | None = None
    aspect_ratio: str = "9:16"   # 出图比例：9:16 / 3:4 / 1:1 / 16:9
    rewrite_strength: str = "medium"      # 改写强度：light / medium / strong
    narrative_perspective: str = "auto"   # 叙事视角：auto / first / third
    voice_speed: float = 1.0     # 配音语速 0.5~2.0
    voice: str | None = None     # 配音员音色 ID（空=用配置页默认）
    reference_image: str | None = None  # 主角参考图暂存路径（来自 /tasks/upload-reference）
    bgm: str = ""                # 背景音乐：空=无；文件名（来自配置 bgm 目录）
    cost_limit: float = 5.0
    time_limit: int = Field(default=900, ge=60, le=3600)
    enable_subtitles: bool = True
    enable_animations: bool = True


class RerunRequest(BaseModel):
    """blocked 任务改文案后重跑（PRD 5.3 状态流转）。"""
    transcript: str | None = None  # 改后的逐字稿；为空则沿用原文重跑


class TaskOut(BaseModel):
    id: str
    status: str
    total_cost: float
    error_code: str | None = None
    error_message: str | None = None

    class Config:
        from_attributes = True


class TaskListItem(BaseModel):
    """任务列表项（PRD 6.1 任务列表页）。比 TaskOut 多带摘要信息。"""
    id: str
    status: str
    total_cost: float
    track: str
    target_audience: str
    transcript_preview: str
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListOut(BaseModel):
    items: list[TaskListItem]
    total: int
    page: int
    page_size: int


class ConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    image_provider: str | None = None
    image_model: str | None = None
    image_api_key: str | None = None
    collect_provider: str | None = None
    collect_api_key: str | None = None
    asr_provider: str | None = None
    asr_api_key: str | None = None
    tts_provider: str | None = None
    tts_api_key: str | None = None
    tts_voice: str | None = None
    tts_appid: str | None = None
    daily_cost_cap: float | None = Field(default=None, ge=0)
    concurrency: int | None = Field(default=None, ge=1, le=10)
    jianying_draft_dir: str | None = None
    task_storage_dir: str | None = None
    bgm_dir: str | None = None


class ConfigOut(BaseModel):
    llm_provider: str
    llm_model: str
    llm_api_key_mask: str
    image_provider: str
    image_model: str
    image_api_key_mask: str
    collect_provider: str
    collect_api_key_mask: str
    asr_provider: str
    asr_api_key_mask: str
    tts_provider: str
    tts_api_key_mask: str
    tts_voice: str
    tts_appid: str
    daily_cost_cap: float
    concurrency: int
    jianying_draft_dir: str
    task_storage_dir: str
    bgm_dir: str


class EstimateOut(BaseModel):
    estimated_cost: float
    daily_cap_reached: bool
