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


# 竖版 9:16（默认）
WIDTH, HEIGHT = 1080, 1920

# 出图比例 → 归一化目标尺寸（长边约 1920，保证清晰度）。
ASPECT_DIMS = {
    "9:16": (1080, 1920),
    "3:4": (1440, 1920),
    "1:1": (1440, 1440),
    "16:9": (1920, 1080),
}
# 出图比例 → 供应商请求尺寸（面积需大于供应商最小像素要求，留余量；下游再归一化裁切）。
ASPECT_GEN_SIZE = {
    "9:16": "1536x2730",
    "3:4": "1664x2218",
    "1:1": "2048x2048",
    "16:9": "2730x1536",
}


def _dims_for(aspect_ratio: str | None) -> tuple[int, int]:
    return ASPECT_DIMS.get(aspect_ratio or "9:16", (WIDTH, HEIGHT))


def _placeholder(out_path: Path, label: str, color: tuple, size: tuple[int, int] = (WIDTH, HEIGHT)):
    """生成纯色占位图（mock 模式 / 无 Key 时）。"""
    img = Image.new("RGB", size, color)
    img.save(out_path, "PNG")


_FALLBACK_COLORS = {"cover": (255, 228, 196), "content": (220, 237, 220),
                    "cta": (255, 218, 224)}


def placeholder_result(out_path: Path, sub_type: str, suggested_duration: int,
                       reason: str = "fallback") -> "ImageResult":
    """降级占位图：真实配图反复被拒时兜底，保证链路不中断。"""
    _placeholder(out_path, sub_type, _FALLBACK_COLORS.get(sub_type, (230, 230, 230)))
    return ImageResult(str(out_path), sub_type, suggested_duration,
                       {"fallback": True, "reason": reason})


def is_placeholder_image(path: str) -> bool:
    """判断一张图是否是失败占位图（纯色块）。
    历史任务的 E 产物没存 fallback 标记，靠看图本身兜底识别：
    占位图由 Image.new 画的单一纯色，每个通道 max==min；真实图必有色彩变化。
    读不到文件视为占位（让其可被重试）。"""
    try:
        p = Path(path)
        if not p.is_file():
            return True
        img = Image.open(p).convert("RGB")
        ex = img.getextrema()  # ((rmin,rmax),(gmin,gmax),(bmin,bmax))
        return all(lo == hi for lo, hi in ex)
    except Exception:
        return False


def _encode_reference(path: str) -> str | None:
    """把参考图读成豆包要求的 data URI（data:image/...;base64,xxx）。
    文件不存在/读取失败返回 None，调用方据此退回纯文生图。"""
    import base64
    try:
        p = Path(path)
        if not p.is_file():
            return None
        ext = p.suffix.lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f"data:image/{mime};base64,{b64}"
    except Exception:
        return None


def generate_image(provider: str, api_key: str, prompt: str, sub_type: str,
                   out_path: Path, suggested_duration: int,
                   model: str | None = None, timeout: float = 60.0,
                   aspect_ratio: str = "9:16", ref_uri: str | None = None) -> ImageResult:
    """生成单张配图。无 Key 或 provider=mock 时走占位图。
    ref_uri 非空时走图生图（把参考图作为 image 传入），用于人物镜头保持主角一致性
    （同一个人、不同场景——提示词须明确"保持面部不变、改变场景姿势"，见 build_image_prompts）；
    为空时纯文生图。"""
    w, h = _dims_for(aspect_ratio)
    use_mock = (not api_key) or provider == "mock"
    if use_mock:
        colors = {"cover": (255, 228, 196), "content": (220, 237, 220), "cta": (255, 218, 224)}
        _placeholder(out_path, sub_type, colors.get(sub_type, (230, 230, 230)), size=(w, h))
        return ImageResult(str(out_path), sub_type, suggested_duration, {"mock": True})

    # 真实供应商：此处仅给出统一调用骨架，具体协议按 provider 补全。
    try:
        url = _endpoint(provider)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        # 豆包（火山方舟）Seedream 文生图：必须带 model（模型 ID / 接入点 ID）。
        # size 用明确像素，竖版 9:16 且需大于最小像素要求（边界值 3686400 实测不通过，留余量）；
        # 下游 _normalize 再裁到 1080x1920。
        payload = {"prompt": prompt, "size": ASPECT_GEN_SIZE.get(aspect_ratio or "9:16", "1536x2730"),
                   "sequential_image_generation": "disabled",
                   "response_format": "url", "stream": False, "watermark": False}
        if model:
            payload["model"] = model
        if ref_uri:
            # 图生图：传参考图保持人物一致性（Seedream 4.x 支持 image 入参，实测有效）
            payload["image"] = ref_uri
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
        raise ImageError(f"绘图服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        # 输出内容审核误判（如豆包 OutputImageSensitiveContentDetected）有随机性，
        # 标记可重试，让上层 re-roll；耗尽后由编排降级为占位图，不毁整条链路。
        if "SensitiveContent" in body or "sensitive" in body.lower():
            raise ImageError(f"配图被内容审核拒绝(可重试): {body}", retryable=True)
        # 其他 4xx（model 不对 / 参数不符等）透出真实报错，不可重试。
        raise ImageError(f"配图被拒 {resp.status_code}: {body}", retryable=False)

    img_url = resp.json()["data"][0]["url"]
    img_bytes = httpx.get(img_url, timeout=timeout).content
    out_path.write_bytes(img_bytes)
    # 统一缩放到目标比例尺寸
    _normalize(out_path, size=(w, h))
    return ImageResult(str(out_path), sub_type, suggested_duration, {})


# 豆包组图单次上限（实测 max_images=15 实际只返回 9）
BATCH_MAX = 9


def generate_images_batch(provider: str, api_key: str, prompt: str,
                          sub_types: list, out_paths: list, durations: list,
                          model: str | None = None, timeout: float = 180.0,
                          aspect_ratio: str = "9:16", ref_uri: str | None = None) -> list:
    """组图：一次请求生成多张（豆包 sequential_image_generation）。返回 ImageResult 列表。
    一次最多 BATCH_MAX 张；同批同次生成 → 风格统一；传 ref_uri → 多张保持同一个人。
    返回数量可能少于请求数，调用方需按 out_paths 长度对齐/补齐。"""
    n = len(out_paths)
    w, h = _dims_for(aspect_ratio)
    use_mock = (not api_key) or provider == "mock"
    if use_mock:
        colors = {"cover": (255, 228, 196), "content": (220, 237, 220), "cta": (255, 218, 224)}
        res = []
        for st, op, du in zip(sub_types, out_paths, durations):
            _placeholder(op, st, colors.get(st, (230, 230, 230)), size=(w, h))
            res.append(ImageResult(str(op), st, du, {"mock": True}))
        return res

    url = _endpoint(provider)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"prompt": prompt,
               "size": ASPECT_GEN_SIZE.get(aspect_ratio or "9:16", "1536x2730"),
               "sequential_image_generation": "auto",
               "sequential_image_generation_options": {"max_images": min(n, BATCH_MAX)},
               "response_format": "url", "stream": False, "watermark": False}
    if model:
        payload["model"] = model
    if ref_uri:
        payload["image"] = ref_uri
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException as e:
        raise ImageError(f"组图超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"组图请求错误: {e}", retryable=True)
    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError("绘图限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"绘图服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        if "SensitiveContent" in body or "sensitive" in body.lower():
            raise ImageError(f"组图被内容审核拒绝(可重试): {body}", retryable=True)
        raise ImageError(f"组图被拒 {resp.status_code}: {body}", retryable=False)

    data = resp.json().get("data") or []
    res = []
    for i, op in enumerate(out_paths):
        if i < len(data) and data[i].get("url"):
            try:
                img_bytes = httpx.get(data[i]["url"], timeout=timeout).content
                op.write_bytes(img_bytes)
                _normalize(op, size=(w, h))
                res.append(ImageResult(str(op), sub_types[i], durations[i], {}))
                continue
            except Exception:
                pass
        # 该位置没拿到图 → 占位兜底（不中断）
        _placeholder(op, sub_types[i], (230, 230, 230), size=(w, h))
        res.append(ImageResult(str(op), sub_types[i], durations[i],
                               {"fallback": True, "reason": "组图返回数量不足"}))
    return res


def _endpoint(provider: str) -> str:
    eps = {
        "doubao": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "kling": "https://api.klingai.com/v1/images/generations",
        "tongyi": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
    }
    if provider not in eps:
        raise ImageError(f"未知绘图供应商: {provider}", retryable=False)
    return eps[provider]


def _normalize(path: Path, size: tuple[int, int] = (WIDTH, HEIGHT)):
    """缩放并居中裁剪到目标尺寸。"""
    target_w, target_h = size
    img = Image.open(path).convert("RGB")
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        new_h = target_h
        new_w = int(target_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(target_w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    img.save(path, "PNG")
