"""语音转写 ASR 服务。视频/音频 → 原始逐字稿。

支持：
- volcano（火山引擎「豆包录音文件识别大模型2.0」，异步：submit 提交 → query 轮询。
  直接吃媒体 URL，不必本地下载视频，推荐）
- siliconflow（OpenAI 兼容 /audio/transcriptions，SenseVoice，需先下载音频字节，备用）

未配置 ASR Key 时抛 ASRUnavailable，由编排降级为"手贴逐字稿"模式。
"""
import os
import time
import uuid
import base64
import tempfile
import subprocess
import httpx
from dataclasses import dataclass

# 硅基流动语音转文字端点（OpenAI 兼容）。
SILICONFLOW_ASR_ENDPOINT = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"

# 火山引擎「豆包录音文件识别大模型2.0」异步接口（v3）。
# submit 提交音频 URL → 拿任务（用我们生成的 X-Api-Request-Id 标识）→ query 轮询取结果。
# 鉴权仅需 x-api-key（不需要 appid，区别于火山 TTS）。
VOLCANO_ASR_SUBMIT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
VOLCANO_ASR_QUERY = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
VOLCANO_ASR_RESOURCE_ID = "volc.seedasr.auc"
# 火山 v3 状态码（在响应头 X-Api-Status-Code）：20000000=成功；处理中码继续轮询。
VOLCANO_STATUS_SUCCESS = "20000000"
VOLCANO_STATUS_PROCESSING = {"20000001", "20000002"}  # 排队中/处理中


@dataclass
class ASRResult:
    text: str
    duration: float = 0.0


class ASRUnavailable(Exception):
    """ASR 不可用（未配置 Key）。编排据此降级为手贴逐字稿。"""


class ASRError(Exception):
    """ASR 调用失败。"""


def _extract_text(node, depth=0) -> str:
    """从火山 query 返回里取转写全文，多路径容错。
    优先 result.text；否则拼 result.utterances[].text；再不行深搜任意 text 字段。"""
    if depth > 8 or node is None:
        return ""
    if isinstance(node, dict):
        # 标准结构：result.text
        res = node.get("result")
        if isinstance(res, dict):
            t = (res.get("text") or "").strip()
            if t:
                return t
            utts = res.get("utterances")
            if isinstance(utts, list):
                joined = "".join((u.get("text") or "") for u in utts
                                 if isinstance(u, dict)).strip()
                if joined:
                    return joined
        # 直接 text
        t = node.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
        # 兜底：深搜
        for v in node.values():
            r = _extract_text(v, depth + 1)
            if r:
                return r
    if isinstance(node, list):
        joined = "".join(_extract_text(it, depth + 1) for it in node).strip()
        if joined:
            return joined
    return ""


def _ffmpeg_exe() -> str:
    """ffmpeg 二进制路径（优先系统，回退 imageio-ffmpeg 自带，与 tts/video 模块一致）。"""
    env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env and os.path.exists(env):
        return env
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _media_url_to_audio_b64(media_url: str, timeout: float = 180.0,
                            proxy: str | None = None) -> tuple[str, str]:
    """把媒体 URL 拉流抽成 mp3 音频，返回 (base64字符串, format)。

    为何不直接把 URL 交给火山：抖音等平台的视频 CDN 有防盗链，火山服务器远程下载
    会失败(45000006)或卡在下载阶段超时。改由本端 ffmpeg 拉流（带断点重连、带
    Referer/UA 头），只抽音轨转 16k 单声道 mp3（体积小、识别足够），再以 base64
    随 submit 一起提交，彻底绕开火山远程下载。
    """
    ff = _ffmpeg_exe()
    fd, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    # -reconnect*: 对端掐断时自动重连；-headers: 带防盗链所需的 Referer/UA。
    # -vn 丢视频只留音轨；-t 限时长上限，防超长视频拖垮识别（够用且省时）。
    cmd = [
        ff, "-y",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://www.douyin.com/\r\n",
        "-i", media_url,
        "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
        "-t", "1800",
        out_path,
    ]
    # ffmpeg 自带网络栈，proxy 经环境变量传入（http_proxy/https_proxy）。
    env = dict(os.environ)
    if proxy:
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        _safe_unlink(out_path)
        raise ASRError("E6110: 音频下载/转码超时，可能视频源地址不通或过大，请重试或手动粘贴逐字稿")
    if p.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        tail = (p.stderr or "")[-300:]
        _safe_unlink(out_path)
        raise ASRError(f"E6111: 音频提取失败（ffmpeg 返回 {p.returncode}）: {tail}")
    try:
        data = open(out_path, "rb").read()
    finally:
        _safe_unlink(out_path)
    return base64.b64encode(data).decode(), "mp3"


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _transcribe_volcano(media_url: str, api_key: str, timeout: float = 120.0,
                        poll_interval: float = 3.0, max_poll: int = 60,
                        proxy: str | None = None) -> ASRResult:
    """火山异步识别：本端抽取音频字节 → base64 提交 → 轮询查询结果。

    不直接把 media_url 交给火山远程下载——抖音等平台 CDN 有防盗链，火山服务器
    拉不动（45000006 / 卡在下载阶段超时）。改由本端 ffmpeg 拉流抽音频再上传。
    proxy：本端下载媒体走代理（境外/受限源用）；火山接口本身国内直连。
    """
    # 1) 本端把视频拉成音频 base64（绕开火山远程下载抖音 CDN 的防盗链问题）。
    audio_b64, audio_fmt = _media_url_to_audio_b64(media_url, proxy=proxy)

    req_id = uuid.uuid4().hex
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "X-Api-Resource-Id": VOLCANO_ASR_RESOURCE_ID,
        "X-Api-Request-Id": req_id,
        "X-Api-Sequence": "-1",
    }
    payload = {
        "user": {"uid": "shipinhao"},
        # 用 data 字段直传 base64 音频（而非 url），服务端按 format 解码。
        "audio": {"data": audio_b64, "format": audio_fmt},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,      # 数字规整（一千二 → 1200）
            "enable_punc": True,     # 加标点，逐字稿更可读
            "enable_ddc": False,
        },
    }
    # 提交（火山接口直连，不走 proxy；proxy 只用于上面的媒体下载）。
    submit_kw = {"timeout": timeout}
    try:
        resp = httpx.post(VOLCANO_ASR_SUBMIT, json=payload, headers=headers, **submit_kw)
    except httpx.RequestError as e:
        raise ASRError(f"E6103: ASR 提交失败: {e}")
    sc = resp.headers.get("X-Api-Status-Code", "")
    if resp.status_code == 401 or sc.startswith("4030"):
        raise ASRError("E6104: ASR API Key 无效或无权限（检查 x-api-key 与服务是否开通）")
    if sc and sc != VOLCANO_STATUS_SUCCESS and sc not in VOLCANO_STATUS_PROCESSING:
        msg = resp.headers.get("X-Api-Message", "") or resp.text[:200]
        raise ASRError(f"E6105: ASR 提交返回 {sc}: {msg}")
    if resp.status_code >= 400:
        raise ASRError(f"E6105: ASR 提交返回 {resp.status_code}: {resp.text[:200]}")

    # 轮询查询（同一 X-Api-Request-Id 标识该任务）
    query_headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "X-Api-Resource-Id": VOLCANO_ASR_RESOURCE_ID,
        "X-Api-Request-Id": req_id,
    }
    for _ in range(max_poll):
        time.sleep(poll_interval)
        try:
            q = httpx.post(VOLCANO_ASR_QUERY, json={}, headers=query_headers, **submit_kw)
        except httpx.RequestError as e:
            raise ASRError(f"E6106: ASR 查询失败: {e}")
        sc = q.headers.get("X-Api-Status-Code", "")
        if sc in VOLCANO_STATUS_PROCESSING:
            continue  # 还在处理，继续等
        if sc and sc != VOLCANO_STATUS_SUCCESS:
            msg = q.headers.get("X-Api-Message", "") or q.text[:200]
            raise ASRError(f"E6105: ASR 识别失败 {sc}: {msg}")
        # 成功（或无状态码时按内容判断）：解析文本
        try:
            body = q.json()
        except Exception:
            body = {}
        text = _extract_text(body)
        if text:
            return ASRResult(text=text)
        # 无状态码且无文本：可能仍在处理，继续轮询
        if not sc:
            continue
        # 有成功码但取不到文本 → 视为空结果
        return ASRResult(text="")
    raise ASRError("E6108: ASR 识别超时（轮询多次仍未完成），请重试或手动粘贴逐字稿")


def transcribe(audio_bytes: bytes, provider: str, api_key: str | None,
               filename: str = "audio.mp3", model: str | None = None,
               timeout: float = 120.0) -> ASRResult:
    """把音频字节转写成文本（同步上传）。火山为异步 URL 接口，请用 transcribe_url。

    api_key 为空 → 抛 ASRUnavailable，编排降级为手贴逐字稿模式。
    """
    if not api_key:
        raise ASRUnavailable("未配置 ASR API Key")
    if provider == "volcano":
        raise ASRError("E6109: 火山 ASR 为 URL 异步接口，请用 transcribe_url 传媒体地址")
    if provider != "siliconflow":
        raise ASRError(f"E6102: 暂不支持的 ASR 供应商: {provider}")

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, audio_bytes)}
    data = {"model": model or DEFAULT_ASR_MODEL}
    try:
        resp = httpx.post(SILICONFLOW_ASR_ENDPOINT, headers=headers,
                          files=files, data=data, timeout=timeout)
    except httpx.RequestError as e:
        raise ASRError(f"E6103: ASR 请求失败: {e}")

    if resp.status_code == 401:
        raise ASRError("E6104: ASR API Key 无效")
    if resp.status_code >= 400:
        raise ASRError(f"E6105: ASR 接口返回 {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    return ASRResult(text=(body.get("text") or "").strip())


def transcribe_url(video_url: str, provider: str, api_key: str | None,
                   timeout: float = 120.0, proxy: str | None = None) -> ASRResult:
    """转写媒体 URL。

    - volcano：直接把 URL 交给火山异步识别，不必本地下载（省带宽、更快）。
    - siliconflow：先下载媒体字节再上传转写。
    proxy：下载/请求走代理（境外媒体地址直连不通时用）。
    """
    if not api_key:
        raise ASRUnavailable("未配置 ASR API Key")
    if provider == "volcano":
        return _transcribe_volcano(video_url, api_key, timeout=timeout, proxy=proxy)
    # siliconflow：需先下载再上传
    get_kw = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        get_kw["proxy"] = proxy
    try:
        r = httpx.get(video_url, **get_kw)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ASRError(f"E6106: 下载媒体失败: {e}")
    return transcribe(r.content, provider, api_key, filename="source.mp4", timeout=timeout)
