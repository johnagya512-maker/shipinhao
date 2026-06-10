"""多平台短视频采集服务。贴链接 → 拿视频地址/标题/博主等元数据。

支持按链接自动识别平台并路由到对应接口（当前对接 TikHub）：
抖音 / TikTok / 快手 / 小红书 / B站 / 微博。

设计要点：
- 平台识别（detect_platform）只看链接域名/口令特征，离线即可判断。
- 每个平台的接口【路径】可被环境变量覆盖（TIKHUB_PATH_<PLATFORM>），
  因为各平台端点可能调整，无需改代码即可校正。
- 返回解析做多层容错：TikHub 各平台响应结构不一，尽量从常见字段路径取值。
- 未配置采集 Key 时抛 CollectUnavailable，由编排降级为"手填逐字稿"，不阻断链路。
- ASR 是平台无关的（只下载 video_url 转写），所以新增平台只需在这里加一条。
"""
import os
import re
import httpx
from dataclasses import dataclass, field

# TikHub 服务基址。
TIKHUB_BASE = os.environ.get("TIKHUB_BASE", "https://api.tikhub.io")

# 各平台「单视频/作品详情」接口路径。这些是按 TikHub 命名习惯给的默认值，
# 若与实际文档不符，用环境变量 TIKHUB_PATH_<PLATFORM> 覆盖即可（无需改代码）。
# <PLATFORM> 取值：DOUYIN / TIKTOK / KUAISHOU / XIAOHONGSHU / BILIBILI / WEIBO。
_DEFAULT_PATHS = {
    "douyin": "/api/v1/douyin/web/fetch_one_video",
    "tiktok": "/api/v1/tiktok/web/fetch_one_video",
    "kuaishou": "/api/v1/kuaishou/web/fetch_one_video",
    "xiaohongshu": "/api/v1/xiaohongshu/web/fetch_feed_notes",
    "bilibili": "/api/v1/bilibili/web/fetch_one_video",
    "weibo": "/api/v1/weibo/web/fetch_post_detail",
}

# 平台中文名，用于元数据展示。
PLATFORM_NAMES = {
    "douyin": "抖音", "tiktok": "TikTok", "kuaishou": "快手",
    "xiaohongshu": "小红书", "bilibili": "B站", "weibo": "微博",
}

# 链接/分享口令 → 平台 的识别特征（按域名关键字）。
_PLATFORM_HINTS = [
    ("douyin", ("douyin.com", "iesdouyin.com", "v.douyin")),
    ("tiktok", ("tiktok.com", "vt.tiktok", "vm.tiktok")),
    ("kuaishou", ("kuaishou.com", "gifshow.com", "chenzhongtech.com", "v.kuaishou")),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com", "xhs.cn")),
    ("bilibili", ("bilibili.com", "b23.tv", "bili2233")),
    ("weibo", ("weibo.com", "weibo.cn", "t.cn")),
]


@dataclass
class CollectResult:
    title: str = ""
    author: str = ""
    play_count: int = 0
    digg_count: int = 0
    video_url: str = ""          # 无水印视频地址，供下游 ASR 取音频
    platform: str = ""           # 识别出的平台 key
    raw_meta: dict = field(default_factory=dict)


class CollectUnavailable(Exception):
    """采集不可用（未配置 Key 等）。编排据此降级为手填模式。"""


class CollectError(Exception):
    """采集调用失败（链接无效、接口报错等）。"""


# 链接提取：从分享口令文本里抠出真实 URL。
_URL_RE = re.compile(r"https?://[^\s，。、）)】\]]+")


def extract_url(text: str) -> str:
    """从分享口令文本里抠出真实链接。整段就是链接时原样返回。"""
    m = _URL_RE.search(text or "")
    if not m:
        raise CollectError("E6001: 未在输入中识别到有效链接")
    return m.group(0).rstrip("/")


def detect_platform(url_or_share: str) -> str:
    """按链接域名识别平台，返回平台 key；无法识别返回空串。"""
    s = (url_or_share or "").lower()
    for key, hints in _PLATFORM_HINTS:
        if any(h in s for h in hints):
            return key
    return ""


def _endpoint_for(platform: str) -> str:
    """取某平台的完整接口 URL，允许环境变量覆盖路径。"""
    override = os.environ.get(f"TIKHUB_PATH_{platform.upper()}")
    path = override or _DEFAULT_PATHS.get(platform)
    if not path:
        raise CollectError(f"E6007: 暂未配置平台 {platform} 的采集接口")
    return TIKHUB_BASE.rstrip("/") + path


def fetch_video(url_or_share: str, provider: str, api_key: str | None,
                timeout: float = 20.0) -> CollectResult:
    """采集任意支持平台的视频元数据。按链接自动识别平台并路由。

    api_key 为空 → 抛 CollectUnavailable，编排降级为手填模式。
    provider 目前支持 tikhub；其他值视为未实现。
    """
    if not api_key:
        raise CollectUnavailable("未配置采集 API Key")

    url = extract_url(url_or_share)
    platform = detect_platform(url_or_share) or detect_platform(url)
    if not platform:
        raise CollectError("E6006: 无法识别链接所属平台（支持：抖音/TikTok/快手/小红书/B站/微博）")

    if provider != "tikhub":
        raise CollectError(f"E6002: 暂不支持的采集供应商: {provider}")

    endpoint = _endpoint_for(platform)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(endpoint, params={"url": url}, headers=headers, timeout=timeout)
    except httpx.RequestError as e:
        raise CollectError(f"E6003: 采集请求失败: {e}")

    if resp.status_code == 401:
        raise CollectError("E6004: 采集 API Key 无效")
    if resp.status_code == 404:
        raise CollectError(
            f"E6008: {PLATFORM_NAMES.get(platform, platform)} 采集接口不存在(404)，"
            f"可能接口路径已变。可用环境变量 TIKHUB_PATH_{platform.upper()} 校正。")
    if resp.status_code >= 400:
        raise CollectError(f"E6005: 采集接口返回 {resp.status_code}: {resp.text[:200]}")

    out = _parse_response(resp.json())
    out.platform = platform
    return out


# 兼容旧调用名。
fetch_douyin = fetch_video


def _first(d: dict, *paths, default=None):
    """按多个候选 key 路径取第一个非空值。path 可为 'a.b.c' 点分嵌套。"""
    for p in paths:
        cur = d
        ok = True
        for seg in p.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return default


def _deep_find_video_url(node, depth=0):
    """深度优先在响应里找第一个像视频地址的 URL（.mp4 或含 play 的 http 链接）。
    各平台 video_url 嵌套位置不同，结构化取不到时用此兜底。"""
    if depth > 8:
        return ""
    if isinstance(node, str):
        if node.startswith("http") and (".mp4" in node or "play" in node or "video" in node):
            return node
        return ""
    if isinstance(node, list):
        for it in node:
            r = _deep_find_video_url(it, depth + 1)
            if r:
                return r
    if isinstance(node, dict):
        # 优先常见键
        for k in ("play_addr", "playAddr", "download_addr", "video_url", "url"):
            if k in node:
                r = _deep_find_video_url(node[k], depth + 1)
                if r:
                    return r
        for v in node.values():
            r = _deep_find_video_url(v, depth + 1)
            if r:
                return r
    return ""


def _parse_response(data: dict) -> CollectResult:
    """解析 TikHub 各平台返回。结构差异大，做多层容错取值。"""
    # 详情主体：不同平台分别在 data.aweme_detail / data / data.0 等位置。
    d = data.get("data") if isinstance(data.get("data"), dict) else data
    aweme = _first(d, "aweme_detail", "video", "note", "item", default=d) or d
    if not isinstance(aweme, dict):
        aweme = d if isinstance(d, dict) else {}

    stats = aweme.get("statistics") or aweme.get("stats") or {}
    author = aweme.get("author") or aweme.get("user") or {}

    title = _first(aweme, "desc", "title", "caption", "content", "share_title", default="") or ""
    author_name = ""
    if isinstance(author, dict):
        author_name = _first(author, "nickname", "name", "nick_name", "screen_name", default="") or ""

    play_count = int(_first(stats, "play_count", "play", "view_count", "playCount", default=0) or 0)
    digg_count = int(_first(stats, "digg_count", "like_count", "digg", "likeCount", default=0) or 0)

    video_url = _deep_find_video_url(aweme) or _deep_find_video_url(data)

    return CollectResult(
        title=title,
        author=author_name,
        play_count=play_count,
        digg_count=digg_count,
        video_url=video_url,
        raw_meta={"id": _first(aweme, "aweme_id", "id", "note_id", default=None), "stats": stats},
    )


# 兼容旧解析名。
_parse_tikhub = _parse_response
