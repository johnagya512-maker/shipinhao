"""抖音采集服务。贴链接 → 拿视频地址/标题/博主/播放量等元数据。

第一版：预留 TikHub 接口位。未配置采集 Key 时抛 CollectUnavailable，
由编排降级为"手填逐字稿/标题"模式，不阻断链路。
"""
import re
import httpx
from dataclasses import dataclass, field

# TikHub 抖音单视频详情端点（按量付费）。占位，接入时以官方文档为准。
TIKHUB_ENDPOINT = "https://api.tikhub.io/api/v1/douyin/web/fetch_one_video"


@dataclass
class CollectResult:
    title: str = ""
    author: str = ""
    play_count: int = 0
    digg_count: int = 0
    video_url: str = ""          # 无水印视频地址，供下游 ASR 取音频
    raw_meta: dict = field(default_factory=dict)


class CollectUnavailable(Exception):
    """采集不可用（未配置 Key 等）。编排据此降级为手填模式。"""


class CollectError(Exception):
    """采集调用失败（链接无效、接口报错等）。"""


# 抖音分享文案里夹带的短链/长链，提取出干净 URL。
_URL_RE = re.compile(r"https?://[^\s，。]+")


def extract_url(text: str) -> str:
    """从抖音分享口令文本里抠出真实链接。整段就是链接时原样返回。"""
    m = _URL_RE.search(text or "")
    if not m:
        raise CollectError("E6001: 未在输入中识别到有效链接")
    return m.group(0).rstrip("/")


def fetch_douyin(url_or_share: str, provider: str, api_key: str | None,
                 timeout: float = 20.0) -> CollectResult:
    """采集抖音视频元数据。

    api_key 为空 → 抛 CollectUnavailable，编排降级为手填模式。
    provider 目前支持 tikhub；其他值视为未实现。
    """
    if not api_key:
        raise CollectUnavailable("未配置采集 API Key")

    url = extract_url(url_or_share)

    if provider != "tikhub":
        raise CollectError(f"E6002: 暂不支持的采集供应商: {provider}")

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(TIKHUB_ENDPOINT, params={"url": url},
                         headers=headers, timeout=timeout)
    except httpx.RequestError as e:
        raise CollectError(f"E6003: 采集请求失败: {e}")

    if resp.status_code == 401:
        raise CollectError("E6004: 采集 API Key 无效")
    if resp.status_code >= 400:
        raise CollectError(f"E6005: 采集接口返回 {resp.status_code}: {resp.text[:200]}")

    return _parse_tikhub(resp.json())


def _parse_tikhub(data: dict) -> CollectResult:
    """解析 TikHub 返回。字段路径以实际接入时的响应为准，做容错取值。"""
    aweme = (data.get("data") or {}).get("aweme_detail") or data.get("data") or {}
    stats = aweme.get("statistics") or {}
    author = aweme.get("author") or {}
    # 无水印地址：play_addr 去掉 watermark，容错多种结构。
    video = aweme.get("video") or {}
    play = video.get("play_addr") or {}
    url_list = play.get("url_list") or []
    return CollectResult(
        title=aweme.get("desc") or "",
        author=author.get("nickname") or "",
        play_count=int(stats.get("play_count") or 0),
        digg_count=int(stats.get("digg_count") or 0),
        video_url=url_list[0] if url_list else "",
        raw_meta={"aweme_id": aweme.get("aweme_id"), "stats": stats},
    )

