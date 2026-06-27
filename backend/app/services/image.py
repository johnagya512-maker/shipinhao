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
    def __init__(self, message: str, retryable: bool = True, audit: bool = False,
                 disconnect: bool = False):
        super().__init__(message)
        self.retryable = retryable
        # audit=True：内容审核拦截（输入文案/输出图片敏感）。原样重试无用, 但 LLM 改写可救,
        # 故上层降级占位图(带审核 reason)交给改写补救, 而非当作终极错误直接抛。
        self.audit = audit
        # disconnect=True：连接层断连(Server disconnected/请求错误)。服务器没回任何响应,
        # 中转站多半未受理、未扣费(见 cost.image_billable_units)。故即使 gpt(失败也计费)也能
        # 放心多重试——重试不烧钱、还常能成。with_retry 据此给断连更高的重试预算。
        self.disconnect = disconnect



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


# ═══════════════════════════════════════════════════════════════════════════
# API 格式判断: OpenAI 兼容 vs 豆包官方
# ═══════════════════════════════════════════════════════════════════════════
#
# 两种 API 格式:
#   1. OpenAI 兼容 - payload: {model, prompt, size, n}
#      → 所有中转站(兔子/siliconflow) + OpenAI官方 + gpt模型
#
#   2. 豆包官方 - payload 额外包含: sequential_image_generation/watermark等
#      → 仅豆包官方API (ark.cn-beijing.volces.com)
#
# **核心规则: 中转站永远用OpenAI格式,不管模型名是gpt还是doubao**
# ═══════════════════════════════════════════════════════════════════════════

# OpenAI 兼容中转站列表 (新增中转站只需在此添加,一处修改全局生效)
_OPENAI_HOSTS = ["tu-zi", "openai.com", "siliconflow", "api.red", "gptsapi"]
_DOUBAO_HOSTS = ["volces.com", "ark.cn-beijing"]  # 豆包官方
_APIB_HOSTS = ["apib.ai", "apimart.ai"]  # APIMart 异步协议（与 OpenAI 不兼容）


def _is_apib(base_url: str | None) -> bool:
    """判断是否使用 APIMart (apib.ai) 异步协议。

    APIMart 虽然兼容部分 OpenAI 参数，但使用异步任务模式，协议不同：
    - 请求参数: size="16:9" (宽高比), resolution="2k" (档位)
    - 响应格式: 返回 task_id，需轮询查询结果
    """
    if not base_url:
        return False
    url = base_url.lower()
    return any(h in url for h in _APIB_HOSTS)


def _use_openai_format(base_url: str | None, model: str | None = None) -> bool:
    """判断是否使用OpenAI兼容格式。

    优先级: ① URL含APIMart→False(异步协议) ② URL含OpenAI中转站→True ③ 模型名含gpt→True
            ④ 模型名含doubao→False ⑤ URL含豆包官方→False ⑥ 默认False

    修复: OpenAI中转站(兔子等)优先于模型名，避免"doubao模型+OpenAI中转站"走豆包图生图而失败。
    """
    # URL优先判断
    if base_url:
        url = base_url.lower()
        # APIMart 用异步协议，不是 OpenAI 格式
        if any(h in url for h in _APIB_HOSTS):
            return False
        # 豆包官方用专有格式
        if any(h in url for h in _DOUBAO_HOSTS):
            return False
        # OpenAI中转站强制用OpenAI格式（包括doubao模型也走纯文字九宫格）
        if any(h in url for h in _OPENAI_HOSTS) or "/v1/images" in url:
            return True

    # 模型名兜底判断
    if model:
        m = model.lower()
        if "gpt" in m or "dall" in m:
            return True   # OpenAI格式：纯文字九宫格
        if "doubao" in m or "seedream" in m:
            return False  # 豆包格式：带3x3模板的图生图（仅豆包官方支持）

    return False  # 默认豆包格式


# 兼容旧代码的别名 (后续可逐步替换成 _use_openai_format)
def _is_gpt(model: str | None, base_url: str | None = None) -> bool:
    """[已废弃] 请用 _use_openai_format,语义更清晰"""
    return _use_openai_format(base_url, model)


# OpenAI 兼容协议的尺寸档位（中转站只认固定档位,任意值会被拒）。
# 兔子API的豆包和gpt模型都只支持这些标准尺寸,下游 _normalize 再裁到目标比例。
_OPENAI_SIZE = {"9:16": "1024x1536", "3:4": "1024x1536",
                "16:9": "1536x1024", "1:1": "1024x1024"}


def _gpt_size(aspect_ratio: str | None) -> str:
    """OpenAI 兼容协议的尺寸档位(中转站和gpt模型)"""
    return _OPENAI_SIZE.get(aspect_ratio or "9:16", "1024x1536")


def _apib_aspect(aspect_ratio: str | None) -> str:
    """APIMart 的宽高比格式 (直接传宽高比字符串, 不是像素)"""
    # APIMart 接受的格式: "1:1", "3:4", "9:16", "16:9" 等
    return aspect_ratio or "9:16"


def _apib_request(url: str, api_key: str, prompt: str, aspect_ratio: str, n: int,
                  model: str, timeout: float, proxy: str | None,
                  ref_uri: str | None = None, label: str = "配图") -> list[bytes]:
    """调用 APIMart (apib.ai) 异步图像生成 API。

    异步流程:
    1. POST /v1/images/generations → 获取 task_id 和 status="submitted"
    2. GET /v1/tasks/{task_id} 轮询状态 → 等待 status="completed"
    3. 从响应中获取 image_url → 下载图片
    """
    import time

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 1. 提交生成任务
    payload = {
        "model": model,
        "prompt": prompt,
        "size": _apib_aspect(aspect_ratio),
        "resolution": "2k",  # 2K 分辨率档位 (1k/2k/4k)
        "n": n,
    }
    if ref_uri:
        payload["image_urls"] = [ref_uri]  # 参考图

    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy

    try:
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
    except httpx.TimeoutException as e:
        raise ImageError(f"{label}提交超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"{label}请求错误: {e}", retryable=True, disconnect=True)

    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError(f"{label}限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"{label}服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        low = body.lower()
        if ("moderation" in low or "safety" in low or "sensitive" in low):
            raise ImageError(f"{label}被内容审核拒绝(可改写): {body}", retryable=False, audit=True)
        raise ImageError(f"{label}被拒 {resp.status_code}: {body}", retryable=False)

    # 解析响应获取 task_id
    try:
        result = resp.json()
        code = result.get("code", 0)
        if code != 200:
            msg = result.get("message", "Unknown error")
            raise ImageError(f"{label}失败: {msg}", retryable=False)

        data = result.get("data", [])
        if not data:
            raise ImageError(f"{label}未返回任务ID(中转站返空, 不自动重试)", retryable=False)

        task_id = data[0].get("task_id")
        if not task_id:
            raise ImageError(f"{label}未返回任务ID", retryable=False)
    except (KeyError, IndexError, ValueError) as e:
        raise ImageError(f"{label}响应解析失败: {e}", retryable=False)

    # 2. 轮询任务状态
    # URL: https://api.apib.ai/v1/images/generations → https://api.apib.ai
    base_url = url.rsplit("/", 2)[0]
    query_url = f"{base_url}/v1/tasks/{task_id}"

    max_attempts = 60  # 最多轮询 60 次
    poll_interval = 2  # 每 2 秒查询一次 (总共最多 120 秒)

    for attempt in range(max_attempts):
        time.sleep(poll_interval)

        try:
            status_resp = httpx.get(query_url, headers=headers, timeout=10.0)
        except httpx.RequestError:
            # 查询失败不立即放弃，继续重试
            continue

        if status_resp.status_code != 200:
            continue

        try:
            status_result = status_resp.json()
            if status_result.get("code") != 200:
                continue

            task_data = status_result.get("data", {})
            task_status = task_data.get("status")

            if task_status == "completed":
                # 任务完成，获取图片 URL
                image_url = task_data.get("image_url")
                if not image_url:
                    # 尝试从 image_urls 数组获取
                    image_urls = task_data.get("image_urls", [])
                    if image_urls:
                        image_url = image_urls[0]

                if not image_url:
                    raise ImageError(f"{label}完成但未返回图片URL", retryable=False)

                # 3. 下载图片
                img_resp = httpx.get(image_url, **_kw)
                return [img_resp.content]

            elif task_status == "failed":
                error_msg = task_data.get("error", "Unknown error")
                raise ImageError(f"{label}生成失败: {error_msg}", retryable=False)

            # 其他状态（processing, pending, submitted）继续等待
        except (KeyError, ValueError):
            continue

    # 超时
    raise ImageError(f"{label}生成超时（{max_attempts * poll_interval}秒）", retryable=True)


def _gpt_request(url: str, api_key: str, prompt: str, size: str, n: int,
                 model: str, timeout: float, proxy: str | None,
                 label: str = "配图") -> list[bytes]:
    """调 gpt-image 文生图，返回 n 张图的原始字节列表（base64 解码后）。
    错误语义沿用 ImageError（含审核 audit 标记），与豆包分支保持一致。"""
    import base64
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": max(1, n),
        "response_format": "b64_json",  # gpt-image-2 必需参数：明确返回 base64 格式
        "quality": "high",  # 高质量渲染
        "output_format": "png"  # PNG 格式输出
    }
    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy
    try:
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
    except httpx.TimeoutException as e:
        raise ImageError(f"{label}超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"{label}请求错误: {e}", retryable=True, disconnect=True)
    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError(f"{label}限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"{label}服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        low = body.lower()
        # gpt 内容审核：moderation_blocked / safety system 等。可改写救回，标 audit。
        if ("moderation" in low or "safety" in low or "content_policy" in low
                or "sensitive" in low):
            raise ImageError(f"{label}被内容审核拒绝(可改写): {body}", retryable=False, audit=True)
        raise ImageError(f"{label}被拒 {resp.status_code}: {body}", retryable=False)
    data = resp.json().get("data") or []
    out = []
    for d in data:
        b64 = d.get("b64_json")
        if b64:
            out.append(base64.b64decode(b64))
        elif d.get("url"):
            # 个别中转站 gpt 线路仍回 url，兜底下载
            out.append(httpx.get(d["url"], **_kw).content)
    if not out:
        # gpt 中转站(兔子等)按【请求次数】计费, 触发审核/上游波动时常回 200 但 data 为空。
        # 这种返空若判 retryable 会被上层 IMG_RETRY 反复重打 → 一张图扣 N 次钱却拿不到图
        # (task: "扣60几次0张图"的元凶)。故标 retryable=False: 不自动重试、直接占位, 一张最多扣1次,
        # 是否再花钱由用户在画廊手动「重新生成」决定。
        raise ImageError(f"{label}未返回图片(中转站返空, 不自动重试)", retryable=False)
    return out


def _gpt_edit_request(url: str, api_key: str, prompt: str, size: str, n: int,
                      model: str, ref_uri: str, timeout: float, proxy: str | None,
                      label: str = "图生图") -> list[bytes]:
    """调 OpenAI /v1/images/edits 图生图，返回 n 张图的原始字节列表。
    使用 multipart/form-data 上传参考图 + 提示词。ref_uri 支持 data:image/... 或文件路径。
    错误语义沿用 ImageError（含审核 audit 标记），与其他分支保持一致。"""
    import base64
    import io

    # 解析参考图：支持 data URI 或文件路径
    if ref_uri.startswith("data:image/"):
        # data:image/png;base64,xxx
        header, b64_data = ref_uri.split(",", 1)
        image_bytes = base64.b64decode(b64_data)
    else:
        # 文件路径
        image_bytes = Path(ref_uri).read_bytes()

    # 构造 multipart/form-data
    files = {
        "image": ("image.png", io.BytesIO(image_bytes), "image/png"),
        "prompt": (None, prompt),
        "n": (None, str(n)),
        "size": (None, size),
        "response_format": (None, "b64_json"),  # gpt-image-2 必需参数
        "quality": (None, "high"),  # 高质量渲染
        "output_format": (None, "png"),  # PNG 格式输出
    }
    if model:
        files["model"] = (None, model)

    headers = {"Authorization": f"Bearer {api_key}"}
    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy

    # 替换端点：/v1/images/generations → /v1/images/edits
    edit_url = url.replace("/generations", "/edits")

    try:
        resp = httpx.post(edit_url, files=files, headers=headers, **_kw)
    except httpx.TimeoutException as e:
        raise ImageError(f"{label}超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"{label}请求错误: {e}", retryable=True, disconnect=True)

    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError(f"{label}限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"{label}服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        low = body.lower()
        # gpt 内容审核
        if ("moderation" in low or "safety" in low or "content_policy" in low
                or "sensitive" in low):
            raise ImageError(f"{label}被内容审核拒绝(可改写): {body}", retryable=False, audit=True)
        raise ImageError(f"{label}被拒 {resp.status_code}: {body}", retryable=False)

    data = resp.json().get("data") or []
    out = []
    for d in data:
        b64 = d.get("b64_json")
        if b64:
            out.append(base64.b64decode(b64))
        elif d.get("url"):
            out.append(httpx.get(d["url"], **_kw).content)

    if not out:
        raise ImageError(f"{label}未返回图片(中转站返空, 不自动重试)", retryable=False)
    return out


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
                   aspect_ratio: str = "9:16", ref_uri: str | None = None,
                   base_url: str | None = None, proxy: str | None = None,
                   grayscale: bool = False) -> ImageResult:
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
        url = _endpoint(provider, base_url)

        # APIMart (apib.ai) 异步协议分支：提交任务 → 轮询状态 → 下载图片
        if _is_apib(base_url):
            imgs = _apib_request(url, api_key, prompt, aspect_ratio, 1,
                                model or "gpt-image-2", timeout, proxy,
                                ref_uri=ref_uri, label="配图")
            out_path.write_bytes(imgs[0])
            _normalize(out_path, size=(w, h), grayscale=grayscale)
            return ImageResult(str(out_path), sub_type, suggested_duration, {})

        # OpenAI 兼容协议分支（中转站或 gpt 模型）：简化 payload，与豆包官方API不同。
        # 中转站(tu-zi等)不管模型名是 gpt 还是 doubao，都必须用 OpenAI 标准格式。
        if _is_gpt(model, base_url):
            # 图生图：有参考图时走 /v1/images/edits (multipart)，否则走 /v1/images/generations (JSON)
            if ref_uri:
                imgs = _gpt_edit_request(url, api_key, prompt, _gpt_size(aspect_ratio), 1,
                                        model, ref_uri, timeout, proxy, label="配图(图生图)")
            else:
                imgs = _gpt_request(url, api_key, prompt, _gpt_size(aspect_ratio), 1,
                                    model, timeout, proxy, label="配图")
            out_path.write_bytes(imgs[0])
            _normalize(out_path, size=(w, h), grayscale=grayscale)
            return ImageResult(str(out_path), sub_type, suggested_duration, {})
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
        _kw = {"timeout": timeout}
        if proxy:
            _kw["proxy"] = proxy
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
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
        # 区分两类审核：输入文案敏感（InputText…）重试无用——同一 prompt 必然再被拒，
        # 直接判不可重试、由编排降级占位，省得白烧请求和钱；输出图片误判（OutputImage…）
        # 有随机性，标记可重试让上层 re-roll。
        low = body.lower()
        if "inputtext" in low and "sensitive" in low:
            # 输入文案敏感：原样重发必再被拒(retryable=False不盲目重试), 但标 audit=True
            # 让上层降级占位+LLM改写补救(改个说法就能过)——这才是输入敏感的正确解法。
            raise ImageError(f"配图输入文案被审核拒绝(可改写): {body}", retryable=False, audit=True)
        if "SensitiveContent" in body or "sensitive" in low:
            # 输出图片被审核拒绝：不在后台自动退避重试(retryable=False，不触发 IMG_RETRY 反复烧钱)，
            # 标 audit=True 直接降级占位+原因，等用户在画廊手动「重新生成」。是否再花钱由用户决定。
            raise ImageError(f"配图被内容审核拒绝(可手动重生): {body}", retryable=False, audit=True)
        # 其他 4xx（model 不对 / 参数不符等）透出真实报错，不可重试。
        raise ImageError(f"配图被拒 {resp.status_code}: {body}", retryable=False)

    data = resp.json().get("data") or []
    if not data or not data[0].get("url"):
        # 豆包/中转站返 200 但 data 空(同 gpt 返空场景): 标 retryable=False 不自动重试,
        # 降级占位等手动重生。避免旧代码 resp.json()["data"][0] 直接 IndexError:
        # 后台靠双保险兜住, 但单图重试路径会 500 没占位。显式抛 ImageError 两条路径都优雅占位。
        raise ImageError(f"配图未返回图片(返空, 不自动重试): {str(resp.text)[:120]}", retryable=False)
    img_url = data[0]["url"]
    img_bytes = httpx.get(img_url, **_kw).content
    out_path.write_bytes(img_bytes)
    # 统一缩放到目标比例尺寸（黑白风格强制转灰度）
    _normalize(out_path, size=(w, h), grayscale=grayscale)
    return ImageResult(str(out_path), sub_type, suggested_duration, {})


# 豆包组图单次上限（实测 max_images=15 实际只返回 9）
BATCH_MAX = 9

# ─── 九宫格省成本模式 ───
# 验证结论：纯文字 prompt 让模型画规整 3×3 不可靠（会画成杂志拼贴/5列）；
# 但传一张「3×3 白线网格模板」作参考图（image 参数），模型会尊重格线、在格子里填画面，
# 切出来每格干净可用。一次请求出 1 张大图（按 1 张计费）→ 本地切 9 张 → 省约 89%。
GRID_RC = (3, 3)            # 行 × 列
GRID_CELLS = GRID_RC[0] * GRID_RC[1]
GRID_CANVAS = 2304         # 模板/请求画布边长（正方形，每格 768，切竖版中心裁后约 432 宽）
GRID_LINE = 24             # 白色分隔线半宽（像素）


def _grid_canvas_size(aspect_ratio: str | None) -> tuple[int, int]:
    """九宫格大图画布尺寸：横版(16:9)让大图也出 16:9，3×3 切出来每格天然 16:9、不变形，
    且每格分辨率比硬切正方形再拉伸更高。其余比例(竖版/方形)沿用正方形画布（每格再中心裁）。"""
    if (aspect_ratio or "9:16") == "16:9":
        return (3072, 1728)    # 16:9，每格 1024×576，长边切片后约够清晰
    return (GRID_CANVAS, GRID_CANVAS)


def _make_grid_template(path: Path, cw_total: int = GRID_CANVAS, ch_total: int | None = None,
                        line: int = GRID_LINE):
    """画一张 3×3 白底+浅灰格子的网格模板，作为图生图参考图，给模型「填格」用。
    支持非方形画布(横版传 ch_total)。"""
    from PIL import ImageDraw
    if ch_total is None:
        ch_total = cw_total
    img = Image.new("RGB", (cw_total, ch_total), (255, 255, 255))
    d = ImageDraw.Draw(img)
    rows, cols = GRID_RC
    cw, ch = cw_total // cols, ch_total // rows
    for i in range(GRID_CELLS):
        r, c = i // cols, i % cols
        d.rectangle([c * cw + line, r * ch + line,
                     (c + 1) * cw - line, (r + 1) * ch - line], fill=(235, 235, 235))
    img.save(path, "PNG")


def _split_grid(grid_path: Path, out_paths: list, sub_types: list, durations: list,
                aspect_ratio: str = "9:16", grayscale: bool = False) -> list:
    """把一张规整 3×3 大图切成 ≤9 张单图，写到各 out_path，返回 ImageResult 列表。
    竖版(9:16)：每格中心裁出 9:16 再归一化；其它比例：每格直接归一化。
    out_paths 不足 9 个时只切前 len(out_paths) 格。grayscale=True 时每格强制转黑白。"""
    w, h = _dims_for(aspect_ratio)
    img = Image.open(grid_path).convert("RGB")
    GW, GH = img.size
    rows, cols = GRID_RC
    cw, ch = GW // cols, GH // rows
    # 每格向内收缩裁边：模型画的白色分隔线有宽度、且 9 格不完全等大，硬按 1/3 均分会切到
    # 白线或邻格（表现为成片边缘有白边/画面偏格）。向内收 ~7% 裁掉外圈白线区，保证画面干净。
    inset_x = int(cw * 0.07)
    inset_y = int(ch * 0.07)
    res = []
    for i, (op, st, du) in enumerate(zip(out_paths, sub_types, durations)):
        if i >= GRID_CELLS:
            break
        r, c = i // cols, i % cols
        # 先按格定位，四边各向内收 inset，避开白色分隔线
        x0, y0 = c * cw + inset_x, r * ch + inset_y
        x1, y1 = c * cw + cw - inset_x, r * ch + ch - inset_y
        cell = img.crop((x0, y0, x1, y1))
        # 每格按【目标比例】中心裁切后再缩放，避免近正方形的格子被直接 resize 成 16:9/9:16
        # 而横向或纵向拉伸（人物变胖/变扁、画面变扭）。对所有比例通用，不只竖版。
        ccw, cch = cell.size
        dst_ratio = w / h
        src_ratio = ccw / cch
        if src_ratio > dst_ratio:
            # 格子比目标更宽：裁掉左右
            tw = int(cch * dst_ratio)
            left = max(0, (ccw - tw) // 2)
            cell = cell.crop((left, 0, min(left + tw, ccw), cch))
        elif src_ratio < dst_ratio:
            # 格子比目标更高：裁掉上下
            th = int(ccw / dst_ratio)
            top = max(0, (cch - th) // 2)
            cell = cell.crop((0, top, ccw, min(top + th, cch)))
        cell = cell.resize((w, h), Image.LANCZOS)
        if grayscale:
            cell = cell.convert("L").convert("RGB")
        cell.save(op, "PNG")
        res.append(ImageResult(str(op), st, du, {"grid": True}))
    return res


def generate_grid_image(provider: str, api_key: str, cell_prompt: str,
                        sub_types: list, out_paths: list, durations: list,
                        model: str | None = None, timeout: float = 180.0,
                        aspect_ratio: str = "9:16", base_url: str | None = None,
                        proxy: str | None = None, grayscale: bool = False,
                        no_crop: bool = False, ref_uri: str | None = None) -> list:
    """九宫格省成本：传 3×3 模板作参考图，一次生成 1 张规整大图（按 1 张计费），本地切 ≤9 张。
    cell_prompt 已是拼好的九格 brief（含风格圣经）。失败抛 ImageError，由调用方整组占位。
    mock 模式直接逐格占位。out_paths 长度 ≤9。
    注：no_crop 和 ref_uri 参数在九宫格模式下不适用，仅为兼容调用方保留。"""
    n = len(out_paths)
    w, h = _dims_for(aspect_ratio)
    use_mock = (not api_key) or provider == "mock"
    if use_mock:
        colors = {"cover": (255, 228, 196), "content": (220, 237, 220), "cta": (255, 218, 224)}
        res = []
        for st, op, du in zip(sub_types, out_paths, durations):
            _placeholder(op, st, colors.get(st, (230, 230, 230)), size=(w, h))
            res.append(ImageResult(str(op), st, du, {"mock": True, "grid": True}))
        return res

    # APIMart 异步协议九宫格分支：纯文字提示画 3×3（和 OpenAI 格式类似）
    if _is_apib(base_url):
        url = _endpoint(provider, base_url)
        grid_prompt = (
            "请把整张图片均匀划分成 3 行 3 列、共 9 个完全等大的方格，"
            "用清晰的白色分隔线隔开。在每个格子里按从左到右、从上到下的编号填入"
            "对应画面，每格画面填满该格、主体居中、不要越过白色分隔线。\n\n"
            + cell_prompt + "\n\n不要在图片里放任何文字说明。"
        )
        imgs = _apib_request(url, api_key, grid_prompt, aspect_ratio, 1,
                            model or "gpt-image-2", timeout, proxy, label="九宫格")
        raw = out_paths[0].parent / "_grid_raw.png"
        raw.write_bytes(imgs[0])
        res = _split_grid(raw, out_paths, sub_types, durations, aspect_ratio, grayscale=grayscale)
        try:
            raw.unlink()
        except Exception:
            pass
        return res

    # OpenAI 兼容协议九宫格分支：中转站或 gpt 模型，纯文字让它画 3×3（不传模板图）。
    # 中转站不支持豆包模板图方式，统一用文字提示画九宫格。
    # 注：文字画 3×3 规整度依赖模型能力，切图可能偏格。
    if _is_gpt(model, base_url):
        url = _endpoint(provider, base_url)
        grid_prompt = (
            "请把整张图片均匀划分成 3 行 3 列、共 9 个完全等大的方格，"
            "用清晰的白色分隔线隔开。在每个格子里按从左到右、从上到下的编号填入"
            "对应画面，每格画面填满该格、主体居中、不要越过白色分隔线。\n\n"
            + cell_prompt + "\n\n不要在图片里放任何文字说明。"
        )
        imgs = _gpt_request(url, api_key, grid_prompt, _gpt_size(aspect_ratio), 1,
                            model, timeout, proxy, label="九宫格")
        raw = out_paths[0].parent / "_grid_raw.png"
        raw.write_bytes(imgs[0])
        res = _split_grid(raw, out_paths, sub_types, durations, aspect_ratio, grayscale=grayscale)
        try:
            raw.unlink()
        except Exception:
            pass
        return res

    import base64
    gcw, gch = _grid_canvas_size(aspect_ratio)
    tpl_path = out_paths[0].parent / "_grid_template.png"
    _make_grid_template(tpl_path, gcw, gch)
    tpl_uri = f"data:image/png;base64,{base64.b64encode(tpl_path.read_bytes()).decode()}"

    url = _endpoint(provider, base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"prompt": cell_prompt, "size": f"{gcw}x{gch}",
               "image": tpl_uri, "sequential_image_generation": "disabled",
               "response_format": "url", "stream": False, "watermark": False}
    if model:
        payload["model"] = model
    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy
    try:
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
    except httpx.TimeoutException as e:
        raise ImageError(f"九宫格超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise ImageError(f"九宫格请求错误: {e}", retryable=True, disconnect=True)
    if resp.status_code == 401:
        raise ImageError("绘图 API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise ImageError("绘图限流", retryable=True)
    if resp.status_code >= 500:
        raise ImageError(f"九宫格服务端错误 {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        body = resp.text[:300]
        # 审核拦截：原样重试必再被拒(retryable=False不烧钱)，但标 audit=True 交上层 LLM 改写补救。
        if "sensitive" in body.lower() or "SensitiveContent" in body:
            raise ImageError(f"九宫格被内容审核拒绝(可改写): {body}", retryable=False, audit=True)
        # 其它 4xx(Key错/参数错等)：重试无用，直接占位。
        raise ImageError(f"九宫格被拒 {resp.status_code}: {body}", retryable=False)
    data = resp.json().get("data") or []
    if not data or not data[0].get("url"):
        # 中转站返 200 但 data 空：重试会一次次扣钱拿不到图(同逐张返空口径)，不自动重试。
        raise ImageError("九宫格未返回图片(中转站返空, 不自动重试)", retryable=False)
    raw = out_paths[0].parent / "_grid_raw.png"
    try:
        raw.write_bytes(httpx.get(data[0]["url"], **_kw).content)
    except Exception as e:
        raise ImageError(f"九宫格下载失败: {e}", retryable=True)
    res = _split_grid(raw, out_paths, sub_types, durations, aspect_ratio, grayscale=grayscale)
    for tmp in (tpl_path, raw):
        try: tmp.unlink()
        except Exception: pass
    return res


def generate_images_batch(provider: str, api_key: str, prompt: str,
                          sub_types: list, out_paths: list, durations: list,
                          model: str | None = None, timeout: float = 180.0,
                          aspect_ratio: str = "9:16", ref_uri: str | None = None,
                          base_url: str | None = None, proxy: str | None = None,
                          grayscale: bool = False) -> list:
    """组图：一次请求生成多张（豆包 sequential_image_generation）。返回 ImageResult 列表。
    一次最多 BATCH_MAX 张；同批同次生成 → 风格统一；传 ref_uri → 多张保持同一个人。
    返回数量不足时：缺的那几张占位兜底（不自动单图补，避免多花请求；用户可在画廊多选后
    「一起重新组图」补，仍是一次请求出多张，最省）。"""
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

    url = _endpoint(provider, base_url)
    # OpenAI 兼容协议组图分支：一次请求 n 张（单次 n 最多 10），返回 base64 逐张落盘。
    # 支持参考图：有 ref_uri 时走 /v1/images/edits 实现人物一致性。
    if _is_gpt(model, base_url):
        try:
            # 图生图：有参考图时走 edits，否则走 generations
            if ref_uri:
                imgs = _gpt_edit_request(url, api_key, prompt, _gpt_size(aspect_ratio),
                                        min(n, 10), model, ref_uri, timeout, proxy, label="组图(图生图)")
            else:
                imgs = _gpt_request(url, api_key, prompt, _gpt_size(aspect_ratio),
                                    min(n, 10), model, timeout, proxy, label="组图")
        except ImageError:
            raise
        res = []
        for i, op in enumerate(out_paths):
            if i < len(imgs):
                op.write_bytes(imgs[i])
                _normalize(op, size=(w, h), grayscale=grayscale)
                res.append(ImageResult(str(op), sub_types[i], durations[i], {}))
            else:
                _placeholder(op, sub_types[i], (230, 230, 230), size=(w, h))
                res.append(ImageResult(str(op), sub_types[i], durations[i],
                                       {"fallback": True, "reason": "gpt组图返回数量不足"}))
        return res

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
    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy
    try:
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
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

    # 下载并发化：组图一次出多张，URL 拿到后逐张下载会串行累加耗时。
    # 用线程池同时下，N 张的下载时间从「累加」压成「最慢一张」。
    def _fetch(i, op):
        if i < len(data) and data[i].get("url"):
            try:
                img_bytes = httpx.get(data[i]["url"], **_kw).content
                op.write_bytes(img_bytes)
                _normalize(op, size=(w, h), grayscale=grayscale)
                return ImageResult(str(op), sub_types[i], durations[i], {})
            except Exception:
                pass
        # 该位置没拿到图 → 占位兜底（不自动单图补，避免多花请求；用户可在画廊多选后
        # 「一起重新组图」补，仍是一次请求出多张，最省）。
        _placeholder(op, sub_types[i], (230, 230, 230), size=(w, h))
        return ImageResult(str(op), sub_types[i], durations[i],
                           {"fallback": True, "reason": "组图返回数量不足"})

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(out_paths), 8)) as ex:
        res = list(ex.map(lambda t: _fetch(*t), enumerate(out_paths)))
    return res



def _endpoint(provider: str, base_url_override: str | None = None) -> str:
    # 中转站覆盖：填了 image_base_url 就用它（OpenAI 兼容的代理站，如 APICore，单价更低）。
    if base_url_override:
        return base_url_override.strip()
    eps = {
        "doubao": "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        "kling": "https://api.klingai.com/v1/images/generations",
        "tongyi": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
    }
    if provider not in eps:
        raise ImageError(f"未知绘图供应商: {provider}", retryable=False)
    return eps[provider]


def _normalize(path: Path, size: tuple[int, int] = (WIDTH, HEIGHT), grayscale: bool = False):
    """缩放并居中裁剪到目标尺寸。grayscale=True 时强制转黑白
    （黑白风格下模型常不听文字、图生图更会照彩色参考出彩色，本地转灰度是唯一可靠解）。"""
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
    if grayscale:
        img = img.convert("L").convert("RGB")  # 转灰度再回RGB(保持3通道, 下游统一)
    img.save(path, "PNG")
