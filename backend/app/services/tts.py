"""配音 TTS 服务。分段文本 → 合成整段音频文件。

支持：
- volcano（火山引擎大模型语音合成 HTTP 非流式接口，推荐）
- siliconflow（OpenAI 兼容 /audio/speech，备用）

未配置 TTS Key 时抛 TTSUnavailable，编排降级为"用户手动上传音频"。
长文案按段合成，再用 imageio-ffmpeg 拼接为单一音频，供 compose/jianying 消费。
"""
import base64
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
import httpx
from dataclasses import dataclass

# 火山引擎大模型语音合成 HTTP 非流式接口（一次性返回 base64 音频）。
VOLCANO_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
VOLCANO_CLUSTER = "volcano_tts"
# 默认音色用官方示例里 v1 HTTP 可用的音色（2.0 音色 *_uranus_bigtts 仅 v3 支持）。
VOLCANO_DEFAULT_VOICE = "zh_male_M392_conversation_wvae_bigtts"

# 硅基流动语音合成端点（OpenAI 兼容 /audio/speech）。
SILICONFLOW_TTS_ENDPOINT = "https://api.siliconflow.cn/v1/audio/speech"
SILICONFLOW_DEFAULT_MODEL = "IndexTeam/IndexTTS-2"
SILICONFLOW_DEFAULT_VOICE = "speech:default"


@dataclass
class TTSResult:
    audio_path: str
    duration: float = 0.0
    segment_count: int = 0


class TTSUnavailable(Exception):
    """TTS 不可用（未配置 Key）。编排据此降级为手动上传音频。"""


class TTSError(Exception):
    """TTS 调用失败。"""


def _synth_volcano(text: str, api_key: str, voice: str | None,
                   appid: str | None, timeout: float, speed: float = 1.0) -> bytes:
    """火山引擎大模型 TTS 单段合成，返回 mp3 字节。

    鉴权：Authorization 头为 "Bearer;${access_token}"（Bearer 与 token 以分号分隔）。
    appid 必填；app.token 无实际鉴权作用，可传任意非空串（此处复用 access_token）。
    """
    if not appid:
        raise TTSError("E6207: 火山 TTS 需配置 appid（在配置页填写）")
    headers = {"Authorization": f"Bearer;{api_key}"}
    payload = {
        "app": {"appid": appid, "token": api_key, "cluster": VOLCANO_CLUSTER},
        "user": {"uid": "shipinhao"},
        "audio": {"voice_type": voice or VOLCANO_DEFAULT_VOICE,
                  "encoding": "mp3", "speed_ratio": _clamp_speed(speed)},
        "request": {"reqid": uuid.uuid4().hex, "text": text, "operation": "query"},
    }
    try:
        resp = httpx.post(VOLCANO_TTS_ENDPOINT, json=payload, headers=headers, timeout=timeout)
    except httpx.RequestError as e:
        raise TTSError(f"E6203: TTS 请求失败: {e}")
    if resp.status_code >= 400:
        raise TTSError(f"E6205: TTS 接口返回 {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    code = body.get("code")
    if code != 3000:
        msg = body.get("message", "")
        if "authenticate" in msg or "grant not found" in msg:
            raise TTSError(f"E6204: TTS 鉴权失败（检查 appid/access_token）: {msg}")
        if "voice_type" in msg or code == 3050:
            raise TTSError(f"E6210: 音色不存在或无授权（检查 voice）: {msg}")
        raise TTSError(f"E6208: TTS 合成失败 code={code}: {msg}")
    data = body.get("data")
    if not data:
        raise TTSError("E6209: TTS 返回空音频")
    return base64.b64decode(data)


def _synth_siliconflow(text: str, api_key: str, voice: str | None,
                       model: str | None, timeout: float, speed: float = 1.0) -> bytes:
    """硅基流动 OpenAI 兼容 TTS 单段合成，返回音频字节。"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model or SILICONFLOW_DEFAULT_MODEL,
        "input": text,
        "voice": voice or SILICONFLOW_DEFAULT_VOICE,
        "response_format": "mp3",
        "speed": _clamp_speed(speed),
    }
    try:
        resp = httpx.post(SILICONFLOW_TTS_ENDPOINT, json=payload,
                          headers=headers, timeout=timeout)
    except httpx.RequestError as e:
        raise TTSError(f"E6203: TTS 请求失败: {e}")
    if resp.status_code == 401:
        raise TTSError("E6204: TTS API Key 无效")
    if resp.status_code >= 400:
        raise TTSError(f"E6205: TTS 接口返回 {resp.status_code}: {resp.text[:200]}")
    return resp.content


def _clamp_speed(speed) -> float:
    """语速限制在 0.5~2.0，非法值回退 1.0。"""
    try:
        return max(0.5, min(2.0, float(speed)))
    except (TypeError, ValueError):
        return 1.0


def _synth_one(text: str, provider: str, api_key: str, voice: str | None,
               appid: str | None, model: str | None, timeout: float, speed: float = 1.0) -> bytes:
    """合成单段，按供应商分发，返回音频字节。"""
    if provider == "volcano":
        return _synth_volcano(text, api_key, voice, appid, timeout, speed)
    if provider == "siliconflow":
        return _synth_siliconflow(text, api_key, voice, model, timeout, speed)
    raise TTSError(f"E6202: 暂不支持的 TTS 供应商: {provider}")


def _has_readable(text: str) -> bool:
    """文本是否含可朗读字符（汉字/字母/数字）。纯标点、空白、符号返回 False，
    用于过滤机械切分产生的碎片段（如单独的引号），避免 TTS 报 No readable text。"""
    return bool(re.search(r"[\w一-鿿]", text))


def synthesize(segments: list[dict], provider: str, api_key: str | None,
               out_dir: Path, voice: str | None = None, appid: str | None = None,
               model: str | None = None, timeout: float = 120.0, speed: float = 1.0) -> TTSResult:
    """把分段文本逐段合成，拼接为单一音频文件。

    segments: [{"text": "..."}, ...]（复用 F 模块产物）
    api_key 为空 → 抛 TTSUnavailable，编排降级为手动上传音频。
    返回 TTSResult，audio_path 指向拼接后的整段音频。
    """
    if not api_key:
        raise TTSUnavailable("未配置 TTS API Key")

    # 过滤：不仅排除空白段，还排除「只有标点/符号、无任何可朗读字符」的碎片段
    # （如机械切分把引号拆出的单独 '"'）。火山 TTS 对纯标点会报 3011 No readable text。
    texts = [t for t in (s.get("text", "").strip() for s in segments)
             if t and _has_readable(t)]
    if not texts:
        raise TTSError("E6201: 无可合成的分段文本")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    for i, text in enumerate(texts):
        audio_bytes = _synth_one(text, provider, api_key, voice, appid, model, timeout, speed)
        p = out_dir / f"seg_{i:03d}.mp3"
        p.write_bytes(audio_bytes)
        part_paths.append(p)

    final_path = out_dir / "audio.mp3"
    _concat_audio(part_paths, final_path)
    duration = _probe_duration(final_path)
    return TTSResult(audio_path=str(final_path), duration=duration,
                     segment_count=len(part_paths))


def test_connectivity(provider: str, api_key: str | None, voice: str | None = None,
                      appid: str | None = None, model: str | None = None,
                      timeout: float = 30.0, speed: float = 1.0) -> int:
    """合成一句短文本验证 TTS 配置是否可用。返回音频字节数（>0 即连通）。

    api_key 为空 → 抛 TTSUnavailable；Key/音色/appid 有误 → 抛 TTSError（带错误码）。
    不落盘、不拼接，仅探活。
    """
    if not api_key:
        raise TTSUnavailable("未配置 TTS API Key")
    audio_bytes = _synth_one("测试配音，你好。", provider, api_key, voice,
                             appid, model, timeout, speed)
    if not audio_bytes:
        raise TTSError("E6209: TTS 返回空音频")
    return len(audio_bytes)


def synth_preview(provider: str, api_key: str | None, voice: str | None = None,
                  appid: str | None = None, model: str | None = None,
                  speed: float = 1.0, timeout: float = 30.0) -> bytes:
    """试听：合成一句短文本，返回 mp3 字节（不落盘），供前端直接播放。"""
    if not api_key:
        raise TTSUnavailable("未配置 TTS API Key")
    audio = _synth_one("你好，这是配音试听效果。", provider, api_key, voice,
                       appid, model, timeout, speed)
    if not audio:
        raise TTSError("E6209: TTS 返回空音频")
    return audio


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _concat_audio(parts: list[Path], out_path: Path):
    """用 ffmpeg concat demuxer 把多段 mp3 拼成一段。"""
    if len(parts) == 1:
        out_path.write_bytes(parts[0].read_bytes())
        return
    ff = _ffmpeg_exe()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.as_posix()}'\n")
        list_file = f.name
    try:
        r = subprocess.run([ff, "-y", "-f", "concat", "-safe", "0",
                            "-i", list_file, "-c", "copy", str(out_path)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise TTSError(f"E6206: 音频拼接失败: {r.stderr[:200]}")
    finally:
        Path(list_file).unlink(missing_ok=True)


def _probe_duration(path: Path) -> float:
    """复用 video_module 的音频时长探测。"""
    try:
        from app.modules.video_module import get_audio_duration
        return round(get_audio_duration(str(path)), 2)
    except Exception:
        return 0.0
