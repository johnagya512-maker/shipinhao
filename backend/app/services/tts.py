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
import time
import uuid
from pathlib import Path
import httpx
from dataclasses import dataclass

# 火山引擎大模型语音合成 HTTP 非流式接口（一次性返回 base64 音频）。
VOLCANO_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
VOLCANO_CLUSTER = "volcano_tts"
# 复刻音色(声音复刻 ICL)走独立 cluster，普通/多情感音色用 volcano_tts。
VOLCANO_CLUSTER_ICL = "volcano_icl"


def _is_clone_voice(voice: str | None) -> bool:
    """复刻音色判断：火山复刻音色 ID 以 S_ 开头（自定义复刻音色亦可能 ICL_ 前缀）。
    复刻音色合成需切到 volcano_icl cluster，普通/多情感音色不受影响。"""
    return bool(voice) and (voice.startswith("S_") or voice.startswith("ICL_"))
# 默认音色用官方示例里 v1 HTTP 可用的音色（2.0 音色 *_uranus_bigtts 仅 v3 支持）。
# 默认音色：本账号已用 /preview-tts 探活返回 200 实测可用的 2.0(uranus) 音色。
# 老版 *_moon/*_mars 在本账号未授权(grant not found)，切勿用作默认。
VOLCANO_DEFAULT_VOICE = "zh_male_cixingjieshuonan_uranus_bigtts"

# 硅基流动语音合成端点（OpenAI 兼容 /audio/speech）。
SILICONFLOW_TTS_ENDPOINT = "https://api.siliconflow.cn/v1/audio/speech"
SILICONFLOW_DEFAULT_MODEL = "IndexTeam/IndexTTS-2"
SILICONFLOW_DEFAULT_VOICE = "speech:default"


@dataclass
class TTSResult:
    audio_path: str
    duration: float = 0.0
    segment_count: int = 0
    # 每段（与传入 segments 一一对应）的真实音频时长（秒），供字幕按真实时长精确对齐。
    seg_durations: list = None


class TTSUnavailable(Exception):
    """TTS 不可用（未配置 Key）。编排据此降级为手动上传音频。"""


class TTSError(Exception):
    """TTS 调用失败。"""


def _synth_volcano(text: str, api_key: str, voice: str | None,
                   appid: str | None, timeout: float, speed: float = 1.0,
                   emotion: str | None = None, emotion_scale: float = 4.0) -> bytes:
    """火山引擎大模型 TTS 单段合成，返回 mp3 字节。

    鉴权：新版控制台 API Key，放 `x-api-key` 头（老的 Bearer;access_token+appid 火山已逐步下线）。
    一个 API Key 通吃普通(uranus)/多情感(mars)/复刻(S_开头)三类音色，仅 cluster 按音色类型选：
      复刻音色(S_/ICL_前缀) → volcano_icl；其余 → volcano_tts。app 不再需要传 appid。
    emotion 非空时启用情感（enable_emotion+emotion+emotion_scale），让多情感音色有起伏、
    不平读；仅多情感音色(*_emo_*)支持，普通音色传了会被忽略或报错，故由 voices.emotion_for 决定。
    """
    if not api_key:
        raise TTSError("E6207: 火山 TTS 需配置 API Key（在配置页填写新版控制台 API Key）")
    vt = voice or VOLCANO_DEFAULT_VOICE
    cluster = VOLCANO_CLUSTER_ICL if _is_clone_voice(vt) else VOLCANO_CLUSTER
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    audio = {"voice_type": vt,
             "encoding": "mp3", "speed_ratio": _clamp_speed(speed)}
    if emotion:
        audio["enable_emotion"] = True
        audio["emotion"] = emotion
        audio["emotion_scale"] = max(1.0, min(5.0, float(emotion_scale)))
    payload = {
        "app": {"cluster": cluster},
        "user": {"uid": "shipinhao"},
        "audio": audio,
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
        if "authenticate" in msg or "grant not found" in msg or "invalid key" in msg or code == 3001:
            raise TTSError(f"E6204: TTS 鉴权失败（检查新版控制台 API Key）: {msg}")
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


YUNTTS_EDGE_ENDPOINT = "https://www.yuntts.com/api/v1/edge_tts"
YUNTTS_EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _synth_edge_local(text: str, voice: str | None, speed: float, timeout: float) -> bytes:
    """本地 edge-tts 开源库合成（直连微软，免费、不限量、无需 key/会员）。
    speed(0.5~2.0) 换算成 edge-tts 的 rate 百分比字符串：1.0→'+0%'，1.5→'+50%'，0.8→'-20%'。"""
    import asyncio
    import edge_tts
    pct = int(round((_clamp_speed(speed) - 1.0) * 100))
    rate = f"+{pct}%" if pct >= 0 else f"{pct}%"
    v = voice or YUNTTS_EDGE_DEFAULT_VOICE

    async def _run() -> bytes:
        buf = bytearray()
        com = edge_tts.Communicate(text, v, rate=rate)
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        return bytes(buf)

    try:
        audio = asyncio.run(_run())
    except Exception as e:
        raise TTSError(f"E6212: 本地 Edge TTS 合成失败（检查网络/音色名）: {str(e)[:150]}")
    if not audio:
        raise TTSError("E6209: 本地 Edge TTS 返回空音频（可能音色名无效）")
    return audio


def _synth_yuntts_edge(text: str, api_key: str, voice: str | None,
                       timeout: float, speed: float = 1.0) -> bytes:
    """云声配音 Edge TTS 合成（同步接口）。返回 JSON 含 audio_url 再下载；需平台会员权限。
    speed(0.5~2.0 倍) 换算成 Edge 的 rate(-100~100 百分比)：1.0→0，1.5→+50，0.5→-50。"""
    rate = int(round((_clamp_speed(speed) - 1.0) * 100))
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "User-Agent": "Mozilla/5.0"}
    payload = {
        "text": text,
        "voice": voice or YUNTTS_EDGE_DEFAULT_VOICE,
        "rate": rate, "pitch": 0, "volume": 0, "stream": False,
    }
    # 偶发连接被重置（WinError 10054）自动退避重试，不一次就报错。
    resp = None
    for attempt in range(3):
        try:
            resp = httpx.post(YUNTTS_EDGE_ENDPOINT, json=payload, headers=headers, timeout=timeout)
            break
        except httpx.RequestError as e:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise TTSError(f"E6203: TTS 请求失败（已重试）: {e}")
    if resp.status_code == 401:
        raise TTSError("E6204: TTS API Key 无效")
    # 业务错误优先解析 JSON 的 message（如 403 权限不足）给友好提示
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict) and body.get("code") not in (200, None):
        msg = body.get("message") or body.get("msg") or body.get("error") or ""
        if resp.status_code == 403 or body.get("code") == 403 or "权限" in msg or "会员" in msg:
            raise TTSError(f"E6211: 云声配音权限不足，请在 yuntts.com 升级为会员后再用 Edge TTS（{msg}）")
        raise TTSError(f"E6208: TTS 合成失败: {msg or body}")
    if resp.status_code >= 400:
        raise TTSError(f"E6205: TTS 接口返回 {resp.status_code}: {resp.text[:200]}")
    if not isinstance(body, dict):
        raise TTSError(f"E6205: TTS 返回非 JSON: {resp.text[:120]}")
    audio_url = body.get("audio_url") or body.get("url") or (body.get("data") or {}).get("audio_url")
    if not audio_url:
        raise TTSError(f"E6209: TTS 未返回音频地址: {str(body)[:120]}")
    try:
        a = httpx.get(audio_url, timeout=timeout, follow_redirects=True)
        a.raise_for_status()
    except httpx.HTTPError as e:
        raise TTSError(f"E6206: 下载合成音频失败: {e}")
    return a.content


def _clamp_speed(speed) -> float:
    """语速限制在 0.5~2.0，非法值回退 1.0。"""
    try:
        return max(0.5, min(2.0, float(speed)))
    except (TypeError, ValueError):
        return 1.0


def _synth_one(text: str, provider: str, api_key: str, voice: str | None,
               appid: str | None, model: str | None, timeout: float, speed: float = 1.0,
               emotion: str | None = None) -> bytes:
    """合成单段，按供应商分发，返回音频字节。emotion 仅火山多情感音色生效。"""
    if provider == "volcano":
        return _synth_volcano(text, api_key, voice, appid, timeout, speed, emotion=emotion)
    if provider == "siliconflow":
        return _synth_siliconflow(text, api_key, voice, model, timeout, speed)
    if provider == "yuntts_edge":
        return _synth_yuntts_edge(text, api_key, voice, timeout, speed)
    if provider == "edge_local":
        return _synth_edge_local(text, voice, speed, timeout)
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
               model: str | None = None, timeout: float = 120.0, speed: float = 1.0,
               emotion: str | None = None) -> TTSResult:
    """把分段文本逐段合成，拼接为单一音频文件。

    segments: [{"text": "..."}, ...]（复用 F 模块产物）
    api_key 为空 → 抛 TTSUnavailable，编排降级为手动上传音频。
    emotion 非空时火山多情感音色启用情绪（不平读）；未显式传则按所选音色自动推断。
    返回 TTSResult，audio_path 指向拼接后的整段音频。
    """
    if not api_key and provider != "edge_local":
        raise TTSUnavailable("未配置 TTS API Key")
    # 未显式指定情感时，按所选音色自动带上其默认情感（多情感音色才有，普通音色为 None）。
    if emotion is None:
        from app.services import voices as voices_svc
        emotion = voices_svc.emotion_for(voice)

    # 过滤：排除空白段、纯标点碎片段（火山对纯标点报 3011 No readable text）；
    # 再把超长段二次切分到 TTS 安全长度（超长会被合成成大段静音）。
    # 关键：按【输入分段】聚合时长——一个输入分段(如一个分镜的 cap)可能被 _split_long
    # 拆成多个子段合成，但要把这些子段时长求和，作为该输入分段的真实时长。这样返回的
    # seg_durations 长度严格 == 输入分段数，下游图/字幕/音频才能按分镜一一对齐。
    # 被整段过滤掉的纯标点/空白分段计 0 时长但【保留位置】，不打乱分段索引。
    seg_pieces: list[list[str]] = []  # 每个输入分段 → 它的可合成子段列表（可能为空）
    for s in segments:
        t = (s.get("text", "") or "").strip()
        pieces: list[str] = []
        if t and _has_readable(t):
            for piece in _split_long(t):
                if _has_readable(piece):
                    pieces.append(piece)
        seg_pieces.append(pieces)
    if not any(seg_pieces):
        raise TTSError("E6201: 无可合成的分段文本")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    seg_durations: list[float] = []  # 与输入 segments 一一对应（过滤段为 0.0）
    piece_idx = 0
    for pieces in seg_pieces:
        seg_total = 0.0
        for text in pieces:
            p = out_dir / f"seg_{piece_idx:03d}.mp3"
            _synth_one_checked(text, provider, api_key, voice, appid, model,
                               timeout, speed, p, emotion=emotion)
            # 每段单独去残留长静音（火山偶发的尾部静音填充），保证段时长真实、与字幕对齐。
            # 不在拼接后整条去静音——那会打乱"段时长↔字幕"的对应关系，导致字幕错位。
            _remove_long_silence(p)
            part_paths.append(p)
            seg_total += _probe_duration(p)
            piece_idx += 1
        seg_durations.append(round(seg_total, 3))

    final_path = out_dir / "audio.mp3"
    _concat_audio(part_paths, final_path)
    duration = _probe_duration(final_path)
    return TTSResult(audio_path=str(final_path), duration=duration,
                     segment_count=len(part_paths), seg_durations=seg_durations)


def _remove_long_silence(path: Path) -> None:
    """把音频里所有 >1.5 秒的静音压缩到 0.4 秒（去大空白、留自然停顿）。失败不阻断。"""
    try:
        ff = _ffmpeg_exe()
        tmp = path.with_suffix(".clean.mp3")
        r = subprocess.run(
            [ff, "-y", "-i", str(path), "-af",
             "silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-40dB:stop_silence=0.4",
             "-c:a", "libmp3lame", "-b:a", "192k", str(tmp)],
            capture_output=True, text=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


def test_connectivity(provider: str, api_key: str | None, voice: str | None = None,
                      appid: str | None = None, model: str | None = None,
                      timeout: float = 30.0, speed: float = 1.0) -> int:
    """合成一句短文本验证 TTS 配置是否可用。返回音频字节数（>0 即连通）。

    api_key 为空 → 抛 TTSUnavailable；Key/音色/appid 有误 → 抛 TTSError（带错误码）。
    不落盘、不拼接，仅探活。
    """
    if not api_key and provider != "edge_local":
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
    if not api_key and provider != "edge_local":
        raise TTSUnavailable("未配置 TTS API Key")
    from app.services import voices as voices_svc
    audio = _synth_one("你好，这是配音试听效果。", provider, api_key, voice,
                       appid, model, timeout, speed,
                       emotion=voices_svc.emotion_for(voice))
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
                       timeout, speed, out_path: Path, emotion: str | None = None) -> None:
    """合成单段并写盘；若产出异常音频（火山偶发返回不完整/大段静音），自动重试，最多 4 次。
    取多次结果里"最接近正常语速"的一版，避免坏结果进成品。

    判定异常的两种情形（实测均出现过）：
    1. 残缺：只念了开头就结束——音频过短、字/秒畸高（如 41 字仅 4 秒 = 10 字/秒）。
    2. 静音填充：念几个字后填大段静音到几十秒——时长畸长、静音过半。
    正常中文播报约 4~6 字/秒，故以 8 字/秒为上限阈值。
    3. HTTP 错误：TTS 接口返回 400/500 等状态码（火山偶发），同样触发重试。
    """
    cps_limit = 8.0          # 字/秒上限，超过视为没念全
    expect = len(text) / 5.0  # 正常时长估计（5 字/秒）
    best = None              # (越小越好的偏差, 音频字节)
    for attempt in range(4):
        try:
            audio = _synth_one(text, provider, api_key, voice, appid, model, timeout, speed,
                               emotion=emotion)
        except TTSError as e:
            # HTTP 错误（如 400/500）也重试，记录日志并退避
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"TTS 合成异常（第 {attempt+1}/4 次）: {e}")
            if attempt < 3:
                time.sleep(1.0 * (attempt + 1))  # 指数退避：1s, 2s, 3s
            continue
        out_path.write_bytes(audio)
        dur = _probe_duration(out_path)
        cps = (len(text) / dur) if dur > 0 else 999
        # 静音占比：>5 秒的音频就检查（不再要求 >20 秒，否则像"念6秒+静音11秒"
        # 这种 17 秒的段会漏判）。
        sil = _silence_ratio(out_path) if dur > 5 else 0.0
        # 异常：念不全(语速畸高) / 内含或尾随大段静音(静音过半) / 时长无效
        abnormal = (dur <= 0) or (cps > cps_limit) or (sil > 0.4)
        if not abnormal:
            return  # 正常，直接用
        # 记录最优候选：偏离正常时长越小越好
        score = abs(dur - expect)
        if best is None or score < best[0]:
            best = (score, audio)
    # 多次仍异常：写回最优候选，并裁掉残留静音（含段内长停顿）
    if best is not None:
        out_path.write_bytes(best[1])
        _remove_long_silence(out_path)
    else:
        # 4 次全部 HTTP 错误，抛最后一次异常
        raise TTSError(f"E6208: TTS 合成失败（已重试 4 次）: {text[:50]}...")


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
