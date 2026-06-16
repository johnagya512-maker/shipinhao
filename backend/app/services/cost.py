"""成本核算与记录。对应 PRD 5.3、7.3、9.6。"""
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.models import CostLog, Config

# LLM 单价（元/千token），按供应商。可在配置中心扩展维护。
LLM_PRICE = {
    "deepseek": 0.001,
    "openai": 0.01,
    "qwen": 0.002,
    "doubao": 0.001,
}
# 配图单价（元/张），按供应商。九宫格模式按「请求单位数」计费——一次请求出 9 张只算
# 1 张钱（见 image_billable_units 的 ceil(张数/9) 折算），省约 89%；逐张/普通组图按实际张数。
IMAGE_PRICE = {
    "doubao": 0.25,
    "kling": 0.30,
    "tongyi": 0.20,
}

# 各 LLM 模块输出 token 相对输入的经验系数（PRD 9.6）。
OUTPUT_RATIO = {"A": 0.9, "B": 0.8, "C": 1.0, "F": 1.0}
# 固定输出 token 的模块。
FIXED_OUTPUT = {"D": 200, "H": 150}

CHARS_TO_TOKENS = 1.5  # 中文约 1.5 token/字
CHARS_PER_SECOND = 5   # 中文口播约 5 字/秒（与 F 分段时长估算一致）


def estimate_tokens(char_count: int) -> int:
    return int(char_count * CHARS_TO_TOKENS)


def estimate_image_count(transcript: str) -> int:
    """按逐字稿长度预估配图张数（与运行时 image_module.count_for_duration 同口径）。
    口播时长 ≈ 字数/5 秒，约 6 秒/张。"""
    from app.modules.image_module import count_for_duration
    return count_for_duration(len(transcript) / CHARS_PER_SECOND)


def estimate_cost(transcript: str, modules: list[str], image_count: int | None,
                  llm_provider: str, image_provider: str,
                  image_gen_mode: str = "per_image") -> float:
    """提交前预估成本（上限估计）。image_count 为 None 时按逐字稿长度动态推算。
    配图默认逐张、按实际张数计费（与 orchestrator 实际生成一致）；image_gen_mode=grid
    （已弃用的九宫格）才按 ceil(张数/9) 折算。"""
    if image_count is None:
        image_count = estimate_image_count(transcript)
    in_tokens = estimate_tokens(len(transcript))
    unit = LLM_PRICE.get(llm_provider, 0.001)
    llm_cost = 0.0
    for m in modules:
        if m in ("E", "G"):
            continue
        if m in FIXED_OUTPUT:
            out = FIXED_OUTPUT[m]
        else:
            out = int(in_tokens * OUTPUT_RATIO.get(m, 1.0))
        # H 是强制闸门，即便不在 modules 也会执行；此处由编排单独计入
        llm_cost += (in_tokens + out) / 1000 * unit
    img_cost = 0.0
    if "E" in modules:
        # 逐张生图（当前默认）按实际张数计费——与 orchestrator 实际走 render_images 一致。
        # 九宫格已弃用；仅当显式 image_gen_mode=grid 时才按 ceil(张数/9) 折算（保留兼容）。
        if image_gen_mode == "grid":
            import math
            billable = math.ceil(image_count / 9)
        else:
            billable = image_count
        img_cost = billable * IMAGE_PRICE.get(image_provider, 0.10)
    return round(llm_cost + img_cost, 4)


def actual_llm_cost(tokens_in: int, tokens_out: int, provider: str) -> float:
    unit = LLM_PRICE.get(provider, 0.001)
    return round((tokens_in + tokens_out) / 1000 * unit, 4)


def image_billable_units(images: list) -> float:
    """按计费口径折算图片「请求单位数」。
    九宫格模式（image 带 grid=True）：一次请求出 9 张只算 1 张钱 → 按 ceil(grid张数/9) 计；
    普通逐张/组图：每张算 1 个单位。images 可以是 dict 列表（E 产物）或带 .meta 的 ImageResult。
    """
    import math
    grid_n = other_n = 0
    for im in images:
        meta = im if isinstance(im, dict) else getattr(im, "meta", {})
        is_grid = bool((meta or {}).get("grid"))
        if is_grid:
            grid_n += 1
        else:
            other_n += 1
    return math.ceil(grid_n / 9) + other_n


def image_cost(images: list, provider: str) -> float:
    """图片实际成本 = 计费单位数 × 单价。"""
    return round(image_billable_units(images) * IMAGE_PRICE.get(provider, 0.1), 4)


def record_cost(db: Session, task_id: str, module: str, provider: str, cost: float):
    db.add(CostLog(task_id=task_id, module=module, provider=provider, cost=cost))
    db.commit()


def daily_cost(db: Session) -> float:
    """今日累计成本，用于每日上限校验。"""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    total = db.query(func.coalesce(func.sum(CostLog.cost), 0)).filter(
        CostLog.created_at >= start, CostLog.created_at < end
    ).scalar()
    return float(total or 0)


def daily_cap_reached(db: Session) -> bool:
    cfg = db.get(Config, 1)
    cap = float(cfg.daily_cost_cap) if cfg else 100.0
    return daily_cost(db) >= cap
