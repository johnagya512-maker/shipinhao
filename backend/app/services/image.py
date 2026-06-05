"""绘图客户端。封装配图生成，支持 doubao/kling/tongyi。

注：各供应商绘图 API 形态差异较大且多为异步任务制，此处提供统一同步封装与
mock 模式（无 Key 或 provider=mock 时返回占位图），便于 G 模块与端到端先跑通。
真实供应商接入在 _call_<provider> 中补全。
"""
import httpx
from dataclasses import dataclass, field
from PIL import Image
from pathlib import Path


@dataclass
class ImageResult:
    path: str
    sub_type: str  # cover/content/cta
    suggested_duration: int
    meta: dict = field(default_factory=dict)


class ImageError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# 竖版 9:16
WIDTH, HEIGHT = 1080, 1920


def _placeholder(out_path: Path, label: str, color: tuple):
    """生成纯色占位图（mock 模式 / 无 Key 时）。"""
    img = Image.new("RGB", (WIDTH, HEIGHT), color)
    img.save(out_path, "PNG")


def generate_image(provider: str, api_key: str, prompt: str, sub_type: str,
                   out_path: Path, suggested_duration: int,
                   timeout: float = 60.0) -> ImageResult:
    """生成单张配图。无 Key 或 provider=mock 时走占位图。"""
    use_mock = (not api_key) or provider == "mock"
    if use_mock:
        colors = {"cover": (255, 228, 196), "content": (220, 237, 220), "cta": (255, 218, 224)}
        _placeholder(out_path, sub_type, colors.get(sub_type, (230, 230, 230)))
        return ImageResult(str(out_path), sub_type, suggested_duration, {"mock": True})

    # 真实供应商：此处仅给出统一调用骨架，具体协议按 provider 补全。
    try:
        url = _endpoint(provider)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"prompt": prompt, "size": f"{WIDTH}x{HEIGHT}", "n": 1}
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException as e:
        raise ImageError(f"配图超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"配图请求错误: {e}", retryable=True)

    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError("绘图限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"绘图服务端错误 {resp.status_code}", retryable=True)
    if resp.status_code >= 400:
        raise ImageError(f"配图被拒 {resp.status_code}", retryable=False)

    img_url = resp.json()["data"][0]["url"]
    img_bytes = httpx.get(img_url, timeout=timeout).content
    out_path.write_bytes(img_bytes)
    # 统一缩放到竖版
    _normalize(out_path)
    return ImageResult(str(out_path), sub_type, suggested_duration, {})


def _endpoint(provider: str) -> str:
    eps = {
        "doubao": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "kling": "https://api.klingai.com/v1/images/generations",
        "tongyi": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
    }
    if provider not in eps:
        raise ImageError(f"未知绘图供应商: {provider}", retryable=False)
    return eps[provider]


def _normalize(path: Path):
    """缩放并居中裁剪到 1080x1920。"""
    img = Image.open(path).convert("RGB")
    src_ratio = img.width / img.height
    dst_ratio = WIDTH / HEIGHT
    if src_ratio > dst_ratio:
        new_h = HEIGHT
        new_w = int(HEIGHT * src_ratio)
    else:
        new_w = WIDTH
        new_h = int(WIDTH / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - WIDTH) // 2
    top = (new_h - HEIGHT) // 2
    img = img.crop((left, top, left + WIDTH, top + HEIGHT))
    img.save(path, "PNG")
