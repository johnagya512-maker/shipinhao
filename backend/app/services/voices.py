"""火山引擎（豆包）大模型 TTS 音色库。

按用途分类的候选清单。音色 ID 沿用火山大模型音色命名（*_bigtts）。
⚠️ 实际可用性取决于用户火山账号的授权：库里是候选清单，
合成/试听时若返回 E6210（音色不存在或无授权），提示用户换一个或检查账号。
"""

import json
from pathlib import Path
from app.core.config import _DATA_DIR

# 分类元信息（前端左侧分类列表用，保持顺序）
# 火山(volcano)只保留探活通过的 uranus 音色，无方言/多情感授权故不列；
# 方言/多情感如需用，走 Edge TTS(yuntts_edge/edge_local)，其音色库另维护。
CATEGORIES = [
    {"key": "narration", "name": "视频配音", "desc": "解说 / 旁白 / 纪实"},
    {"key": "male", "name": "通用男声", "desc": "叙事 / 讲书"},
    {"key": "female", "name": "通用女声", "desc": "亲切 / 治愈"},
    {"key": "character", "name": "角色扮演", "desc": "戏剧感强"},
    {"key": "dialect", "name": "方言口音", "desc": "地方特色"},
    {"key": "emotion", "name": "多情感", "desc": "可调情绪"},
    {"key": "clone", "name": "声音复刻", "desc": "我的克隆音色"},
]

# 音色清单：id=火山 voice_type，name=展示名，tag=描述标签，category=分类 key
# ⚠️ 全部为 2.0/uranus 版，且已用本账号 token 经 /preview-tts 探活返回 200 实测可用。
# 老版 *_moon/*_mars 在本账号未授权(grant not found)，已全部移除。
VOICE_LIBRARY = [
    # ── 视频配音（账号授权·uranus 2.0）──
    {"id": "zh_female_gufengshaoyu_uranus_bigtts", "name": "古风少御", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_jitangnv_uranus_bigtts", "name": "鸡汤女", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_liuchangnv_uranus_bigtts", "name": "流畅女声", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_mizai_uranus_bigtts", "name": "黑猫侦探社咪仔", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_peiqi_uranus_bigtts", "name": "佩奇猪", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_shaoergushi_uranus_bigtts", "name": "少儿故事", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_tvbnv_uranus_bigtts", "name": "TVB女声", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_xinlingjitang_uranus_bigtts", "name": "心灵鸡汤", "tag": "视频配音", "category": "narration"},
    {"id": "zh_female_yingyujiaoxue_uranus_bigtts", "name": "Tina老师", "tag": "教学场景", "category": "narration"},
    {"id": "zh_male_aojiaobazong_uranus_bigtts", "name": "傲娇霸总", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_baqiqingshu_uranus_bigtts", "name": "霸气青叔", "tag": "有声阅读", "category": "narration"},
    {"id": "zh_male_dayi_uranus_bigtts", "name": "大壹", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_guanggaojieshuo_uranus_bigtts", "name": "广告解说", "tag": "通用场景", "category": "narration"},
    {"id": "zh_male_jieshuoxiaoming_uranus_bigtts", "name": "解说小明", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_kuailexiaodong_uranus_bigtts", "name": "快乐小东", "tag": "通用场景", "category": "narration"},
    {"id": "zh_male_qingcang_uranus_bigtts", "name": "擎苍", "tag": "有声阅读", "category": "narration"},
    {"id": "zh_male_ruyaqingnian_uranus_bigtts", "name": "儒雅青年", "tag": "有声阅读", "category": "narration"},
    {"id": "zh_male_ruyayichen_uranus_bigtts", "name": "儒雅逸辰", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_shenyeboke_uranus_bigtts", "name": "深夜播客", "tag": "有声阅读", "category": "narration"},
    {"id": "zh_male_sunwukong_uranus_bigtts", "name": "猴哥", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_tiancaitongsheng_uranus_bigtts", "name": "天才童声", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_xuanyijieshuo_uranus_bigtts", "name": "悬疑解说", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_yizhipiannan_uranus_bigtts", "name": "译制片男", "tag": "视频配音", "category": "narration"},
    {"id": "zh_male_youyoujunzi_uranus_bigtts", "name": "悠悠君子", "tag": "视频配音", "category": "narration"},
    # ── 通用男声（账号授权·uranus 2.0）──
    {"id": "zh_male_cixingjieshuonan_uranus_bigtts", "name": "磁性解说男声/Morgan", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_dongfanghaoran_uranus_bigtts", "name": "东方浩然", "tag": "通用", "category": "male"},
    {"id": "zh_male_fanjuanqingnian_uranus_bigtts", "name": "反卷青年", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_huolixiaoge_uranus_bigtts", "name": "活力小哥", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_kailangdidi_uranus_bigtts", "name": "开朗弟弟", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_kailangxuezhang_uranus_bigtts", "name": "开朗学长", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_lanyinmianbao_uranus_bigtts", "name": "懒音绵宝", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_liangsangmengzai_uranus_bigtts", "name": "亮嗓萌仔", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_linjiananhai_uranus_bigtts", "name": "邻家男孩", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_liufei_uranus_bigtts", "name": "刘飞", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_m191_uranus_bigtts", "name": "云舟", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_naiqimengwa_uranus_bigtts", "name": "奶气萌娃", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_qingshuangnanda_uranus_bigtts", "name": "清爽男大", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_shaonianzixin_uranus_bigtts", "name": "少年梓辛/Brayan", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_taocheng_uranus_bigtts", "name": "小天", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_wennuanahu_uranus_bigtts", "name": "温暖阿虎/Alvin", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_wenrouxiaoge_uranus_bigtts", "name": "温柔小哥", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_yangguangqingnian_uranus_bigtts", "name": "阳光青年", "tag": "通用场景", "category": "male"},
    {"id": "zh_male_yuanboxiaoshu_uranus_bigtts", "name": "渊博小叔", "tag": "通用场景", "category": "male"},
    # ── 通用女声（账号授权·uranus 2.0）──
    {"id": "zh_female_chanmeinv_uranus_bigtts", "name": "谄媚女声", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_gaolengyujie_uranus_bigtts", "name": "高冷御姐", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_jiaochuannv_uranus_bigtts", "name": "娇喘女声", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_jitangmei_uranus_bigtts", "name": "鸡汤妹妹/Hope", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_kailangjiejie_uranus_bigtts", "name": "开朗姐姐", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_kefunvsheng_uranus_bigtts", "name": "暖阳女声", "tag": "客服场景", "category": "female"},
    {"id": "zh_female_linjianvhai_uranus_bigtts", "name": "邻家女孩", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_meilinvyou_uranus_bigtts", "name": "魅力女友", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_mengyatou_uranus_bigtts", "name": "萌丫头/Cutey", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_popo_uranus_bigtts", "name": "婆婆", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_qiaopinv_uranus_bigtts", "name": "俏皮女声", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_qingchezizi_uranus_bigtts", "name": "清澈梓梓", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_qingxinnvsheng_uranus_bigtts", "name": "清新女声", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_qinqienv_uranus_bigtts", "name": "亲切女声", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_shuangkuaisisi_uranus_bigtts", "name": "爽快思思", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_sophie_uranus_bigtts", "name": "魅力苏菲", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_tianmeitaozi_uranus_bigtts", "name": "甜美桃子", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_tianmeiyueyue_uranus_bigtts", "name": "甜美悦悦", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_tiexinnvsheng_uranus_bigtts", "name": "贴心女声/Candy", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_vv_uranus_bigtts", "name": "Vivi", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_wenjingmaomao_uranus_bigtts", "name": "文静毛毛", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_wenroumama_uranus_bigtts", "name": "温柔妈妈", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_wenroushunv_uranus_bigtts", "name": "温柔淑女", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_wenrouxiaoya_uranus_bigtts", "name": "温柔小雅", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_xiaohe_uranus_bigtts", "name": "小何", "tag": "通用场景", "category": "female"},
    {"id": "zh_female_zhixingnv_uranus_bigtts", "name": "知性女声", "tag": "通用场景", "category": "female"},
    # ── 角色扮演（账号授权·uranus 2.0）──
    {"id": "zh_female_cancan_uranus_bigtts", "name": "知性灿灿", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_chunribu_uranus_bigtts", "name": "春日部姐姐", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_ganmaodianyin_uranus_bigtts", "name": "感冒电音姐姐", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_gujie_uranus_bigtts", "name": "顾姐", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_lingling_uranus_bigtts", "name": "玲玲姐姐", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_linxiao_uranus_bigtts", "name": "林潇", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_nvleishen_uranus_bigtts", "name": "女雷神", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_roumeinvyou_uranus_bigtts", "name": "柔美女友", "tag": "通用场景", "category": "character"},
    {"id": "zh_female_sajiaoxuemei_uranus_bigtts", "name": "撒娇学妹", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_wuzetian_uranus_bigtts", "name": "武则天", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_yingtaowanzi_uranus_bigtts", "name": "樱桃丸子", "tag": "角色扮演", "category": "character"},
    {"id": "zh_female_zhishuaiyingzi_uranus_bigtts", "name": "直率英子", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_lubanqihao_uranus_bigtts", "name": "鲁班七号", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_silang_uranus_bigtts", "name": "四郎", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_tangseng_uranus_bigtts", "name": "唐僧", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_xionger_uranus_bigtts", "name": "熊二", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_zhuangzhou_uranus_bigtts", "name": "庄周", "tag": "角色扮演", "category": "character"},
    {"id": "zh_male_zhubajie_uranus_bigtts", "name": "猪八戒", "tag": "角色扮演", "category": "character"},
    # ── 多情感（账号授权·emo 2.0，合成时自动带 emotion 让声音有起伏不平读）──
    # emotion 为该音色默认情绪（已实测 happy/sad/angry/neutral/excited/surprised 均可合成）。
    {"id": "zh_female_roumeinvyou_emo_v2_mars_bigtts", "name": "柔美女友", "tag": "多情感·温柔", "category": "emotion", "emotion": "happy"},
    {"id": "zh_female_meilinvyou_emo_v2_mars_bigtts", "name": "魅力女友", "tag": "多情感·甜美", "category": "emotion", "emotion": "happy"},
    {"id": "zh_female_shuangkuaisisi_emo_v2_mars_bigtts", "name": "爽快思思", "tag": "多情感·活力", "category": "emotion", "emotion": "excited"},
    {"id": "zh_female_gaolengyujie_emo_v2_mars_bigtts", "name": "高冷御姐", "tag": "多情感·冷感", "category": "emotion", "emotion": "neutral"},
    {"id": "zh_male_yangguangqingnian_emo_v2_mars_bigtts", "name": "阳光青年", "tag": "多情感·阳光", "category": "emotion", "emotion": "happy"},
    {"id": "zh_male_beijingxiaoye_emo_v2_mars_bigtts", "name": "北京小爷", "tag": "多情感·京腔", "category": "emotion", "emotion": "happy"},
    {"id": "zh_male_ruyayichen_emo_v2_mars_bigtts", "name": "儒雅逸辰", "tag": "多情感·儒雅", "category": "emotion", "emotion": "neutral"},
    {"id": "ICL_zh_female_huoponvhai_tob", "name": "活泼女孩", "tag": "多情感·活泼", "category": "emotion", "emotion": "excited"},
]


# ── 声音复刻音色支持从用户数据目录配置文件热加载 ──
# 这样平台上新增/删除复刻音色时，只需修改 data/clone_voices.json 并重启后端，
# 无需再改代码和重新打包。
_CLONE_VOICES_FILE: Path = _DATA_DIR / "clone_voices.json"

_DEFAULT_CLONE_VOICES = [
    # 已确认可用的复刻音色（按火山控制台「声音复刻 → 我的音色」中的名称与 voice_id 对应）。
    {"id": "S_eXK5czr62", "name": "于谦", "tag": "我的复刻", "category": "clone"},
    {"id": "S_fXK5czr62", "name": "阿夏", "tag": "我的复刻", "category": "clone"},
    {"id": "S_gXK5czr62", "name": "男声2", "tag": "我的复刻", "category": "clone"},
    {"id": "S_hXK5czr62", "name": "男声1", "tag": "我的复刻", "category": "clone"},
    {"id": "S_iXK5czr62", "name": "王立群音色", "tag": "我的复刻", "category": "clone"},
    {"id": "S_jXK5czr62", "name": "女朗读音色", "tag": "我的复刻", "category": "clone"},
    {"id": "S_kXK5czr62", "name": "晓松音色", "tag": "我的复刻", "category": "clone"},
]


def _filter_clone_voices(items: list[dict]) -> list[dict]:
    """过滤掉占位或未填写的复刻音色条目（id 为空或以 PLEASE_FILL 开头）。"""
    return [
        {**item, "tag": item.get("tag", "我的复刻"), "category": "clone"}
        for item in items
        if item.get("id") and not str(item.get("id", "")).startswith("PLEASE_FILL")
    ]


def _load_clone_voices() -> list[dict]:
    """从用户数据目录的 clone_voices.json 加载复刻音色。"""
    if not _CLONE_VOICES_FILE.exists():
        try:
            _CLONE_VOICES_FILE.write_text(
                json.dumps(_DEFAULT_CLONE_VOICES, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return _filter_clone_voices(_DEFAULT_CLONE_VOICES)
    try:
        data = json.loads(_CLONE_VOICES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return _filter_clone_voices(data)
    except Exception:
        pass
    return _filter_clone_voices(_DEFAULT_CLONE_VOICES)


CLONE_VOICES: list[dict] = _load_clone_voices()


# ── 云声配音 Edge TTS 音色库（微软 Edge 神经网络音色，会员免费、不限量、无需探活）──
# voice 用微软命名（zh-CN-XxxNeural）。Edge 音色对所有账号开放，故 available 恒 True。
EDGE_VOICE_LIBRARY = [
    # 视频配音 / 解说
    {"id": "zh-CN-YunjianNeural", "name": "云健", "tag": "解说·体育激情", "category": "narration"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "tag": "新闻·专业播报", "category": "narration"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "tag": "东北·旁白", "category": "narration"},
    # 通用男声
    {"id": "zh-CN-YunxiNeural", "name": "云希", "tag": "阳光·活力少年", "category": "male"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏", "tag": "可爱·童声男", "category": "male"},
    {"id": "zh-CN-YunfengNeural", "name": "云枫", "tag": "成熟·稳重", "category": "male"},
    {"id": "zh-CN-YunhaoNeural", "name": "云皓", "tag": "广告·磁性", "category": "male"},
    {"id": "zh-CN-YunjieNeural", "name": "云杰", "tag": "纪实·沉稳", "category": "male"},
    # 通用女声
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "tag": "温暖·通用首选", "category": "female"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "tag": "活泼·亲切", "category": "female"},
    {"id": "zh-CN-XiaohanNeural", "name": "晓涵", "tag": "温柔·舒缓", "category": "female"},
    {"id": "zh-CN-XiaomengNeural", "name": "晓梦", "tag": "甜美·治愈", "category": "female"},
    {"id": "zh-CN-XiaoruiNeural", "name": "晓睿", "tag": "成熟·知性", "category": "female"},
    {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱", "tag": "干练·御姐", "category": "female"},
    {"id": "zh-CN-XiaomoNeural", "name": "晓墨", "tag": "情感·细腻", "category": "female"},
    {"id": "zh-CN-XiaozhenNeural", "name": "晓甄", "tag": "认真·清晰", "category": "female"},
    # 方言
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "tag": "陕西·方言", "category": "dialect"},
    {"id": "zh-HK-HiuMaanNeural", "name": "曉曼", "tag": "粤语·女声", "category": "dialect"},
    {"id": "zh-HK-HiuGaaiNeural", "name": "曉佳", "tag": "粤语·女声", "category": "dialect"},
    {"id": "zh-HK-WanLungNeural", "name": "雲龍", "tag": "粤语·男声", "category": "dialect"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻", "tag": "台湾·女声", "category": "dialect"},
    {"id": "zh-TW-HsiaoYuNeural", "name": "曉雨", "tag": "台湾·女声", "category": "dialect"},
    {"id": "zh-TW-YunJheNeural", "name": "雲哲", "tag": "台湾·男声", "category": "dialect"},
]


def library_for(provider: str | None):
    """按 TTS 供应商返回 (音色清单, 是否需要探活)。
    yuntts_edge 用 Edge 音色库且无需探活（恒可用）；其余用火山候选库（需探活）。
    复刻音色从用户数据目录的 clone_voices.json 热加载。"""
    if provider in ("yuntts_edge", "edge_local"):
        return EDGE_VOICE_LIBRARY, False
    return VOICE_LIBRARY + CLONE_VOICES, True


def categories_for(library) -> list[dict]:
    """只返回该库里实际有音色的分类，避免前端显示空分类（如火山无方言/多情感）。"""
    present = {v["category"] for v in library}
    return [c for c in CATEGORIES if c["key"] in present]


# voice_id → 该音色支持的默认 emotion（仅火山多情感音色有；其余 None）。
# 供 TTS 合成时自动带上 enable_emotion+emotion，让声音有情绪起伏而非平读。
_VOICE_EMOTION = {v["id"]: v["emotion"] for v in VOICE_LIBRARY if v.get("emotion")}


def emotion_for(voice_id: str | None) -> str | None:
    """返回该音色的默认 emotion（火山多情感音色），非情感音色返回 None。"""
    if not voice_id:
        return None
    return _VOICE_EMOTION.get(voice_id)


# ── 按账号探活：音色授权是按火山账号的，库里是候选清单，实际可用性需用当前凭证试合成 ──
# 探活结果按「凭证指纹」缓存在内存：同一套 appid+key 只测一轮，凭证变了重测。
# 探活在后台线程跑（逐个合成一句短文本，慢），接口先返回缓存（首次全为 None=未知）。
import hashlib
import threading
import logging

_logger = logging.getLogger("uvicorn")
# {fingerprint: {voice_id: bool}}  bool=可用; 缺 key=尚未测出
_probe_cache: dict[str, dict[str, bool]] = {}
_probe_running: set[str] = set()
_probe_lock = threading.Lock()


def _fingerprint(appid: str | None, api_key: str | None) -> str:
    raw = f"{appid or ''}:{api_key or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def availability(appid: str | None, api_key: str | None) -> dict[str, bool]:
    """返回当前凭证下已探出的音色可用性（voice_id -> bool）。未探出的不在 dict 里。"""
    if not api_key:
        return {}
    return dict(_probe_cache.get(_fingerprint(appid, api_key), {}))


def ensure_probe(provider: str, appid: str | None, api_key: str | None) -> None:
    """确保当前凭证的探活已启动（幂等）。已有缓存或正在跑则跳过，否则起后台线程逐个探活。"""
    if not api_key:
        return
    fp = _fingerprint(appid, api_key)
    with _probe_lock:
        if fp in _probe_cache or fp in _probe_running:
            return
        _probe_running.add(fp)

    def _run():
        from app.services import tts as tts_svc
        result: dict[str, bool] = {}
        for v in VOICE_LIBRARY + CLONE_VOICES:
            try:
                tts_svc.test_connectivity(provider, api_key, voice=v["id"],
                                          appid=appid, timeout=20.0)
                result[v["id"]] = True
            except tts_svc.TTSUnavailable:
                # 凭证整体不可用（无 key），整轮放弃，不缓存（下次可重试）
                with _probe_lock:
                    _probe_running.discard(fp)
                return
            except Exception:
                result[v["id"]] = False
        with _probe_lock:
            _probe_cache[fp] = result
            _probe_running.discard(fp)
        n_ok = sum(1 for x in result.values() if x)
        _logger.info("音色探活完成: %d/%d 可用 (凭证 %s)", n_ok, len(result), fp)

    threading.Thread(target=_run, daemon=True).start()
