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
    {"id": "zh_female_roumeinvyou_emo_v2_mars_bigtts", "name": "柔美女友", "tag": "可调情绪", "category": "emotion", "emotion": "tender"},
    {"id": "zh_male_beijingxiaoye_emo_mars_bigtts", "name": "北京小爷", "tag": "可调情绪", "category": "emotion", "emotion": "happy"},
    {"id": "zh_female_jiaohuanvsheng_emo_mars_bigtts", "name": "娇憨女声", "tag": "可调情绪", "category": "emotion", "emotion": "happy"},
    {"id": "zh_male_yangguangqingnian_emo_v2_mars_bigtts", "name": "阳光青年(情感)", "tag": "可调情绪", "category": "emotion", "emotion": "happy"},
    {"id": "zh_female_meilinvyou_emo_v2_mars_bigtts", "name": "魅力女友(情感)", "tag": "可调情绪", "category": "emotion", "emotion": "tender"},
    {"id": "zh_male_junlangnanyou_emo_v2_mars_bigtts", "name": "俊朗男友", "tag": "可调情绪", "category": "emotion", "emotion": "tender"},

    # ── 视频配音 / 解说旁白（扩充）──
    {"id": "zh_male_changtianyi_mars_bigtts", "name": "长天意", "tag": "纪录片·大气旁白", "category": "narration"},
    {"id": "zh_female_zhixingwenwan_mars_bigtts", "name": "知性温婉", "tag": "知识·讲解", "category": "narration"},
    {"id": "zh_male_baqiqingshu_mars_bigtts", "name": "霸气青叔", "tag": "硬核·解说", "category": "narration"},
    {"id": "zh_male_qingyiyuxuan_mars_bigtts", "name": "清逸御轩", "tag": "儒雅·讲述", "category": "narration"},
    {"id": "zh_female_kailangjiejie_moon_bigtts", "name": "开朗姐姐", "tag": "亲和·讲解", "category": "narration"},
    {"id": "zh_male_dongfanghaoran_moon_bigtts", "name": "东方浩然", "tag": "正气·播报", "category": "narration"},

    # ── 通用男声（扩充）──
    {"id": "zh_male_yuanboxiaoshu_moon_bigtts", "name": "渊博小叔", "tag": "成熟·磁性", "category": "male"},
    {"id": "zh_male_ruyayichen_moon_bigtts", "name": "儒雅亦辰", "tag": "温文·斯文", "category": "male"},
    {"id": "zh_male_qingshuangnanda_mars_bigtts", "name": "清爽男大", "tag": "青春·阳光", "category": "male"},
    {"id": "zh_male_haoyuxiaoge_moon_bigtts", "name": "浩宇小哥", "tag": "亲切·邻家", "category": "male"},
    {"id": "zh_male_guozhoudege_moon_bigtts", "name": "果周德哥", "tag": "稳重·大哥", "category": "male"},

    # ── 通用女声（扩充）──
    {"id": "zh_female_linjianvhai_moon_bigtts", "name": "邻家女孩", "tag": "甜美·邻家", "category": "female"},
    {"id": "zh_female_tianmeiyueyue_moon_bigtts", "name": "甜美悦悦", "tag": "甜美·活泼", "category": "female"},
    {"id": "zh_female_kefuyuanyuan_moon_bigtts", "name": "客服圆圆", "tag": "专业·亲和", "category": "female"},
    {"id": "zh_female_wenroushunv_moon_bigtts", "name": "温柔淑女", "tag": "温柔·端庄", "category": "female"},
    {"id": "zh_female_popo_mars_bigtts", "name": "婆婆", "tag": "年长·亲切", "category": "female"},
    {"id": "zh_female_shaoergushi_mars_bigtts", "name": "少儿故事", "tag": "童趣·讲故事", "category": "female"},

    # ── 角色扮演（扩充）──
    {"id": "zh_male_naiqimengwa_mars_bigtts", "name": "奶气萌娃", "tag": "童声·萌", "category": "character"},
    {"id": "zh_female_baotuanzi_mars_bigtts", "name": "包子姐", "tag": "搞笑·活泼", "category": "character"},
    {"id": "zh_male_dongfangjianke_moon_bigtts", "name": "东方剑客", "tag": "武侠·豪迈", "category": "character"},
    {"id": "zh_female_gufengshaoyu_moon_bigtts", "name": "古风少御", "tag": "古风·清冷", "category": "character"},
    {"id": "zh_female_wuzetian_moon_bigtts", "name": "霸气女皇", "tag": "威严·气场", "category": "character"},
    {"id": "zh_female_sajiaonvyou_mars_bigtts", "name": "撒娇女友", "tag": "甜腻·撒娇", "category": "character"},

    # ── 方言口音（扩充）──
    {"id": "zh_female_sichuanxiaomei_moon_bigtts", "name": "四川小妹", "tag": "川味·麻辣", "category": "dialect"},
    {"id": "zh_male_dongbeilaotie_moon_bigtts", "name": "东北老铁", "tag": "东北·豪爽", "category": "dialect"},
    {"id": "zh_male_henanboge_moon_bigtts", "name": "河南博哥", "tag": "河南·中原", "category": "dialect"},
    {"id": "zh_female_shanghainvsheng_moon_bigtts", "name": "上海女声", "tag": "沪语·软糯", "category": "dialect"},
    {"id": "zh_male_guangxinanda_moon_bigtts", "name": "广西男大", "tag": "桂味·方言", "category": "dialect"},
]


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
    yuntts_edge 用 Edge 音色库且无需探活（恒可用）；其余用火山候选库（需探活）。"""
    if provider in ("yuntts_edge", "edge_local"):
        return EDGE_VOICE_LIBRARY, False
    return VOICE_LIBRARY, True


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
        for v in VOICE_LIBRARY:
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
