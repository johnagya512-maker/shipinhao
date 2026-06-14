"""模块 E：配图编排。含 D→E 选主书（PRD 9.3）与提示词变量组装（9.4）。
按赛道画风（tracks.IMAGE_STYLES 三层配置）生成配图。
"""
from pathlib import Path
from app.services.image import generate_image, placeholder_result, ImageError, ImageResult
from app.modules import tracks

IMG_RETRY = 2

# 配图节奏：默认约 8 秒一张（中老年友好）；实际秒/张由赛道决定（见 tracks.seconds_per_image）。
# 张数随时长动态变，封顶避免成本失控；下限保证封面+内容+CTA 基本结构。
SECONDS_PER_IMAGE = 8
MIN_IMAGES = 3
MAX_IMAGES = 48


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
    "流血": "褪色的旧手帕", "鲜血": "微红的霞光", "血": "暗红的绸缎",
    "皮肤溃烂": "光线投射的沧桑侧脸", "溃烂": "斑驳的旧墙",
    "肿胀": "沉重的旧衣", "脓": "潮湿的苔痕",
    "死亡": "缓缓飘落的叶", "死去": "暗下去的窗", "尸体": "覆着白布的卧榻",
    "上吊": "空荡的房梁", "自杀": "熄灭的灯", "吐血": "打翻的红墨",
    "病重": "倚靠床头的身影", "重病": "床边的药碗",
    "尸": "覆着白布的卧榻", "腐烂": "斑驳的旧物",
    "刀": "搁置的旧物", "枪": "尘封的器物", "杀": "凝重的对峙",
    "暴力": "紧绷的气氛", "殴打": "对峙的身影",
    # 政治/领土类（豆包对地图、国界、标记极敏感，几乎必拒）：整个换成安全物件画面。
    "中国地图": "摊开的旧书与笔记", "地图上": "书桌上", "地图": "摊开的旧书卷",
    "版图": "泛黄的书页", "国界": "蜿蜒的山川轮廓", "领土": "辽阔的山河远景",
    "政区图": "古朴的山水画卷", "红笔标记": "密密的批注笔迹",
    "红笔": "毛笔", "标注": "批注", "圈画": "书写",
}


def _sanitize_imagery(text: str) -> str:
    """把画面描述里的违禁意象替换成安全替代意象。长词优先，避免子串误伤。"""
    for bad in sorted(_SAFE_IMAGERY, key=len, reverse=True):
        if bad in text:
            text = text.replace(bad, _SAFE_IMAGERY[bad])
    return text


def _wrap(style: dict, subject: str) -> str:
    """用画风三层包裹主体描述，生成最终绘图 prompt。"""
    return f"{style['prefix']}{subject}{style['suffix']}"


def _gen_with_fallback(provider, api_key, prompt, sub_type, out_path,
                       suggested_duration, model, aspect_ratio="9:16", ref_uri=None):
    """生成单张：可重试错误(超时/限流/审核误判)退避重试，耗尽则降级占位图。
    单张失败不中断整批，保证链路产出（PRD 11.2 必选模块尽量不整体失败）。"""
    from app.modules.retry import with_retry
    try:
        result, _ = with_retry(
            lambda: generate_image(provider, api_key, prompt, sub_type, out_path,
                                   suggested_duration, model=model, aspect_ratio=aspect_ratio,
                                   ref_uri=ref_uri),
            IMG_RETRY)
        return result
    except ImageError as e:
        # 不可重试错误（Key 无效等）直接抛；可重试但耗尽 → 降级占位图
        if not getattr(e, "retryable", False):
            raise
        return placeholder_result(out_path, sub_type, suggested_duration, reason=str(e)[:120])


def run_images(provider, api_key, book_info, segments, out_dir: Path,
               image_count=5, track="character_story", image_style=None, model=None,
               concurrency=5, aspect_ratio="9:16", character_desc=None, ref_uri=None):
    """生成 1 封面 + N 内容 + 1 结尾。按赛道主体 + 画风三层包裹。
    角色一致性：有参考图(ref_uri)时人物镜头走图生图(同一个人、不同场景)，
    否则靠提示词里的人物特征文字(character_desc)锚定；空镜/物件画面紧贴文案、不带人物。
    单张被内容审核反复拒绝时降级为占位图，不中断整条链路。
    多张并发生成（线程池，I/O 密集），保持封面→内容→结尾的顺序返回。"""
    tasks = build_image_prompts(book_info, segments, out_dir, image_count=image_count,
                                track=track, image_style=image_style,
                                character_desc=character_desc, ref_uri=ref_uri)
    return render_images(provider, api_key, tasks, model=model, concurrency=concurrency,
                         aspect_ratio=aspect_ratio)


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
                        character_desc=None, ref_uri=None):
    """Step 3「提示词生成」：组装绘图任务列表（提示词+落盘路径），不调用绘图 API。
    返回 [(prompt, sub_type, out_path, suggested_duration, ref_uri), ...]，封面→内容→结尾顺序。
    scenes 非空时用画面脚本（视觉化分镜描述）作内容图主体，否则回退到 segment 截字。
    scenes 每项可为 dict {"desc","has_character"} 或纯字符串（兼容老格式，视为有人物）。
    character_desc 非空时，仅在「需要人物出场」的画面（封面 + has_character 的内容图）
    注入主角特征文字做一致性锚定；空镜/物件镜头不带人物特征，让画面紧贴文案。
    ref_uri（参考图 data URI）非空时，人物镜头走图生图（保持主角一致：同一个人、不同场景），
    每个人物镜头的 prompt 用「保持面部不变、改变场景姿势」句式包裹，并带上 ref_uri。"""
    image_count = max(MIN_IMAGES, min(MAX_IMAGES, image_count))
    n_content = image_count - 2
    out_dir.mkdir(parents=True, exist_ok=True)

    style = tracks.get_style(image_style, track)
    tk = tracks.get_track(track)

    if track == "character_story":
        title = (book_info or {}).get("title") or tk["image_subject"]
        cover_subject = f"{title}，人物半身画像，温和的神情"
        cta_subject = f"{title}，意境悠远的收尾画面，留白引人遐想"
    elif track == "health_book":
        title = (book_info or {}).get("title") or "健康养生"
        topic = (book_info or {}).get("category") or "健康养生"
        cover_subject = f"《{title}》书籍封面，{topic}主题，清晰易读"
        cta_subject = f"《{title}》书籍特写，号召了解的画面"
    else:
        # 其它赛道：用赛道主体（image_subject）生成通用封面/结尾，不套书籍封面框架。
        subject = tk["image_subject"]
        title = (book_info or {}).get("title") or subject
        cover_subject = f"{subject}，{title}，吸睛开场画面"
        cta_subject = f"{subject}，引发互动的收尾画面，留白引人遐想"

    # 把 scenes 归一成 [{"cap","desc_prompt","has_character"}]，兼容老格式（desc 字段 / 纯字符串）。
    norm_scenes = []
    for x in (scenes or []):
        if isinstance(x, dict):
            dp = _sanitize_imagery(str(x.get("desc_prompt") or x.get("desc") or ""))
            if dp.strip():
                norm_scenes.append({"cap": str(x.get("cap", "")),
                                    "desc_prompt": dp,
                                    "has_character": bool(x.get("has_character", True))})
        elif str(x).strip():
            norm_scenes.append({"cap": "", "desc_prompt": _sanitize_imagery(str(x)),
                                "has_character": True})

    # 组装任务列表（保持顺序：封面 → 内容 → 结尾）
    # 优先用画面脚本（视觉化分镜）；没有则回退到 segment 截字 + 镜头轮换。
    if norm_scenes:
        scene_items = [norm_scenes[min(i, len(norm_scenes) - 1)] for i in range(n_content)]
        use_storyboard = True
    else:
        descs = assign_content_descriptions(segments, n_content)
        scene_items = [{"cap": "", "desc_prompt": d, "has_character": True} for d in descs]
        use_storyboard = False

    # 图生图人物镜头的提示词包裹：强调"保持参考人物面部/外貌不变，改变场景姿势"
    # （实测此句式能做到同一个人、不同场景，而非复刻参考图构图）。
    def _persona(subject):
        return (f"参考图中的同一个人物，保持其面部特征、五官、气质不变，"
                f"但改为以下全新画面（不同的姿势、表情、构图）：{subject}")

    # 封面：人物故事赛道是视频第一帧、需抓眼球，强制带主角特征（若有）。
    cover_prompt_subject = cover_subject
    if character_desc and track == "character_story":
        cover_prompt_subject = f"{cover_subject}，主角形象：{character_desc}"
    cover_ref = ref_uri if track == "character_story" else None
    cover_text = _persona(cover_prompt_subject) if cover_ref else cover_prompt_subject
    tasks = [(_wrap(style, cover_text), "cover", out_dir / "cover.png", 4, cover_ref)]

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
                anchor = f"，主角形象：{character_desc}" if character_desc else "，同一主角保持外貌一致"
            else:
                anchor = "，画面中无人物，聚焦场景与物件本身"
            content_subject = f"{desc}，{shot}{anchor}"
        # 仅"需要人物出场"的镜头走图生图（带参考图）；空镜/物件镜头纯文生图。
        item_ref = ref_uri if has_char else None
        content_text = _persona(content_subject) if item_ref else content_subject
        tasks.append((_wrap(style, content_text), "content",
                      out_dir / f"content_{i}.png", 10, item_ref))
    tasks.append((_wrap(style, cta_subject), "cta", out_dir / "cta.png", 6, None))
    return tasks


def render_images(provider, api_key, tasks, model=None, concurrency=5,
                  aspect_ratio="9:16"):
    """Step 4「批量生图」：按 build_image_prompts 产出的任务列表并发生成。
    单张失败降级占位图，不中断整批。保持任务列表顺序返回。"""
    from concurrent.futures import ThreadPoolExecutor
    results: list = [None] * len(tasks)
    workers = max(1, min(concurrency, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_gen_with_fallback, provider, api_key, p, st, op, sd, model,
                          aspect_ratio, rf): idx
                for idx, (p, st, op, sd, rf) in enumerate(tasks)}
        for fut in futs:
            results[futs[fut]] = fut.result()  # 单张失败会抛不可重试错误，向上传递
    return results


# 豆包组图单次上限
GROUP_MAX = 9


def render_images_grouped(provider, api_key, tasks, model=None,
                          aspect_ratio="9:16"):
    """组图模式批量生图：把 tasks 按「是否带参考图」分组、每组≤9 张，一次请求出多张。
    省约 89% 成本（N 张 → ceil(N/9) 次请求），同批同次生成→风格统一，带参考图→人物一致。
    每个 task 五元组 (prompt, sub_type, out_path, duration, ref_uri)；
    prompt 已是套风格的完整提示词，组图时合并为「请生成 N 张，分别为：1.xx 2.xx ...」。
    任一批组图失败 → 该批回退逐张 render，不中断出片。返回顺序与 tasks 一致。"""
    from app.services.image import generate_images_batch, ImageError
    results = [None] * len(tasks)
    # 分组：相邻、同 ref_uri（None 或同一张）的归一组，每组≤GROUP_MAX
    groups = []  # [(indices, ref_uri)]
    cur, cur_ref = [], "__init__"
    for i, (_p, _st, _op, _sd, rf) in enumerate(tasks):
        if rf != cur_ref or len(cur) >= GROUP_MAX:
            if cur:
                groups.append((cur, cur_ref))
            cur, cur_ref = [i], rf
        else:
            cur.append(i)
    if cur:
        groups.append((cur, cur_ref))

    def _do_group(indices, ref):
        """处理一个批次：单张走单图；多张合并提示词走组图，整批失败回退逐张。"""
        if len(indices) == 1:
            i = indices[0]
            p, st, op, sd, rf = tasks[i]
            results[i] = _gen_with_fallback(provider, api_key, p, st, op, sd, model,
                                            aspect_ratio, rf)
            return
        sub_prompts = [tasks[i][0] for i in indices]
        merged = (f"请生成 {len(indices)} 张风格统一的竖版画面，像同一支短片的连续分镜，"
                  f"分别为：" + " ".join(f"{k+1}.{sp}" for k, sp in enumerate(sub_prompts)))
        sub_types = [tasks[i][1] for i in indices]
        out_paths = [tasks[i][2] for i in indices]
        durations = [tasks[i][3] for i in indices]
        try:
            batch = generate_images_batch(provider, api_key, merged, sub_types,
                                          out_paths, durations, model=model,
                                          aspect_ratio=aspect_ratio, ref_uri=ref)
            for k, i in enumerate(indices):
                results[i] = batch[k]
        except ImageError:
            for i in indices:
                p, st, op, sd, rf = tasks[i]
                results[i] = _gen_with_fallback(provider, api_key, p, st, op, sd,
                                                model, aspect_ratio, rf)

    # 多批并发跑（每批一次组图请求），把串行等待压成并行，明显提速。
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(len(groups), 4))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda g: _do_group(g[0], g[1]), groups))
    return results
