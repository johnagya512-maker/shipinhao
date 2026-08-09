"""模块 E：配图编排。含 D→E 选主书（PRD 9.3）与提示词变量组装（9.4）。
按赛道画风（tracks.IMAGE_STYLES 三层配置）生成配图。
"""
from pathlib import Path
from app.services.image import generate_image, placeholder_result, ImageError, ImageResult
from app.modules import tracks

IMG_RETRY = 2
# gpt-image 协议：失败/超时请求中转站【照样收费】，故瞬时故障重试收紧到 1 次（最多 2 次请求），
# 不像豆包失败 $0 可放心重试 3 次。避免一张顽固失败的图按次烧钱。
IMG_RETRY_GPT = 1
# ⚠️ 断连重试已收紧为 0：实测中转站(tu-zi)断连也扣费！重试=反复烧钱。
# 之前给 1 次预算，导致断连后多烧一次钱仍拿不到图。
# 现改为 0 次：断连直接占位，用户在画廊手动"重新组图"，至少不自动反复扣费。
IMG_DISCONNECT_RETRY = 0


def _img_retry_for(model: str | None) -> int:
    """按模型定瞬时故障重试次数：gpt/dall 失败也计费 → 收紧；其余按默认。"""
    m = (model or "").lower()
    return IMG_RETRY_GPT if ("gpt" in m or "dall" in m) else IMG_RETRY

# 配图节奏：默认约 8 秒一张（中老年友好）；实际秒/张由赛道决定（见 tracks.seconds_per_image）。
# 张数随时长动态变，封顶避免成本失控；下限保证封面+内容+CTA 基本结构。
SECONDS_PER_IMAGE = 8
MIN_IMAGES = 3
MAX_IMAGES = 20


def count_for_duration(total_duration: float,
                       seconds_per_image: float = SECONDS_PER_IMAGE,
                       min_images: int = MIN_IMAGES,
                       max_images: int = MAX_IMAGES) -> int:
    """按总时长推配图张数（约 seconds_per_image 秒/张），夹在 [min,max]。
    含封面+CTA，所以至少 3 张。"""
    if total_duration <= 0:
        return 5
    n = round(total_duration / max(1.0, seconds_per_image))
    return max(min_images, min(max_images, int(n)))

# 面部质量增强提示词：减少AI绘图常见的面部畸形问题
# 豆包 Seedream 不支持独立 negative_prompt 参数，故把负面词转成正文禁止句追加
FACE_QUALITY_SUFFIX = "面部清晰对称，五官端正，高质量人像摄影，自然表情"
FACE_NEGATIVE_SUFFIX = "。严格避免：面部畸形、五官扭曲、表情怪异、眼睛不对称、嘴巴变形"

# 人物故事赛道配图：封面=人物主体，内容=情节场景，结尾=悬念/引导
# 健康书单赛道配图：封面=书籍，内容=健康场景，结尾=CTA
# 各赛道用 tracks 的画风三层包裹，主体描述按赛道切换。


def pick_main_book(books: list[dict]) -> dict | None:
    """选主书：confidence 最高，并列取 extracted_from 更靠前者（PRD 9.3）。"""
    if not books:
        return None
    return sorted(
        books,
        key=lambda b: (-float(b.get("confidence", 0)), len(b.get("extracted_from", ""))),
    )[0]


def assign_content_descriptions(segments: list[dict], n_content: int) -> list[str]:
    """内容插图与 segment 均匀匹配（PRD 9.4）。取段首≤20字作画面描述。"""
    descs = []
    m = max(1, len(segments))
    for k in range(n_content):
        idx = min(m - 1, round(k * m / max(1, n_content)))
        text = segments[idx]["text"] if segments else "场景"
        descs.append(_sanitize_imagery(text[:20]))
    return descs


# 画面安全替换：故事原文常含血腥/病态/死亡描述，照搬进 prompt 会被绘图平台
# 内容审核拒图。在源头把违禁意象换成安全的替代意象（参考竞品做法），
# 同样的剧情、画面更易过审且更有美感。比"被拒了再重试"更省成本。
_SAFE_IMAGERY = {
    "血水": "暗红的颜料", "流血": "褪色的旧手帕", "鲜血": "微红的霞光",
    "血珠": "晶莹的露珠", "血迹": "斑驳的暗红痕", "渗血": "洇开的红墨",
    "血": "暗红的绸缎",
    "皮肤溃烂": "光线投射的沧桑侧脸", "溃烂": "斑驳的旧墙",
    "肿胀": "沉重的旧衣", "脓": "潮湿的苔痕",
    "惨死": "缓缓熄灭的灯", "死亡": "缓缓飘落的叶", "死去": "暗下去的窗",
    "尸体": "覆着白布的卧榻", "战死": "残阳下静默的古战场",
    "上吊": "空荡的房梁", "自杀": "熄灭的灯", "吐血": "打翻的红墨",
    "病重": "倚靠床头的身影", "重病": "床边的药碗",
    "尸": "覆着白布的卧榻", "腐烂": "斑驳的旧物",
    "刀": "搁置的旧物", "枪": "尘封的器物", "杀": "凝重的对峙",
    "暗杀": "暗夜中凝重的对峙", "刺杀": "紧张静默的夜色", "行刑": "肃穆的庭院",
    "暴力": "紧绷的气氛", "殴打": "对峙的身影", "酷刑": "压抑的牢窗微光",
    # 政治/领土类（豆包对地图、国界、标记极敏感，几乎必拒）：整个换成安全物件画面。
    "军阀": "旧时代的肃穆官员", "枪下": "肃杀的旧时光",
    "中国地图": "摊开的旧书与笔记", "地图上": "书桌上", "地图": "摊开的旧书卷",
    "版图": "泛黄的书页", "国界": "蜿蜒的山川轮廓", "领土": "辽阔的山河远景",
    "政区图": "古朴的山水画卷", "红笔标记": "密密的批注笔迹",
    "暗杀网络": "错综的旧档案线索", "各地点": "一处处旧址",
    "红色细线": "蜿蜒的墨线", "红线": "蜿蜒的墨线",
    "红笔": "毛笔", "标注": "批注", "圈画": "书写",
}


def is_monochrome_style(style: dict | None) -> bool:
    """判断是否纯黑白风格（需本地强制转灰度）。
    黑白纪实等风格走 API 时，模型常不听'黑白'文字（尤其图生图：彩色人物参考图的视觉信号
    压倒文字），仍出彩色。唯一可靠解法是出图后本地强制转灰度。
    只认明确的纯黑白（prefix 含'黑白'）；水墨带淡彩是其特色，不强制。"""
    if not style:
        return False
    pre = (style.get("prefix") or "")
    return "黑白" in pre


def _sanitize_imagery(text: str) -> str:
    """把画面描述里的违禁意象替换成安全替代意象。长词优先，避免子串误伤。"""
    for bad in sorted(_SAFE_IMAGERY, key=len, reverse=True):
        if bad in text:
            text = text.replace(bad, _SAFE_IMAGERY[bad])
    return text


def _wrap(style: dict, subject: str) -> str:
    """用画风三层包裹主体描述，生成最终绘图 prompt。
    豆包 Seedream 不支持独立 negative_prompt 参数，故把画风的 negative 词转成正文禁止句追加，
    否则「黑白纪实」等强色彩约束会失效（实测选黑白仍出彩色）。"""
    neg = (style.get("negative") or "").strip().strip("，,")
    base = f"{style['prefix']}{subject}{style['suffix']}"
    if neg:
        base += f"。严格避免以下元素：{neg}"
    return base


def _gen_with_fallback(provider, api_key, prompt, sub_type, out_path,
                       suggested_duration, model, aspect_ratio="9:16", ref_uri=None,
                       base_url=None, proxy=None, grayscale=False, no_crop=False):
    """生成单张：可重试错误(超时/限流/审核误判)退避重试，耗尽则降级占位图。
    单张失败不中断整批，保证链路产出（PRD 11.2 必选模块尽量不整体失败）。
    no_crop=True 时 _normalize 只等比缩放不裁切（保留完整画面）。"""
    from app.modules.retry import with_retry
    try:
        result, _ = with_retry(
            lambda: generate_image(provider, api_key, prompt, sub_type, out_path,
                                   suggested_duration, model=model, aspect_ratio=aspect_ratio,
                                   ref_uri=ref_uri, base_url=base_url, proxy=proxy,
                                   grayscale=grayscale, no_crop=no_crop, timeout=600.0),
            _img_retry_for(model), disconnect_retry=IMG_DISCONNECT_RETRY)
        return result
    except ImageError as e:
        # 任何 ImageError 都降级占位、绝不向上 raise。
        # 历史教训(task: "扣60几次0张图"): 逐张模式下某张碰到不可重试硬错误(如余额不足 403、
        # model 错), 旧代码 raise → render_images 的 fut.result() 一抛掀翻整批 → E 产物根本没存 →
        # 画廊全是"未生成"空槽, 而前面已扣的钱全打水漂。
        # 改为单张失败只占位+原因, 其余照常返回, E 必落地。是否重生由用户在画廊决定。
        # (Key无效这类全局错也只占位, 不再让一张错带崩整批; 用户看占位原因即知要去改 Key。)
        return placeholder_result(out_path, sub_type, suggested_duration, reason=str(e)[:120])


def run_images(provider, api_key, book_info, segments, out_dir: Path,
               image_count=5, track="character_story", image_style=None, model=None,
               concurrency=5, aspect_ratio="9:16", character_desc=None, ref_uri=None,
               proxy=None):
    """生成 1 封面 + N 内容 + 1 结尾。按赛道主体 + 画风三层包裹。
    角色一致性：有参考图(ref_uri)时人物镜头走图生图(同一个人、不同场景)，
    否则靠提示词里的人物特征文字(character_desc)锚定；空镜/物件画面紧贴文案、不带人物。
    单张被内容审核反复拒绝时降级为占位图，不中断整条链路。
    多张并发生成（线程池，I/O 密集），保持封面→内容→结尾的顺序返回。"""
    tasks, _ = build_image_prompts(book_info, segments, out_dir, image_count=image_count,
                                   track=track, image_style=image_style,
                                   character_desc=character_desc, ref_uri=ref_uri)
    return render_images(provider, api_key, tasks, model=model, concurrency=concurrency,
                         aspect_ratio=aspect_ratio, proxy=proxy,
                         grayscale=is_monochrome_style(tracks.get_style(image_style, track) if image_style else None))


_SHOT_VARIATIONS = [
    "远景全身，人物置于环境中，交代场景氛围",
    "中景半身，刻画人物动作与姿态",
    "近景特写，聚焦人物神情，浅景深",
    "过肩视角，从人物背后望向远方场景",
    "侧面构图，强调轮廓与光影",
    "低角度仰拍，突出人物气势",
    "俯拍全身，展现环境与人物关系",
    "人物与环境细节的过渡空镜，弱化正脸",
]


def build_image_prompts(book_info, segments, out_dir: Path, image_count=5,
                        track="character_story", image_style=None, scenes=None,
                        character_desc=None, ref_uri=None,
                        ref_map=None, character_keys=None):
    """Step 3「提示词生成」：组装绘图任务列表（提示词+落盘路径），不调用绘图 API。
    返回 [(prompt, sub_type, out_path, suggested_duration, ref_uri), ...]，封面→内容→结尾顺序。
    scenes 非空时用画面脚本（视觉化分镜描述）作内容图主体，否则回退到 segment 截字。
    scenes 每项可为 dict {"desc","has_character","character_key"} 或纯字符串（兼容老格式，视为有人物）。
    character_desc 非空时，仅在「需要人物出场」的画面（封面 + has_character 的内容图）
    注入主角特征文字做一致性锚定；空镜/物件镜头不带人物特征，让画面紧贴文案。
    ref_uri（参考图 data URI）非空时，人物镜头走图生图（保持主角一致：同一个人、不同场景），
    每个人物镜头的 prompt 用「保持面部不变、改变场景姿势」句式包裹，并带上 ref_uri。
    ref_map（{key: data_uri}）非空时，按 scene.character_key 匹配对应参考图，实现多角色各自一致；
    未匹配到 key 时回退到 ref_uri（全局参考图）或纯文生图。
    character_keys 是可选的角色 key 列表，用于在 prompt 未标注时匹配文案中的角色名。"""
    image_count = max(MIN_IMAGES, min(MAX_IMAGES, image_count))
    out_dir.mkdir(parents=True, exist_ok=True)

    style = tracks.get_style(image_style, track)
    tk = tracks.get_track(track)

    # 兜底主体（SB 失败、无分镜时用赛道主体生成通用画面）。
    if track == "character_story":
        title = (book_info or {}).get("title") or tk["image_subject"]
        fallback_subject = f"{title}，人物半身画像，温和的神情"
    elif track == "health_book":
        title = (book_info or {}).get("title") or "健康养生"
        topic = (book_info or {}).get("category") or "健康养生"
        fallback_subject = f"《{title}》书籍封面，{topic}主题，清晰易读"
    else:
        subject = tk["image_subject"]
        title = (book_info or {}).get("title") or subject
        fallback_subject = f"{subject}，{title}，吸睛开场画面"

    # 把 scenes 归一成 [{"cap","desc_prompt","has_character","character_key"}]，兼容老格式。
    norm_scenes = []
    for x in (scenes or []):
        if isinstance(x, dict):
            dp = _sanitize_imagery(str(x.get("desc_prompt") or x.get("desc") or ""))
            if not dp.strip():
                # desc_prompt 被审核词替换后变空：用 cap 截字兜底，保住分镜数量
                # （不能直接丢弃——会导致 P.scenes 比 SB 分镜少一项，TTS 段数也少一段，
                # 最后一段配音永远缺失）。
                dp = _sanitize_imagery(str(x.get("cap", ""))[:40])
            if not dp.strip():
                dp = fallback_subject  # cap 也空：用赛道兜底主体
            norm_scenes.append({
                "cap": str(x.get("cap", "")),
                "desc_prompt": dp,
                "has_character": bool(x.get("has_character", True)),
                "character_key": str(x.get("character_key", "") or "").strip() or None,
            })
        elif str(x).strip():
            norm_scenes.append({"cap": "", "desc_prompt": _sanitize_imagery(str(x)),
                                "has_character": True, "character_key": None})

    # 图片与分镜【严格一一对应】：N 个分镜 = N 张图，下标对齐（image[i] = scene[i]），
    # 不再额外加抽象封面/CTA。首张标 sub_type="cover"（仅供剪映草稿封面缩略图用），
    # 但它就是第一个分镜（画首段文案的内容、配首段配音）；其余标 "content"。
    # 无分镜（SB 失败）时回退：按 image_count 用 segment 截字 + 镜头轮换兜底。
    if norm_scenes:
        scene_items = norm_scenes
        use_storyboard = True
    else:
        n = max(MIN_IMAGES, image_count)
        descs = assign_content_descriptions(segments, n)
        scene_items = [{"cap": "", "desc_prompt": d or fallback_subject,
                        "has_character": True} for d in descs]
        use_storyboard = False

    # 图生图人物镜头的提示词包裹：强调"保持参考人物面部/外貌不变，改变场景姿势"
    # （实测此句式能做到同一个人、不同场景，而非复刻参考图构图）。
    # 关键：必须极度强调面部完全不变，否则模型会自由发挥生成不同人物
    def _persona(subject):
        return (f"【重要】生成与参考图中完全相同的人物：保持此人的面部特征、五官比例、发型、肤色、性别、年龄完全不变，"
                f"与参考图是同一个人。仅改变以下部分——场景背景、服装、姿势、表情：{subject}。"
                f"绝对不要改变此人的面部外观和身份特征。"
                f"面部质量要求：{FACE_QUALITY_SUFFIX}{FACE_NEGATIVE_SUFFIX}")

    tasks = []
    for i, item in enumerate(scene_items):
        desc = item["desc_prompt"]
        has_char = item["has_character"]
        if use_storyboard:
            # 新版 desc_prompt 已是完整整句（SB 已按 has_character 把人物特征写进句子），直接用。
            # 仅对空镜补一句“无人物”兜底防止绘图模型自作主张加人。
            content_subject = desc if has_char else f"{desc}，画面中无人物，聚焦场景与物件本身"
        else:
            # 老格式/回退：截字描述 + 镜头轮换 + 一致性锚定。
            shot = _SHOT_VARIATIONS[i % len(_SHOT_VARIATIONS)]
            if has_char:
                if character_desc:
                    anchor = f"，主角形象：{character_desc}【严禁改变主角性别，必须是「{character_desc[:20]}」中描述的性别】"
                else:
                    anchor = "，同一主角保持外貌一致"
            else:
                anchor = "，画面中无人物，聚焦场景与物件本身"
            content_subject = f"{desc}，{shot}{anchor}"
        # 仅"需要人物出场"的镜头走图生图（带参考图）；空镜/物件镜头纯文生图。
        item_ref = None
        if has_char:
            # 多角色模式：按 character_key 匹配对应参考图
            if ref_map and item.get("character_key"):
                item_ref = ref_map.get(item["character_key"])
            # 未匹配到 key 时：尝试从文案中检测角色名
            if not item_ref and ref_map and character_keys:
                cap_lower = (item.get("cap") or "").lower()
                desc_lower = desc.lower()
                for ck in character_keys:
                    if ck.lower() in cap_lower or ck.lower() in desc_lower:
                        item_ref = ref_map.get(ck)
                        break
            # 兜底：全局单参考图
            if not item_ref:
                item_ref = ref_uri
        # 有人出场时：有参考图走图生图（_persona 已含面部质量提示），无参考图也追加面部质量提示
        if item_ref:
            content_text = _persona(content_subject)
        elif has_char:
            # 纯文生图但有人脸：追加面部质量提示词减少畸形
            content_text = f"{content_subject}，{FACE_QUALITY_SUFFIX}{FACE_NEGATIVE_SUFFIX}"
        else:
            content_text = content_subject
        # 首张标 cover（剪映草稿封面用），其余 content。落盘文件名按下标，保证稳定。
        sub_type = "cover" if i == 0 else "content"
        fname = "cover.png" if i == 0 else f"content_{i}.png"
        tasks.append((_wrap(style, content_text), sub_type,
                      out_dir / fname, 10, item_ref))
    # 返回 tasks 和【实际使用的分镜项】scene_items：调用方用它落库 P.scenes，保证
    # 图与配音/字幕严格同源同长（cap 一一对应），不靠隐式过滤撞巧合。
    return tasks, scene_items


def render_images(provider, api_key, tasks, model=None, concurrency=5,
                  aspect_ratio="9:16", base_url=None, proxy=None, grayscale=False,
                  no_crop=False):
    """Step 4「批量生图」：按 build_image_prompts 产出的任务列表并发生成。
    单张失败降级占位图，不中断整批。保持任务列表顺序返回。
    base_url 非空时所有请求打中转站（OpenAI 兼容），用于降单价。
    proxy 非空时所有请求走代理（中转站如 tu-zi.com 需代理才能访问）。
    grayscale=True 时所有图本地强制转黑白（黑白风格模型常不听文字、图生图照彩色参考出彩色）。
    no_crop=True 时 _normalize 只等比缩放不裁切（保留完整画面，用于 center_h 版式）。"""
    from concurrent.futures import ThreadPoolExecutor
    results: list = [None] * len(tasks)
    workers = max(1, min(concurrency, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_gen_with_fallback, provider, api_key, p, st, op, sd, model,
                          aspect_ratio, rf, base_url, proxy, grayscale, no_crop): idx
                for idx, (p, st, op, sd, rf) in enumerate(tasks)}
        for fut in futs:
            idx = futs[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                # 双保险:_gen_with_fallback 已兜住 ImageError, 这里再兜住任何意外异常
                # (写盘失败/编码异常等), 保证单张出事不掀翻整批、E 必落地。
                p, st, op, sd, rf = tasks[idx]
                results[idx] = placeholder_result(op, st, sd, reason=f"生成异常: {str(e)[:100]}")
    return results


# 豆包组图单次上限
GROUP_MAX = 9

# 九宫格省成本模式：固定风格圣经（一条视频所有图共享同一套色彩/光线/镜头/气质，
# 像同一支短片的连续分镜）。作为九宫格 prompt 的统一前缀。
GRID_STYLE_BIBLE = (
    "固定美术方向：明亮电影感真实摄影，安静、克制、有知识短视频质感。"
    "固定色彩：暖白、浅木色、柔和灰蓝、低饱和绿色，少量温暖阳光点缀。"
    "固定光线：窗边自然光、清晨或傍晚柔光，阴影干净，整体曝光偏明亮。"
    "固定镜头：35mm/50mm 人文镜头语言，主体明确，背景简洁。"
    "人物气质：普通成年人，安静、理性、克制，优先背影、侧影、手部动作和生活场景。"
    "所有画面必须共享同一套色彩、光线、镜头、人物气质、材质和时代感，"
    "像同一支短片的连续分镜，而不是不相关的图。"
    "避免医院、病房、手术、器官、伤口、监护仪等画面，病痛用生活化隐喻表达。"
)


def build_grid_prompt(cell_briefs: list, style_bible: str | None = None) -> str:
    """把 N 格画面 brief 拼成九宫格图生图 prompt（配合 3×3 白线模板参考图）。
    模型会在模板的灰色格子里按编号填画面。brief 应是裸主体描述（不含风格三层包裹）。
    style_bible 非空时用它作统一风格说明（来自用户所选画风）；为空回退内置 GRID_STYLE_BIBLE。"""
    lines = "\n".join(f"{i+1}. {b}" for i, b in enumerate(cell_briefs))
    bible = (style_bible or "").strip() or GRID_STYLE_BIBLE
    # 九宫格全局面部质量约束：作为风格圣经的补充，确保所有人物画面面部质量。
    # 每格分辨率本来就低，人脸越大越容易崩——所以除了要求五官对称，还明确让人物
    # 别怼脸大特写、格子间脸部特征不要互相混合（多张脸挤在一张画布里容易互相"借"五官）。
    face_quality = (
        "【人脸质量·最高优先】每格若出现人物：五官必须完整、比例正常——两眼对称等高、"
        "鼻梁居中、嘴唇轮廓单一不重叠，脸部干净清晰，不融合、不重影、不扭曲、不出现多余"
        "或缺失的眼睛/嘴巴/鼻子。人物脸部占格子画面的比例不宜过大，避免顶格大特写正脸，"
        "优先半身或中近景，让五官有自然的呈现空间。每个格子里的人物都是独立个体，"
        "不要把相邻格子的人脸特征相互混合或叠加。宁可五官画得简单干净，也不要画得精致但错位。"
    )
    return (
        "参考图是一张 3×3 九宫格模板，由白色分隔线划分成 9 个完全等大的灰色格子。\n"
        "请严格保持参考图的网格结构和白色格线位置不变，只在每个灰色格子里填入对应编号的"
        "照片画面。\n"
        "【关键要求】每格的背景/环境要完全填满整个格子，不能有留白或空白区域，"
        "但画面主体不必贴近特写——主体完整入镜、四周留出安全边距，不要越过白色分隔线。\n\n"
        + bible + "\n\n"
        + face_quality + "\n\n"
        "九格画面（从左到右、从上到下）：\n" + lines + "\n\n"
        "不要在图片里放任何文字。不要输出解释。"
    )


def _style_bible(style: dict | None) -> str:
    """把用户所选画风的 prefix/suffix/negative 拼成九宫格统一风格说明。
    九宫格 image 槽被 3×3 模板占用，无法逐格套三层 wrap，只能把画风浓缩成一句统一前缀，
    让全部 9 格共享同一画风（也保证风格统一）。style 为空/无前后缀则返回空（回退内置圣经）。
    强色彩约束（黑白/水墨等，negative 含"彩色"）会把"纯单色、禁止彩色"提到最前重复强调——
    九宫格是图生图，画风词权重被网格模板冲淡，不强调会失效（实测选黑白仍出彩色）。"""
    if not style:
        return ""
    pre = (style.get("prefix") or "").strip().strip("，,")
    suf = (style.get("suffix") or "").strip().strip("，,")
    neg = (style.get("negative") or "").strip().strip("，,")
    parts = [p for p in (pre, suf) if p]
    if not parts:
        return ""
    head = ""
    # 黑白/单色类强约束：negative 里点名"彩色"时，开头硬性强调，压住图生图的彩色倾向
    if "彩色" in neg or "黑白" in pre or "水墨" in pre or "单色" in pre:
        head = "【强制】整幅图必须是纯单色（黑白/水墨）画面，绝对不能出现任何彩色。"
    bible = ("固定美术方向（所有 9 格必须共享同一画风、像同一支短片的连续分镜）："
             + "；".join(parts) + "。")
    if neg:
        bible += f"严格避免：{neg}。"
    return head + bible


def _strip_wrap(style: dict, full_prompt: str) -> str:
    """从 wrap 过的完整 prompt 剥出裸主体（去掉风格三层 prefix/suffix + negative 禁止句）。
    九宫格统一用 style_bible 管风格，不需要逐格再套三层。"""
    pre, suf = style.get("prefix", ""), style.get("suffix", "")
    neg = (style.get("negative") or "").strip().strip("，,")
    s = full_prompt
    # 先剥 _wrap 追加的 negative 禁止句（在最末），再剥 prefix/suffix
    if neg:
        tail = f"。严格避免以下元素：{neg}"
        if s.endswith(tail):
            s = s[:len(s) - len(tail)]
    if pre and s.startswith(pre):
        s = s[len(pre):]
    if suf and s.endswith(suf):
        s = s[:len(s) - len(suf)]
    return s.strip() or full_prompt


def render_images_grouped(provider, api_key, tasks, model=None,
                          aspect_ratio="9:16", grid_mode=False, style=None,
                          base_url=None, proxy=None, rewrite_fn=None, no_crop=False):
    """组图模式批量生图：把 tasks 按「是否带参考图」分组、每组≤9 张。
    每个 task 五元组 (prompt, sub_type, out_path, duration, ref_uri)；prompt 已是套风格的完整提示词。
    grid_mode=True：每组用「3×3 模板图生图」一次出 1 张大图本地切 9 张（按 1 张计费，省 89%）。
      失败即判败 → 整组占位图，等用户在画廊手动重新组图；绝不回退 auto 组图或逐张
      （一次九宫格失败若降级逐张会放大成最多 9 次请求，成本失控，已彻底屏蔽）。
    grid_mode=False：每组合并 prompt 走 auto 组图（按张计费），失败同样占位、不逐张降级。
    返回顺序与 tasks 一致。"""
    from app.services.image import (generate_images_batch, generate_grid_image, ImageError)
    _gray = is_monochrome_style(style)  # 黑白风格：出图后本地强制转灰度
    results = [None] * len(tasks)
    from collections import OrderedDict
    by_ref = OrderedDict()  # ref_uri -> [task index...]，保留首次出现顺序
    for i, (_p, _st, _op, _sd, rf) in enumerate(tasks):
        by_ref.setdefault(rf, []).append(i)
    groups = []  # [(indices, ref_uri)]
    for rf, idxs in by_ref.items():
        for s in range(0, len(idxs), GROUP_MAX):
            groups.append((idxs[s:s + GROUP_MAX], rf))

    def _fill_placeholder(indices, reason):
        """整组判败：每格写占位图，等用户在画廊手动重新组图。绝不逐张补图（控成本）。
        九宫格模式下占位也打 grid 标记——这一组失败=【一次】网格请求失败，计费才能按
        ceil(格数/9) 正确折算（否则失败格被当逐张图各计 1 单位，gpt-image 下会高估成本）。"""
        for i in indices:
            _p, st, op, sd, _rf = tasks[i]
            r = placeholder_result(op, st, sd, reason=reason)
            if grid_mode:
                r.meta["grid"] = True
            results[i] = r

    def _batch_only(indices, ref):
        """auto 组图：一次请求出多张。失败即整组判败占位，绝不回退逐张。"""
        sub_prompts = [tasks[i][0] for i in indices]
        merged = (f"请生成 {len(indices)} 张风格统一的竖版画面，像同一支短片的连续分镜，"
                  f"分别为：" + " ".join(f"{k+1}.{sp}" for k, sp in enumerate(sub_prompts)))
        sub_types = [tasks[i][1] for i in indices]
        out_paths = [tasks[i][2] for i in indices]
        durations = [tasks[i][3] for i in indices]
        try:
            batch = generate_images_batch(provider, api_key, merged, sub_types,
                                          out_paths, durations, model=model,
                                          aspect_ratio=aspect_ratio, ref_uri=ref,
                                          base_url=base_url, proxy=proxy, grayscale=_gray,
                                          no_crop=no_crop, timeout=1200.0)
            for k, i in enumerate(indices):
                results[i] = batch[k]
        except ImageError as e:
            _fill_placeholder(indices, f"组图失败: {str(e)[:80]}")
        except Exception as e:
            # 同九宫格: 非 ImageError(json解析/解码/写盘等)也占位, 不冒泡掀翻整批。
            _fill_placeholder(indices, f"组图处理异常: {str(e)[:80]}")

    def _do_group(indices, ref):
        """处理一个批次：单张也走九宫格/组图一次请求；失败即判败占位，绝不逐张降级。
        九宫格被内容审核拒时：若提供 rewrite_fn，自动 LLM 改写整批裸 brief 后重发（最多2次，
        递进激进），仍败才占位。9 格拼一个 prompt、1 格踩雷整组被拒，故整批改写是九宫格的解药。"""
        if grid_mode:
            # 九宫格：剥出裸 brief（统一用风格圣经管风格，不逐格套三层）。
            briefs = [_strip_wrap(style or {}, tasks[i][0]) for i in indices]
            sub_types = [tasks[i][1] for i in indices]
            out_paths = [tasks[i][2] for i in indices]
            durations = [tasks[i][3] for i in indices]

            def _is_audit(msg):
                m = (msg or "").lower()
                return ("sensitive" in m or "审核" in m or "拒绝" in m or "451" in m
                        or "moderation" in m or "safety" in m or "violate" in m)

            last_err = ""
            for attempt in range(0, 3):  # 第0次原始, 1/2次递进改写
                if attempt > 0:
                    if not rewrite_fn:
                        break  # 无改写能力, 直接占位
                    try:
                        briefs = [rewrite_fn(b, attempt) for b in briefs]
                        briefs = [_sanitize_imagery(b) for b in briefs]
                    except Exception:
                        break
                cell_prompt = build_grid_prompt(briefs, style_bible=_style_bible(style))
                try:
                    # 瞬时故障(超时/断连/限流/5xx)退避重试，和逐张 _gen_with_fallback 一致：
                    # 中转站瞬时断连("Server disconnected without sending a response")多半上游没出图
                    # 也没扣费，重试一次常能成；retryable=False 的错(审核/被拒/返空)在 with_retry 内不重试。
                    # timeout=600s 覆盖 gpt-image-2 最慢情况（实测 1755s/29min，但 600s 已能覆盖绝大多数；
                    # 超 600s 的极端慢请求也认了，总比 180s 超时断连导致"扣费+丢图"强）。
                    from app.modules.retry import with_retry
                    grid, _ = with_retry(
                        lambda: generate_grid_image(provider, api_key, cell_prompt, sub_types,
                                                    out_paths, durations, model=model,
                                                    aspect_ratio=aspect_ratio, base_url=base_url,
                                                    proxy=proxy, grayscale=_gray, timeout=600.0,
                                                    ref_uri=ref),
                        _img_retry_for(model), disconnect_retry=IMG_DISCONNECT_RETRY)
                    for k, i in enumerate(indices):
                        results[i] = grid[k]
                    return  # 成功
                except ImageError as e:
                    last_err = str(e)
                    if not _is_audit(last_err):
                        break  # 非审核类(超时/Key等), 改写无用, 直接占位
                except Exception as e:
                    # 非 ImageError(如 _split_grid 解码坏图/截断数据抛 PIL.UnidentifiedImageError、
                    # 下载/写盘异常): 改写救不了, 直接占位。绝不让它冒泡到 ex.map 掀翻整批
                    # (九宫格版"单点掀桌": 一组解码失败→render_images_grouped崩→E没存→全槽未生成,
                    # 而那次九宫格请求已扣过钱)。
                    last_err = f"九宫格处理异常: {str(e)[:80]}"
                    break
            _fill_placeholder(indices, f"九宫格失败: {last_err[:80]}")
            return
        _batch_only(indices, ref)

    # 多批并发跑（每批一次组图请求），把串行等待压成并行，明显提速。
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(len(groups), 4))

    def _safe_group(g):
        # 双保险: _do_group 内部已兜异常, 这里再兜住 try 块之外的意外(如组装 prompt 抛错),
        # 保证某一组出事不掀翻其余组、对应槽位占位兜底, E 必落地。
        try:
            _do_group(g[0], g[1])
        except Exception as e:
            _fill_placeholder(g[0], f"九宫格批次异常: {str(e)[:80]}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_safe_group, groups))
    return results
