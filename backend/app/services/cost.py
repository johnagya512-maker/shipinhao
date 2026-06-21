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
    "gpt-image": 0.58,   # gpt-image-2（中转站如兔子API），约 $0.0828/次 ≈ 0.58 元，每次请求计费
}


def _is_gpt_model(model: str | None) -> bool:
    """模型名带 gpt/dall 即 gpt-image 协议（与 image.py:_is_gpt 同口径）。
    gpt-image 关键差异：失败/被拒的请求中转站【照样收费】，不能像豆包那样跳过不计。"""
    m = (model or "").lower()
    return "gpt" in m or "dall" in m

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
                  image_gen_mode: str = "per_image",
                  image_unit_price: float | None = None) -> float:
    """提交前预估成本（上限估计）。image_count 为 None 时按逐字稿长度动态推算。
    配图按实际生成模式计费：per_image 按实际张数、grid 按 ceil(张数/9) 折算请求数；
    单价优先用 image_unit_price（中转站真实单价），为空/<=0 才回退内置 IMAGE_PRICE。"""
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
        if image_gen_mode == "grid":
            import math
            billable = math.ceil(image_count / 9)
        else:
            billable = image_count
        img_cost = billable * _image_unit(image_provider, image_unit_price)
    return round(llm_cost + img_cost, 4)


def actual_llm_cost(tokens_in: int, tokens_out: int, provider: str) -> float:
    unit = LLM_PRICE.get(provider, 0.001)
    return round((tokens_in + tokens_out) / 1000 * unit, 4)


def image_billable_units(images: list, model: str | None = None) -> float:
    """按计费口径折算图片「请求单位数」。
    九宫格模式（image 带 grid=True）：一次请求出 9 张只算 1 张钱 → 按 ceil(grid张数/9) 计；
    普通逐张/组图：每张算 1 个单位。images 可以是 dict 列表（E 产物）或带 .meta 的 ImageResult。

    失败占位（fallback=True）是否计费，按协议区分：
    - 豆包等：失败请求中转站收 $0 → 跳过不计（实测 SensitiveContentDetected 账单 0.00000）。
    - gpt-image（gpt/dall 模型）：失败/被拒请求【照样收费】→ 必须计入，否则严重低估成本
      （task_69f9678c4942 实测：第4组九宫格失败仍被兔子API按 $0.0828/次扣费）。
    """
    import math
    is_gpt = _is_gpt_model(model)
    grid_n = other_n = 0
    for im in images:
        meta = im if isinstance(im, dict) else getattr(im, "meta", {})
        meta = meta or {}
        if meta.get("fallback") and not is_gpt:
            continue  # 豆包等：占位=失败降级，中转站不收费，跳过
        # gpt-image：占位图对应的请求照样发生过、照样扣钱，计入。
        is_grid = bool(meta.get("grid"))
        if is_grid:
            grid_n += 1
        else:
            other_n += 1
    return math.ceil(grid_n / 9) + other_n


def _image_unit(provider: str, override: float | None = None) -> float:
    """配图单价：优先用 override（中转站真实单价，>0 才算有效），否则回退内置缺省价。"""
    try:
        if override is not None and float(override) > 0:
            return float(override)
    except (TypeError, ValueError):
        pass
    return IMAGE_PRICE.get(provider, 0.10)


def image_cost(images: list, provider: str, unit_price: float | None = None,
               model: str | None = None) -> float:
    """图片实际成本 = 计费单位数 × 单价。
    unit_price 非空且>0 时用它（中转站真实单价，用户在配置中心填）。
    否则回退内置缺省价：gpt-image 模型用 gpt-image 价（0.58），其余按 provider。
    gpt-image 协议下失败请求也计入单位数（中转站照收费）。"""
    units = image_billable_units(images, model=model)
    price_key = "gpt-image" if _is_gpt_model(model) else provider
    return round(units * _image_unit(price_key, unit_price), 4)


def record_cost(db: Session, task_id: str, module: str, provider: str, cost: float):
    db.add(CostLog(task_id=task_id, module=module, provider=provider, cost=cost))
    db.commit()


def rebill_module(db: Session, task, module: str, provider: str, cost: float):
    """重算某模块成本（替换而非累加）。用于图片重试/重新组图：当前 E 产物的实际成本
    覆盖该模块旧账，避免历史叠加把 total_cost 越滚越高、误判超限。
    做法：删该 task+module 的旧 CostLog → 记一笔新的 → total_cost 重算为各模块当前之和。"""
    db.query(CostLog).filter(CostLog.task_id == task.id, CostLog.module == module).delete()
    db.add(CostLog(task_id=task.id, module=module, provider=provider, cost=cost))
    db.flush()
    total = db.query(func.coalesce(func.sum(CostLog.cost), 0)).filter(
        CostLog.task_id == task.id).scalar()
    task.total_cost = float(total or 0)
    db.commit()
    return task.total_cost


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
