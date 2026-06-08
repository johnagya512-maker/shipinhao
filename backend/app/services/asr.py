"""语音转写 ASR 服务。视频/音频 → 原始逐字稿。

第一版：预留硅基流动 SenseVoice 接口位。未配置 ASR Key 时抛 ASRUnavailable，
由编排降级为"手贴逐字稿"模式。
"""
import httpx
from dataclasses import dataclass

# 硅基流动语音转文字端点（OpenAI 兼容）。占位，接入时以官方文档为准。
SILICONFLOW_ASR_ENDPOINT = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"


@dataclass
class ASRResult:
    text: str
    duration: float = 0.0


class ASRUnavailable(Exception):
    """ASR 不可用（未配置 Key）。编排据此降级为手贴逐字稿。"""


class ASRError(Exception):
    """ASR 调用失败。"""


def transcribe(audio_bytes: bytes, provider: str, api_key: str | None,
               filename: str = "audio.mp3", model: str | None = None,
               timeout: float = 120.0) -> ASRResult:
    """把音频字节转写成文本。

    api_key 为空 → 抛 ASRUnavailable，编排降级为手贴逐字稿模式。
    """
    if not api_key:
        raise ASRUnavailable("未配置 ASR API Key")
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
                   timeout: float = 120.0) -> ASRResult:
    """先下载视频/音频再转写。供采集拿到 video_url 后调用。"""
    if not api_key:
        raise ASRUnavailable("未配置 ASR API Key")
    try:
        r = httpx.get(video_url, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ASRError(f"E6106: 下载媒体失败: {e}")
    return transcribe(r.content, provider, api_key, filename="source.mp4", timeout=timeout)
