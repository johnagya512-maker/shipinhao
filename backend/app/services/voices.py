"""火山引擎（豆包）大模型 TTS 音色库。

按用途分类的候选清单。音色 ID 沿用火山大模型音色命名（*_bigtts）。
⚠️ 实际可用性取决于用户火山账号的授权：库里是候选清单，
合成/试听时若返回 E6210（音色不存在或无授权），提示用户换一个或检查账号。
"""

# 分类元信息（前端左侧分类列表用，保持顺序）
CATEGORIES = [
    {"key": "narration", "name": "视频配音", "desc": "解说 / 旁白 / 纪实"},
    {"key": "male", "name": "通用男声", "desc": "叙事 / 讲书"},
    {"key": "female", "name": "通用女声", "desc": "亲切 / 治愈"},
    {"key": "character", "name": "角色扮演", "desc": "戏剧感强"},
    {"key": "dialect", "name": "方言口音", "desc": "地方特色"},
    {"key": "emotion", "name": "多情感", "desc": "可调情绪"},
]

# 音色清单：id=火山 voice_type，name=展示名，tag=描述标签，category=分类 key
VOICE_LIBRARY = [
    # ── 视频配音 / 解说旁白 ──
    {"id": "zh_male_M392_conversation_wvae_bigtts", "name": "沉稳解说", "tag": "叙事·讲书", "category": "narration"},
    {"id": "zh_male_jieshuonansheng_mars_bigtts", "name": "解说男声", "tag": "纪实·旁白", "category": "narration"},
    {"id": "zh_female_jitangmeimei_moon_bigtts", "name": "鸡汤妹妹", "tag": "情感·治愈解说", "category": "narration"},
    {"id": "zh_male_silang_moon_bigtts", "name": "磁性四郎", "tag": "低沉·质感旁白", "category": "narration"},
    {"id": "zh_female_zhixingnvsheng_mars_bigtts", "name": "知性女声", "tag": "知识·讲解", "category": "narration"},

    # ── 通用男声 ──
    {"id": "zh_male_wennuanahu_moon_bigtts", "name": "温暖阿虎", "tag": "温暖·亲和", "category": "male"},
    {"id": "zh_male_shaonianzixin_moon_bigtts", "name": "少年自信", "tag": "年轻·清亮", "category": "male"},
    {"id": "zh_male_qingcang_moon_bigtts", "name": "擎苍", "tag": "浑厚·大气", "category": "male"},
    {"id": "zh_male_yangguangqingnian_moon_bigtts", "name": "阳光青年", "tag": "活力·阳光", "category": "male"},

    # ── 通用女声 ──
    {"id": "zh_female_wanwanxiaohe_moon_bigtts", "name": "温柔小荷", "tag": "亲切·治愈", "category": "female"},
    {"id": "zh_female_qingxinnvsheng_mars_bigtts", "name": "清新女声", "tag": "轻快·明亮", "category": "female"},
    {"id": "zh_female_shuangkuaisisi_moon_bigtts", "name": "爽快思思", "tag": "活泼·带货", "category": "female"},
    {"id": "zh_female_tianmeixiaoyuan_moon_bigtts", "name": "甜美小源", "tag": "甜美·邻家", "category": "female"},
    {"id": "zh_female_wenrouxiaoya_moon_bigtts", "name": "温柔小雅", "tag": "温柔·舒缓", "category": "female"},

    # ── 角色扮演 ──
    {"id": "zh_male_jingqiangkanye_moon_bigtts", "name": "京腔侃爷", "tag": "幽默·接地气", "category": "character"},
    {"id": "zh_female_gaolengyujie_moon_bigtts", "name": "高冷御姐", "tag": "气场·御姐", "category": "character"},
    {"id": "zh_male_aojiaobazong_moon_bigtts", "name": "傲娇霸总", "tag": "戏剧·霸总", "category": "character"},
    {"id": "zh_female_meilinvyou_moon_bigtts", "name": "魅力女友", "tag": "撒娇·亲密", "category": "character"},

    # ── 方言口音 ──
    {"id": "zh_male_jingqiangkanye_moon_bigtts_dialect", "name": "北京话", "tag": "京味·儿化", "category": "dialect"},
    {"id": "zh_female_wankouxiaohe_moon_bigtts", "name": "湾区小何", "tag": "台湾腔", "category": "dialect"},
    {"id": "zh_male_yuangulaoyeye_moon_bigtts", "name": "粤语老爷", "tag": "粤语·港味", "category": "dialect"},

    # ── 多情感 ──
    {"id": "zh_female_roumeinvyou_emo_v2_mars_bigtts", "name": "柔美女友", "tag": "可调情绪", "category": "emotion"},
    {"id": "zh_male_beijingxiaoye_emo_mars_bigtts", "name": "北京小爷", "tag": "可调情绪", "category": "emotion"},
    {"id": "zh_female_jiaohuanvsheng_emo_mars_bigtts", "name": "娇憨女声", "tag": "可调情绪", "category": "emotion"},
]
