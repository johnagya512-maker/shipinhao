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
import logging
import os
import re
import httpx
from dataclasses import dataclass, field

# 注意：不能用 logging.getLogger("uvicorn")——uvicorn 启动后会给这个 logger
# 设 propagate=False（只输出到 stderr），传不到 root 的文件 handler，日志会“打了但看不见”。
logger = logging.getLogger(__name__)

# TikHub 服务基址。
TIKHUB_BASE = os.environ.get("TIKHUB_BASE", "https://api.tikhub.io")

# 各平台「按分享链接取单视频/作品详情」接口。值为 (路径, 链接参数名)。
# 优先用 by_share_url / by_url 这类"吃链接"的端点——因为用户贴的是分享链接而非视频ID。
# 路径可被环境变量 TIKHUB_PATH_<PLATFORM> 覆盖、参数名可被 TIKHUB_PARAM_<PLATFORM> 覆盖，
# 无需改代码即可校正。<PLATFORM>：DOUYIN/TIKTOK/KUAISHOU/XIAOHONGSHU/BILIBILI/WEIBO/WECHAT。
# 已据 TikHub 文档确认：douyin/kuaishou/wechat 的路径；其余为最可能的命名，按需用环境变量校正。
# 值为 (路径, 链接参数名, HTTP方法)。wechat 是当前唯一的 POST+JSON body 接口
# （TikHub 该接口不接受 GET query 参数，body 里传 share_url），其余均为 GET。
_DEFAULT_PATHS = {
    "douyin": ("/api/v1/douyin/web/fetch_one_video_by_share_url", "share_url", "GET"),
    "tiktok": ("/api/v1/tiktok/web/fetch_one_video_by_share_url", "share_url", "GET"),
    "kuaishou": ("/api/v1/kuaishou/web/fetch_one_video_by_url", "url", "GET"),
    "xiaohongshu": ("/api/v1/xiaohongshu/web/get_note_info_v2", "share_text", "GET"),
    "bilibili": ("/api/v1/bilibili/web/fetch_one_video", "bv_id", "GET"),
    "weibo": ("/api/v1/weibo/web/fetch_post_detail", "url", "GET"),
    "wechat": ("/api/v1/wechat_channels/v2/fetch_video_detail", "share_url", "POST"),
}

# 平台中文名，用于元数据展示。
PLATFORM_NAMES = {
    "douyin": "抖音", "tiktok": "TikTok", "kuaishou": "快手",
    "xiaohongshu": "小红书", "bilibili": "B站", "weibo": "微博",
    "wechat": "视频号",
}

# 链接/分享口令 → 平台 的识别特征（按域名关键字）。
_PLATFORM_HINTS = [
    ("douyin", ("douyin.com", "iesdouyin.com", "v.douyin")),
    ("tiktok", ("tiktok.com", "vt.tiktok", "vm.tiktok")),
    ("kuaishou", ("kuaishou.com", "gifshow.com", "chenzhongtech.com", "v.kuaishou")),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com", "xhs.cn")),
    ("bilibili", ("bilibili.com", "b23.tv", "bili2233")),
    ("weibo", ("weibo.com", "weibo.cn", "t.cn")),
    ("wechat", ("channels.weixin.qq.com", "finder", "weixin.qq.com/finder",
                "weixin.qq.com/sph", "v.weixin.qq.com", "wxaurl.cn",
                "support.weixin.qq.com")),
]


@dataclass
class CollectResult:
    title: str = ""
    author: str = ""
    play_count: int = 0
    digg_count: int = 0
    video_url: str = ""          # 无水印视频地址，供下游 ASR 取音频
    video_url_candidates: list = field(default_factory=list)  # 多个CDN候选,ASR重试换地址
    platform: str = ""           # 识别出的平台 key
    duration_ms: int = 0         # 视频时长（毫秒），供 ASR 校验音频是否抽全
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


def _endpoint_for(platform: str) -> tuple[str, str, str]:
    """取某平台的 (完整接口 URL, 链接参数名, HTTP方法)。均可被环境变量覆盖：
    TIKHUB_PATH_<PLATFORM> 改路径，TIKHUB_PARAM_<PLATFORM> 改链接参数名，
    TIKHUB_METHOD_<PLATFORM> 改请求方法（GET/POST）。"""
    entry = _DEFAULT_PATHS.get(platform)
    if not entry:
        raise CollectError(f"E6007: 暂未配置平台 {platform} 的采集接口")
    default_path, default_param, default_method = entry
    path = os.environ.get(f"TIKHUB_PATH_{platform.upper()}") or default_path
    param = os.environ.get(f"TIKHUB_PARAM_{platform.upper()}") or default_param
    method = (os.environ.get(f"TIKHUB_METHOD_{platform.upper()}") or default_method).upper()
    return TIKHUB_BASE.rstrip("/") + path, param, method


def fetch_video(url_or_share: str, provider: str, api_key: str | None,
                timeout: float = 20.0, proxy: str | None = None) -> CollectResult:
    """采集任意支持平台的视频元数据。按链接自动识别平台并路由。

    api_key 为空 → 抛 CollectUnavailable，编排降级为手填模式。
    provider 目前支持 tikhub；其他值视为未实现。
    proxy：出站代理地址（如 http://127.0.0.1:7890），境外采集接口直连不通时用。
    """
    if not api_key:
        raise CollectUnavailable("未配置采集 API Key")

    url = extract_url(url_or_share)
    platform = detect_platform(url_or_share) or detect_platform(url)
    if not platform:
        raise CollectError("E6006: 无法识别链接所属平台（支持：抖音/快手/小红书/B站/微博/视频号/TikTok）")

    if provider != "tikhub":
        raise CollectError(f"E6002: 暂不支持的采集供应商: {provider}")

    endpoint, param_name, method = _endpoint_for(platform)
    headers = {"Authorization": f"Bearer {api_key}"}
    # 多数"按链接"接口收原始分享口令也能解析，故传完整 url_or_share（含口令文本）。
    params = {param_name: url_or_share.strip() or url}
    client_kw = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        client_kw["proxy"] = proxy
    # 偶发连接被重置（WinError 10054）/超时多为网络抖动，自动退避重试，不一次就报错。
    last_err = None
    for attempt in range(3):
        try:
            if method == "POST":
                resp = httpx.post(endpoint, json=params, headers=headers, **client_kw)
            else:
                resp = httpx.get(endpoint, params=params, headers=headers, **client_kw)
            break
        except httpx.RequestError as e:
            last_err = e
            if attempt < 2:
                import time
                time.sleep(1.5 * (attempt + 1))  # 1.5s、3s 退避
                continue
            raise CollectError(
                f"E6003: 采集请求失败（已重试{attempt + 1}次）: {e}。"
                f"多为网络波动或防火墙拦截，请检查网络后重试。")
    else:
        raise CollectError(f"E6003: 采集请求失败: {last_err}")

    if resp.status_code == 401:
        raise CollectError("E6004: 采集 API Key 无效")
    if resp.status_code == 404:
        raise CollectError(
            f"E6008: {PLATFORM_NAMES.get(platform, platform)} 采集接口不存在(404)，"
            f"可能接口路径已变。可用环境变量 TIKHUB_PATH_{platform.upper()} 校正。")
    if resp.status_code >= 400:
        raise CollectError(f"E6005: 采集接口返回 {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    # TikHub 部分接口即便请求失败也返回 HTTP 200，真实状态在 body.code 里；
    # data 为空同时 code 非成功码时视为失败，直接报错，避免让下游把兜底垃圾数据当视频地址用。
    if isinstance(body, dict) and not body.get("data") and body.get("code") not in (None, 200, 0):
        msg = body.get("message") or body.get("msg") or str(body)[:200]
        raise CollectError(
            f"E6009: {PLATFORM_NAMES.get(platform, platform)} 采集接口返回失败"
            f"（code={body.get('code')}）: {msg}")

    out = _parse_response(body)
    out.platform = platform
    if not out.video_url:
        # 解析不出视频地址时把完整原始响应落盘（不截断），便于按平台实际字段结构补解析规则。
        # 只保留最近一次，避免占空间；路径固定，出问题后直接去数据目录 logs/ 下翻。
        try:
            import json
            from pathlib import Path
            from app.core.config import _DATA_DIR
            debug_path = Path(_DATA_DIR) / "logs" / f"collect_debug_{platform}.json"
            debug_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.warning(f"[collect] {platform} 响应未解析出 video_url，完整原始响应已写入 {debug_path}")
        except Exception as e:
            logger.warning(f"[collect] {platform} 响应未解析出 video_url，且调试落盘失败: {e}")
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


def _douyin_play_candidates(aweme: dict) -> list:
    """抖音专用：从 aweme_detail.video 取所有可下载播放直链，按优先级排序返回。

    TikHub 返回的 video.play_addr.url_list 里通常有多个地址：
    - www.douyin.com/aweme/v1/play/?video_id=... 官方播放入口：最通畅，排最前；
    - zjcdn.com 的 CDN 直链：带防盗链，第三方裸下载可能被掐断（导致音频截断/文案不全），
      作为备用——ASR 抽音频时若主地址被截断，会自动换列表里下一个重试。
    取不到 video.* 时返回空列表，交由通用深搜兜底。
    """
    video = aweme.get("video") if isinstance(aweme, dict) else None
    if not isinstance(video, dict):
        return []
    # 收集各码率字段下的所有候选地址，保持顺序去重。
    cands: list = []
    for field in ("play_addr", "play_addr_h264", "download_addr", "play_addr_265", "bit_rate"):
        node = video.get(field)
        if isinstance(node, dict):
            for u in (node.get("url_list") or []):
                if isinstance(u, str) and u.startswith("http") and u not in cands:
                    cands.append(u)
        elif isinstance(node, list):  # bit_rate 是数组，元素里再套 play_addr
            for br in node:
                pa = (br or {}).get("play_addr") if isinstance(br, dict) else None
                for u in ((pa or {}).get("url_list") or []):
                    if isinstance(u, str) and u.startswith("http") and u not in cands:
                        cands.append(u)
    # 官方播放入口排到最前，其余保持原序；去掉 dash 分片地址（ffmpeg 拉流易出问题）。
    cands = [u for u in cands if "/play/dash/" not in u]
    official = [u for u in cands if "douyin.com/aweme/v1/play" in u]
    others = [u for u in cands if "douyin.com/aweme/v1/play" not in u]
    ordered = official + others
    # 去重保序后最多保留 5 个候选（够覆盖换 CDN 重试，避免重试过多拖慢）。
    seen, out = set(), []
    for u in ordered:
        if u not in seen:
            seen.add(u); out.append(u)
        if len(out) >= 5:
            break
    return out


def _deep_find_video_url(node, depth=0):
    """深度优先在响应里找第一个像视频地址的 URL（.mp4 或含 play 的 http 链接）。
    各平台 video_url 嵌套位置不同，结构化取不到时用此兜底。"""
    if depth > 8:
        return ""
    if isinstance(node, str):
        # api.tikhub.io / docs.tikhub.io 是接口自身域名，其下链接（文档、路由回显等）
        # 绝不可能是真实视频文件地址——曾把 wechat 接口失败时返回的文档链接误判成视频地址。
        if node.startswith("http") and "tikhub.io" not in node and (
                ".mp4" in node or "play" in node or "video" in node):
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

    candidates = _douyin_play_candidates(aweme)
    video_url = (candidates[0] if candidates
                 else _deep_find_video_url(aweme) or _deep_find_video_url(data))
    if not candidates and video_url:
        candidates = [video_url]

    # 视频时长（毫秒）：抖音在 video.duration / aweme.duration；多路径容错。
    video_node = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    duration_ms = int(_first(video_node, "duration", default=0)
                      or _first(aweme, "duration", default=0) or 0)

    return CollectResult(
        title=title,
        author=author_name,
        play_count=play_count,
        digg_count=digg_count,
        video_url=video_url,
        video_url_candidates=candidates,
        duration_ms=duration_ms,
        raw_meta={"id": _first(aweme, "aweme_id", "id", "note_id", default=None), "stats": stats},
    )


# 兼容旧解析名。
_parse_tikhub = _parse_response
