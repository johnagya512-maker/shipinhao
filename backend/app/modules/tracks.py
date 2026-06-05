"""赛道配置。把赛道相关的差异（改写风格/画风/合规词库）集中到这里，
切赛道只改配置不动代码。对应手册「模板远程下发」思路。
"""

# ── 画风配置：三层（prefix/suffix/negative），切风格 1 秒 ──
IMAGE_STYLES = {
    "古风电影": {
        "prefix": "古风电影感画面，",
        "suffix": "，电影级布光，景深，4k，竖版1080x1920",
        "negative": "现代元素，文字，水印，低质量，变形",
    },
    "工笔古画": {
        "prefix": "中国工笔画风格，",
        "suffix": "，细腻笔触，绢本设色，传统国画，竖版1080x1920",
        "negative": "现代元素，照片感，文字，水印，低质量",
    },
    "水墨写意": {
        "prefix": "中国水墨写意画，",
        "suffix": "，留白意境，墨色浓淡，宣纸质感，竖版1080x1920",
        "negative": "彩色照片，现代元素，文字，水印",
    },
    "复古胶片": {
        "prefix": "复古胶片摄影，",
        "suffix": "，颗粒质感，暖黄色调，年代感，竖版1080x1920",
        "negative": "现代数码感，文字，水印，过曝",
    },
    "温暖暖色": {  # 健康书单沿用
        "prefix": "",
        "suffix": "，柔和暖色调，温暖亲切，简洁清晰，竖版1080x1920",
        "negative": "暗黑，恐怖，文字，水印，低质量",
    },
}


# ── 赛道配置 ──
TRACKS = {
    "character_story": {
        "name": "人物故事",
        "default_style": "古风电影",
        # B 改写风格关键词，注入 B 提示词
        "rewrite_focus": "戏剧化人物叙事，强悬念开头，情节起伏，结尾引发评论互动",
        # 合规：人物故事放松医疗词，重点是史实/敏感
        "compliance_high": ["血腥", "暴力血腥", "反动", "邪教"],
        "compliance_warn": ["影射", "杜撰史实"],
        # 历史人物题材，配图主体是人物与历史场景
        "image_subject": "历史人物",
    },
    "health_book": {
        "name": "健康书单",
        "default_style": "温暖暖色",
        "rewrite_focus": "健康知识通俗化，案例共鸣，温和带货",
        # 健康书单：保留原有医疗合规词库（在 text_modules 内置）
        "compliance_high": [],   # 空表示用 text_modules 默认医疗词库
        "compliance_warn": [],
        "image_subject": "健康生活",
    },
}

DEFAULT_TRACK = "character_story"


def get_track(track_key: str | None) -> dict:
    return TRACKS.get(track_key or DEFAULT_TRACK, TRACKS[DEFAULT_TRACK])


def get_style(style_key: str | None, track_key: str | None = None) -> dict:
    """取画风配置。style_key 为空时用赛道默认画风。"""
    if not style_key:
        style_key = get_track(track_key)["default_style"]
    return IMAGE_STYLES.get(style_key, IMAGE_STYLES["古风电影"])

