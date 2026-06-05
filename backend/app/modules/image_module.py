"""模块 E：配图编排。含 D→E 选主书（PRD 9.3）与提示词变量组装（9.4）。
按赛道画风（tracks.IMAGE_STYLES 三层配置）生成配图。
"""
from pathlib import Path
from app.services.image import generate_image, ImageResult
from app.modules import tracks

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
        descs.append(text[:20])
    return descs


def _wrap(style: dict, subject: str) -> str:
    """用画风三层包裹主体描述，生成最终绘图 prompt。"""
    return f"{style['prefix']}{subject}{style['suffix']}"


def run_images(provider, api_key, book_info, segments, out_dir: Path,
               image_count=5, track="character_story", image_style=None):
    """生成 1 封面 + N 内容 + 1 结尾。按赛道主体 + 画风三层包裹。"""
    image_count = max(3, min(6, image_count))
    n_content = image_count - 2
    out_dir.mkdir(parents=True, exist_ok=True)

    style = tracks.get_style(image_style, track)
    tk = tracks.get_track(track)
    results: list[ImageResult] = []

    if track == "character_story":
        title = (book_info or {}).get("title") or tk["image_subject"]
        cover_subject = f"{title}，主角人物肖像，富有戏剧张力的特写"
        cta_subject = f"{title}，意味深长的结局画面，留白引人遐想"
    else:
        title = (book_info or {}).get("title") or "健康养生"
        topic = (book_info or {}).get("category") or "健康养生"
        cover_subject = f"《{title}》书籍封面，{topic}主题，清晰易读"
        cta_subject = f"《{title}》书籍特写，号召了解的画面"

    # 封面
    results.append(generate_image(provider, api_key, _wrap(style, cover_subject), "cover",
                                  out_dir / "cover.png", suggested_duration=4))

    # 内容插图：用分段文本作画面描述
    descs = assign_content_descriptions(segments, n_content)
    for i, desc in enumerate(descs):
        subject = f"{desc}的场景画面"
        results.append(generate_image(provider, api_key, _wrap(style, subject), "content",
                                      out_dir / f"content_{i}.png", suggested_duration=10))

    # 结尾
    results.append(generate_image(provider, api_key, _wrap(style, cta_subject), "cta",
                                  out_dir / "cta.png", suggested_duration=6))

    return results
