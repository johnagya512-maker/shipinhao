"""剪映草稿模板：把入场动画/转场/字幕样式打包成几套预设。

设计要点：
- 模板只存「枚举名字符串」，由 jianying.py 用 getattr(IntroType, name) 取剪映枚举，
  取不到就跳过（容错，沿用 _try_add_zoom 不阻断的风格）。剪映库版本差异不致崩。
- 每个镜头从模板的入场池里按「任务派生种子」选动画 → 序列确定可复现，
  重新生成草稿不会换一套动画，但不同任务/不同模板各不相同。
- 模板名用中文枚举名字符串（剪映 IntroType/TransitionType 的成员就是中文）。
"""
import random

# 模板定义：
#   intro_pool   逐镜头从中按种子选一个入场动画（枚举名）
#   transitions  相邻镜头间的转场池（枚举名），None=不加转场
#   subtitle     字幕样式 dict（传给 jianying 构造 TextStyle），None=默认样式
TEMPLATES: dict[str, dict] = {
    "none": {
        "name": "关闭",
        "desc": "不加任何动效",
        "intro_pool": [],
        "transitions": None,
        "subtitle": None,
    },
    "classic": {
        "name": "经典",
        "desc": "每张图轻微放大入场（旧版默认）",
        "intro_pool": ["放大"],
        "transitions": None,
        "subtitle": None,
    },
    "narration": {
        "name": "沉稳叙事",
        "desc": "缓慢推拉 · 叠化转场 · 适合讲书解说",
        "intro_pool": ["放大", "缩小", "轻微放大"],
        "transitions": ["叠化"],
        "subtitle": {"size": 8.0, "color": (1.0, 1.0, 1.0),
                     "border": (0.0, 0.0, 0.0)},
    },
    "lively": {
        "name": "活泼带货",
        "desc": "滑动+翻转 · 闪黑推近 · 适合带货种草",
        "intro_pool": ["放大", "向上滑动", "向下滑动", "镜像翻转"],
        "transitions": ["闪黑", "推近"],
        "subtitle": {"size": 9.0, "color": (1.0, 1.0, 1.0),
                     "border": (0.0, 0.0, 0.0)},
    },
    "cinematic": {
        "name": "电影感",
        "desc": "翻转+旋转 · 叠化拉远 · 适合故事情感",
        "intro_pool": ["镜像翻转", "旋转", "轻微放大"],
        "transitions": ["叠化", "拉远"],
        "subtitle": None,
    },
    "random": {
        "name": "随机混搭",
        "desc": "全池随机 · 每镜头不同 · 要变化就选它",
        "intro_pool": ["放大", "缩小", "轻微放大", "向上滑动", "向下滑动",
                       "镜像翻转", "旋转", "渐显"],
        "transitions": ["叠化", "闪黑", "推近", "拉远", "色彩溶解"],
        "subtitle": None,
    },
}

DEFAULT_TEMPLATE = "classic"


def list_templates() -> list[dict]:
    """前端渲染用：返回 [{key, name, desc}]，保持定义顺序。"""
    return [{"key": k, "name": v["name"], "desc": v["desc"]}
            for k, v in TEMPLATES.items()]


def _get(template_key: str) -> dict:
    return TEMPLATES.get(template_key) or TEMPLATES[DEFAULT_TEMPLATE]


def _rng(seed: int, salt: int) -> random.Random:
    """同一 (seed, salt) 永远得到同一序列 → 草稿可复现。"""
    return random.Random((seed & 0xFFFFFFFF) * 1000003 + salt)


def pick_intro(template_key: str, seed: int, index: int) -> str | None:
    """第 index 个镜头的入场动画枚举名；池为空返回 None。"""
    pool = _get(template_key)["intro_pool"]
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    return _rng(seed, index * 2 + 1).choice(pool)


def pick_transition(template_key: str, seed: int, index: int) -> str | None:
    """第 index 个镜头之后接的转场枚举名；无转场返回 None。"""
    pool = _get(template_key)["transitions"]
    if not pool:
        return None
    return _rng(seed, index * 2 + 2).choice(pool)


def subtitle_style(template_key: str) -> dict | None:
    """字幕样式参数 dict；默认样式返回 None。"""
    return _get(template_key)["subtitle"]
