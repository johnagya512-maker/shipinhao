"""模块 E：配图编排。含 D→E 选主书（PRD 9.3）与提示词变量组装（9.4）。
按赛道画风（tracks.IMAGE_STYLES 三层配置）生成配图。
"""
from pathlib import Path
from app.services.image import generate_image, placeholder_result, ImageError, ImageResult
from app.modules import tracks

IMG_RETRY = 2

# 配图节奏：人物故事/中老年受众，约 6 秒一张（常规叙事 5-7s 的中值）。
# 张数随时长动态变，封顶避免成本失控；下限保证封面+内容+CTA 基本结构。
SECONDS_PER_IMAGE = 6
MIN_IMAGES = 3
MAX_IMAGES = 24


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
                       suggested_duration, model, aspect_ratio="9:16", reference_image=None):
    """生成单张：可重试错误(超时/限流/审核误判)退避重试，耗尽则降级占位图。
    单张失败不中断整批，保证链路产出（PRD 11.2 必选模块尽量不整体失败）。"""
    from app.modules.retry import with_retry
    try:
        result, _ = with_retry(
            lambda: generate_image(provider, api_key, prompt, sub_type, out_path,
                                   suggested_duration, model=model, aspect_ratio=aspect_ratio,
                                   reference_image=reference_image),
            IMG_RETRY)
        return result
    except ImageError as e:
        # 不可重试错误（Key 无效等）直接抛；可重试但耗尽 → 降级占位图
        if not getattr(e, "retryable", False):
            raise
        return placeholder_result(out_path, sub_type, suggested_duration, reason=str(e)[:120])


def run_images(provider, api_key, book_info, segments, out_dir: Path,
               image_count=5, track="character_story", image_style=None, model=None,
               concurrency=5, aspect_ratio="9:16", reference_image=None):
    """生成 1 封面 + N 内容 + 1 结尾。按赛道主体 + 画风三层包裹。
    reference_image 非空时作为角色一致性参考喂给绘图模型（best-effort，模型不支持则退回纯文生图）。
    单张被内容审核反复拒绝时降级为占位图，不中断整条链路。
    多张并发生成（线程池，I/O 密集），保持封面→内容→结尾的顺序返回。"""
    tasks = build_image_prompts(book_info, segments, out_dir, image_count=image_count,
                                track=track, image_style=image_style)
    return render_images(provider, api_key, tasks, model=model, concurrency=concurrency,
                         aspect_ratio=aspect_ratio, reference_image=reference_image)


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
                        track="character_story", image_style=None):
    """Step 3「提示词生成」：组装绘图任务列表（提示词+落盘路径），不调用绘图 API。
    返回 [(prompt, sub_type, out_path, suggested_duration), ...]，保持封面→内容→结尾顺序。"""
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

    # 组装任务列表（保持顺序：封面 → 内容 → 结尾）
    # 优先用画面脚本（视觉化分镜）；没有则回退到 segment 截字 + 镜头轮换。
    clean_scenes = [_sanitize_imagery(str(x)) for x in (scenes or []) if str(x).strip()]
    if clean_scenes:
        descs = [clean_scenes[min(i, len(clean_scenes) - 1)] for i in range(n_content)]
        use_storyboard = True
    else:
        descs = assign_content_descriptions(segments, n_content)
        use_storyboard = False
    tasks = [(_wrap(style, cover_subject), "cover", out_dir / "cover.png", 4)]
    for i, desc in enumerate(descs):
        if use_storyboard:
            # 画面脚本已含景别/角度/动作，直接用，仅补一句一致性约束。
            content_subject = f"{desc}，同一主角保持外貌一致"
        else:
            shot = _SHOT_VARIATIONS[i % len(_SHOT_VARIATIONS)]
            content_subject = f"{desc}，{shot}，同一主角保持外貌一致"
        tasks.append((_wrap(style, content_subject), "content",
                      out_dir / f"content_{i}.png", 10))
    tasks.append((_wrap(style, cta_subject), "cta", out_dir / "cta.png", 6))
    return tasks


def render_images(provider, api_key, tasks, model=None, concurrency=5,
                  aspect_ratio="9:16", reference_image=None):
    """Step 4「批量生图」：按 build_image_prompts 产出的任务列表并发生成。
    单张失败降级占位图，不中断整批。保持任务列表顺序返回。"""
    from concurrent.futures import ThreadPoolExecutor
    results: list = [None] * len(tasks)
    workers = max(1, min(concurrency, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_gen_with_fallback, provider, api_key, p, st, op, sd, model,
                          aspect_ratio, reference_image): idx
                for idx, (p, st, op, sd) in enumerate(tasks)}
        for fut in futs:
            results[futs[fut]] = fut.result()  # 单张失败会抛不可重试错误，向上传递
    return results
