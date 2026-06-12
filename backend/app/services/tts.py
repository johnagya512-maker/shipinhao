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


# 火山 TTS 单次合成的安全字数上限。超长文本会被截断/填充大段静音（实测某些超长句
# 合成出 60 秒、大半是静音），故合成前按此长度二次切分。
_TTS_MAX_CHARS = 120


def _split_long(text: str, limit: int = _TTS_MAX_CHARS) -> list[str]:
    """把过长文本切成 ≤limit 的小段：优先按次级标点（逗号/顿号/分号等）断句，
    单个标点小句仍超长时按字数硬切。保证每段都在 TTS 安全长度内。"""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    # 按次级标点切，保留标点在句尾
    pieces = re.split(r"(?<=[，,、；;：:])", text)
    out, buf = [], ""
    for p in pieces:
        if not p:
            continue
        if len(buf) + len(p) <= limit:
            buf += p
        else:
            if buf:
                out.append(buf)
            # 单片仍超长 → 按字数硬切
            while len(p) > limit:
                out.append(p[:limit])
                p = p[limit:]
            buf = p
    if buf:
        out.append(buf)
    return [s for s in out if s.strip()]


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

    # 过滤：排除空白段、纯标点碎片段（火山对纯标点报 3011 No readable text）；
    # 再把超长段二次切分到 TTS 安全长度（超长会被合成成大段静音）。
    texts: list[str] = []
    for s in segments:
        t = (s.get("text", "") or "").strip()
        if not t or not _has_readable(t):
            continue
        for piece in _split_long(t):
            if _has_readable(piece):
                texts.append(piece)
    if not texts:
        raise TTSError("E6201: 无可合成的分段文本")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    for i, text in enumerate(texts):
        p = out_dir / f"seg_{i:03d}.mp3"
        _synth_one_checked(text, provider, api_key, voice, appid, model,
                           timeout, speed, p)
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
    """把多段 mp3 拼成一段。

    用 concat filter 重新编码（而非 demuxer + -c copy）：各段 mp3 有独立的编码器
    延迟与填充，流复制拼接会在段边界错位、爆音，甚至个别段播放无声。重编码消除这些问题，
    代价是多一次编码（音频量小，可忽略）。
    """
    if len(parts) == 1:
        out_path.write_bytes(parts[0].read_bytes())
        return
    ff = _ffmpeg_exe()
    cmd = [ff, "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    n = len(parts)
    # 每个输入的音频流依次喂给 concat filter，输出单条音频流
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    cmd += ["-filter_complex", filt, "-map", "[out]",
            "-c:a", "libmp3lame", "-ar", "44100", "-b:a", "192k", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise TTSError(f"E6206: 音频拼接失败: {r.stderr[:200]}")


def _silence_ratio(path: Path) -> float:
    """估算音频中的静音占比（0~1）。火山 TTS 偶发返回大段静音的异常音频
    （实测某段 60 秒里 58 秒静音），用此识别坏段并触发重试。"""
    try:
        ff = _ffmpeg_exe()
        r = subprocess.run([ff, "-i", str(path), "-af",
                            "silencedetect=noise=-40dB:d=2", "-f", "null", "-"],
                           capture_output=True, text=True)
        sils = re.findall(r"silence_duration:\s*([\d.]+)", r.stderr or "")
        siltot = sum(float(x) for x in sils)
        dur = _probe_duration(path)
        return (siltot / dur) if dur > 0 else 0.0
    except Exception:
        return 0.0


def _synth_one_checked(text: str, provider, api_key, voice, appid, model,
                       timeout, speed, out_path: Path) -> None:
    """合成单段并写盘；若产出大段静音（火山偶发异常），自动重试，最多 3 次。
    重试仍异常则保留最后一次结果（不阻断整体出片）。"""
    last = None
    for attempt in range(3):
        audio = _synth_one(text, provider, api_key, voice, appid, model, timeout, speed)
        out_path.write_bytes(audio)
        # 短文本（<60字）合成出 >20 秒、且静音过半，几乎肯定是异常返回
        if len(text) < 60 and _probe_duration(out_path) > 20 and _silence_ratio(out_path) > 0.5:
            last = out_path
            continue
        return
    # 兜底：用最后一次结果，但裁掉尾部静音，避免整段几十秒空白
    if last is not None:
        _trim_trailing_silence(out_path)


def _trim_trailing_silence(path: Path) -> None:
    """裁掉音频尾部静音（重试仍异常时的兜底，至少不留几十秒空白）。"""
    try:
        ff = _ffmpeg_exe()
        tmp = path.with_suffix(".trim.mp3")
        r = subprocess.run([ff, "-y", "-i", str(path), "-af",
                            "silenceremove=stop_periods=-1:stop_duration=2:stop_threshold=-40dB",
                            "-c:a", "libmp3lame", "-b:a", "192k", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 500:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


def _probe_duration(path: Path) -> float:
    """探测音频时长（秒）。优先 ffmpeg（对刚写出的 mp3 更可靠），回退 moviepy。"""
    # ffmpeg 读时长：解析 stderr 里的 "Duration: HH:MM:SS.ss"
    try:
        ff = _ffmpeg_exe()
        r = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr or "")
        if m:
            h, mn, s = m.groups()
            dur = int(h) * 3600 + int(mn) * 60 + float(s)
            if dur > 0:
                return round(dur, 2)
    except Exception:
        pass
    # 回退 moviepy
    try:
        from app.modules.video_module import get_audio_duration
        return round(get_audio_duration(str(path)), 2)
    except Exception:
        return 0.0
