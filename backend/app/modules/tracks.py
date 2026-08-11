"""赛道配置。把赛道相关的差异（改写风格/画风/合规词库）集中到这里，
切赛道只改配置不动代码。对应手册「模板远程下发」思路。
"""

# ── 画风配置：三层（prefix/suffix/negative），切风格 1 秒 ──
IMAGE_STYLES = {
    "古风电影": {
        "prefix": "8K高清古风电影感画面，真实物理光影，",
        "suffix": "，电影级布光，景深，服饰道具纹理细节清晰，人物五官端正肢体协调，画面克制自然",
        "negative": "现代元素，CG渲染，二次元，卡通，过度后期，五官扭曲，畸形肢体，文字，水印，低质量，变形",
    },
    "工笔古画": {
        "prefix": "中国工笔画风格，",
        "suffix": "，细腻笔触，绢本设色，传统国画",
        "negative": "现代元素，照片感，文字，水印，低质量",
    },
    "水墨写意": {
        "prefix": "中国水墨写意画，",
        "suffix": "，留白意境，墨色浓淡，宣纸质感",
        "negative": "彩色照片，现代元素，文字，水印",
    },
    "古风工笔画": {
        "prefix": "中国古风工笔画，工笔重彩人物，",
        "suffix": "，线条精致，矿物颜料设色，衣纹飘逸，古典叙事感，绢本质感",
        "negative": "现代元素，照片感，3D渲染，文字，水印，低质量，变形",
    },
    "新国风水彩": {
        "prefix": "新国风水彩插画，水彩晕染，国潮配色，",
        "suffix": "，淡雅清新，现代插画构图，柔和渐变，留白意境",
        "negative": "写实照片，3D渲染，浓重油画感，文字，水印，低质量，杂乱",
    },
    "复古胶片": {
        "prefix": "复古胶片摄影，真实相机拍摄，",
        "suffix": "，胶片颗粒质感，暖黄色调，年代感，服饰道具纹理清晰，人物五官端正肢体协调，画面克制自然",
        "negative": "现代数码感，CG渲染，过度后期，五官扭曲，畸形肢体，文字，水印，过曝",
    },
    "温暖暖色": {  # 健康书单沿用
        "prefix": "",
        "suffix": "，柔和暖色调，温暖亲切，简洁清晰",
        "negative": "暗黑，恐怖，文字，水印，低质量",
    },
    "写实彩色": {
        "prefix": "8K高清写实摄影，真实相机拍摄，真实物理光影，",
        "suffix": "，自然光影，服饰道具环境纹理细节清晰，人物五官端正肢体协调，电影感构图，画面克制不过度美颜",
        "negative": "插画感，卡通，CG渲染，二次元，厚涂，过度后期，过度美颜，五官扭曲，畸形肢体，文字，水印，低质量，变形",
    },
    "黑白纪实": {
        "prefix": "8K高清黑白纪实摄影，真实相机拍摄，真实物理光影，",
        "suffix": "，强烈明暗对比，轻微胶片颗粒质感，历史厚重感，服饰道具环境纹理细节清晰，人物五官端正肢体协调无畸形，画面克制不过度美颜",
        "negative": "彩色，现代数码感，CG渲染，二次元，卡通，厚涂插画，过度后期，过度美颜，五官扭曲，畸形肢体，文字，水印，过曝",
    },
    "皮克斯3D": {
        "prefix": "皮克斯风格3D渲染，",
        "suffix": "，柔和体积光，圆润造型，温暖色彩，电影级渲染",
        "negative": "写实照片，恐怖，文字，水印，低质量",
    },
    "吉卜力动画": {
        "prefix": "吉卜力动画风格，",
        "suffix": "，手绘水彩质感，清新自然，治愈氛围，细腻背景",
        "negative": "写实照片，3D渲染，文字，水印，低质量",
    },
    "极简插画": {
        "prefix": "极简扁平插画，",
        "suffix": "，简洁构图，柔和配色，现代设计感，留白",
        "negative": "写实照片，复杂细节，文字，水印，杂乱",
    },
    "温馨绘本": {
        "prefix": "儿童绘本插画风格，",
        "suffix": "，柔和蜡笔质感，童趣可爱，明亮温暖，圆润线条",
        "negative": "写实，恐怖，暗黑，文字，水印，低质量",
    },
    "明亮商业": {
        "prefix": "明亮商业摄影，",
        "suffix": "，干净背景，产品质感突出，专业打光，清晰锐利",
        "negative": "杂乱背景，昏暗，文字，水印，低质量，变形",
    },
    "古典油画": {
        "prefix": "古典油画风格，厚涂笔触，丰富肌理，",
        "suffix": "，伦勃朗式布光，明暗对比强烈，深沉饱满色调，画布质感，古典写实",
        "negative": "照片感，3D渲染，卡通，扁平，文字，水印，低质量，变形",
    },
    "印象派油画": {
        "prefix": "印象派外光派油画（En plein air），厚涂可见笔触作为质感，斑斓光色，主体清晰、",
        "suffix": "，人物五官分明、面部结构准确，主体轮廓清楚，柔和自然光（室内借窗光/户外天光），光影通透，色彩明快有层次，莫奈/雷诺阿式笔触，厚涂颜料肌理与画布质感，清晰耐看",
        "negative": "糊成一团，面部模糊，五官不清，色块堆砌，过度抽象，看不清主体，照片感，3D渲染，塑料感，扁平，文字，水印，低质量，变形",
    },
}


# ── 赛道配置 ──
TRACKS = {
    "character_story": {
        "name": "人物故事",
        "default_style": "古风电影",
        # B 改写风格关键词，注入 B 提示词
        "rewrite_focus": "戏剧化人物叙事，强悬念开头，情节起伏，结尾引发评论互动",
        # 标题钩子公式（注入 TITLE_TAGS）：决定该赛道标题往哪个钩子使劲
        "title_style": "命运反差 / 悬念缺口——用身份落差、不为人知的秘密、结局悬念抓人。",
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
        "title_style": "反常识 + 痛点 + 利益点——打破养生误区、戳中关切、给出确定性好处，"
                       "但口吻克制不恐吓、不做疗效承诺。",
        # 健康书单：医疗合规词库（规范 5.15-5.17 不规范医疗科普）
        "compliance_high": [
            # 疗效承诺/绝对化（规范 5.16）
            "治愈", "根治", "100%有效", "包好", "彻底解决", "降血压",
            "替代药物", "神效", "奇迹", "祖传秘方", "立竿见影",
            "一招见效", "根治根除", "包治百病", "灵丹妙药",
            # 不规范医疗（规范 5.15-5.17）
            "偏方", "妙招", "食疗治病", "茶饮治病", "排毒",
            "食物相克", "食物生克", "治癌症", "宫廷秘方",
            # 诱导导流（规范 4.4）
            "加微信", "私信我", "点击主页", "关注领取", "扫码进群",
            "点赞领取", "分享抽奖", "转发有礼", "集赞", "拆礼盒",
            # 胁迫煽动（规范 4.4.2）
            "不点赞不是", "不转不是", "是中国人就",
            # 绝对化/夸大（规范 5.5）
            "暴瘦", "逆转", "飙升",
        ],
        "compliance_warn": [
            "震惊", "速转", "紧急通知", "不看吃亏", "错过后悔",
            "赶紧转", "火速转发", "致命",
        ],
        "image_subject": "健康生活",
    },
    "culture_science": {
        "name": "文化科普",
        "default_style": "工笔古画",
        "rewrite_focus": "华夏文化/传统民俗通俗化讲解，知识点清晰，引发文化认同与好奇",
        "title_style": "好奇缺口 / 颠覆认知——用你不知道的真相、原来如此、纠正常识抓人。",
        "compliance_high": ["反动", "邪教", "封建迷信宣扬"],
        "compliance_warn": ["杜撰史实", "以偏概全"],
        "image_subject": "传统文化场景",
    },
    "kids_picturebook": {
        "name": "绘本故事",
        "default_style": "温馨绘本",
        "rewrite_focus": "儿童睡前故事，语言简单温柔，节奏舒缓，传递正向价值观",
        "title_style": "温柔代入 / 亲子共鸣——用睡前感、情绪治愈、给孩子的道理抓人。",
        "compliance_high": ["血腥", "暴力", "恐怖", "惊悚"],
        "compliance_warn": ["负面情绪", "成人话题"],
        "image_subject": "童话角色",
    },
    "ecommerce": {
        "name": "电商带货",
        "default_style": "明亮商业",
        "rewrite_focus": "产品种草/好物推荐，痛点切入，卖点清晰，强行动号召",
        "title_style": "痛点 + 利益 + 紧迫——用解决具体烦恼、效果可感、手慢无抓人。",
        "compliance_high": ["最", "国家级", "绝对", "100%", "根治"],
        "compliance_warn": ["夸大功效", "虚假承诺", "诱导消费"],
        "image_subject": "产品实拍",
    },
    "soul_chicken": {
        "name": "心灵鸡汤",
        "default_style": "极简插画",
        "rewrite_focus": "情感治愈/励志感悟，金句共鸣，节奏舒缓，引发评论与转发",
        "title_style": "金句共鸣 / 情绪戳心——用扎心真相、人生顿悟、说出你的心声抓人。",
        "compliance_high": ["反动", "邪教"],
        "compliance_warn": ["贩卖焦虑", "极端价值观"],
        "image_subject": "意境画面",
    },
    "folk_tale": {
        "name": "民间故事",
        "default_style": "古风电影",
        "rewrite_focus": "虚构传说/因果寓言，悬念叙事，因果分明，结尾引发讨论",
        "title_style": "悬念 + 因果 / 善恶报应——用诡异开场、因果反转、警示抓人。",
        "compliance_high": ["血腥", "暴力血腥", "反动", "邪教", "封建迷信宣扬"],
        "compliance_warn": ["宣扬迷信", "因果报应过度"],
        "image_subject": "传说场景",
    },
    "food_探店": {
        "name": "美食探店",
        "default_style": "写实彩色",
        "rewrite_focus": "城市烟火气，美食诱惑力描述，探店体验感，引发到店欲望",
        "title_style": "馋人 / 避坑 / 本地秘藏——用垂涎描述、踩雷预警、内行才知道抓人。",
        "compliance_high": ["最好吃", "第一", "绝对"],
        "compliance_warn": ["夸大", "虚假评价"],
        "image_subject": "美食特写",
    },
    "general": {
        "name": "通用故事",
        "default_style": "写实彩色",
        "rewrite_focus": "通用短视频口播改写，开头钩子，叙事流畅，结尾引导互动",
        "title_style": "悬念 + 反差——通用钩子，制造好奇、勾起点击。"
                       "如「没想到结局会是这样」「这件事，90%的人都做错了」",
        "compliance_high": ["血腥", "暴力血腥", "反动", "邪教"],
        "compliance_warn": [],
        "image_subject": "场景画面",
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


# ── 各赛道出图节奏：每张图停留秒数 ──
# 面向视频号中老年观众为基调（看清画面+跟上字幕，不过快不过慢）：
# 叙事/慢品类放慢（8-9 秒），知识/带货/美食类信息密、切换勤（6-7 秒）。
_SECONDS_PER_IMAGE = {
    "character_story": 8,   # 人物故事：叙事，看清人物
    "health_book": 8,       # 健康书单：中老年主力，舒适
    "culture_science": 7,   # 文化科普：信息较密
    "kids_picturebook": 9,  # 绘本故事：慢，给孩子看图
    "ecommerce": 6,         # 电商带货：节奏快、抓注意力
    "soul_chicken": 8,      # 心灵鸡汤：慢品
    "folk_tale": 9,         # 民间故事：娓娓道来
    "food_探店": 6,         # 美食探店：画面丰富、切换勤
    "general": 7,           # 通用：折中
}
DEFAULT_SECONDS_PER_IMAGE = 8


def seconds_per_image(track_key: str | None) -> float:
    """取某赛道的每张图停留秒数（决定出图节奏/张数）。未配置赛道用默认值。"""
    return _SECONDS_PER_IMAGE.get(track_key or DEFAULT_TRACK, DEFAULT_SECONDS_PER_IMAGE)


def image_ratio_for(task) -> str:
    """出图比例：版式 center_h（竖屏中央横图）强制出 16:9 横图，与画布(9:16)解耦；
    其余版式沿用任务自身的 aspect_ratio。供编排/重生/重组三处统一调用。"""
    if getattr(task, "layout", "full") == "center_h":
        return "16:9"
    return getattr(task, "aspect_ratio", None) or "9:16"


