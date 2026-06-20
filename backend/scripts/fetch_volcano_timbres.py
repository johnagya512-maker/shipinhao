"""拉取火山账号已授权的大模型音色清单(ListSpeakers)。

用途：把账号真实授权的 voice_type 导出，照实填进 voices.py，杜绝靠猜导致 grant not found。

接口：ListSpeakers（大模型音色列表·新接口），Version=2025-05-20，Region=cn-beijing，
Service=speech_saas_prod。分页用 body 里的 Limit/Offset（单页上限 100）。
返回 Result.Total + Result.Speakers[]，每项含 VoiceType / Name / Gender /
Categories / CategoryKeys / Status(online=可用) / Emotions / ResourceID 等。

鉴权：火山 OpenAPI V4 签名(AK/SK)——与 TTS 的 appid/access_token 是两套！
AK/SK 在火山控制台「访问控制 → API访问密钥(Access Key)」获取。

用法（在 backend 目录下）：
    set VOLC_AK=你的AccessKeyId
    set VOLC_SK=你的SecretAccessKey
    python scripts/fetch_volcano_timbres.py
所有外呼走 Clash 代理(见项目记忆 proxy-all-outbound)，脚本已默认 7897。
"""
import os
import sys
import json
import hashlib
import hmac
import datetime

import httpx

AK = os.environ.get("VOLC_AK", "")
SK = os.environ.get("VOLC_SK", "")
PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7897")

HOST = "open.volcengineapi.com"
REGION = "cn-beijing"
SERVICE = "speech_saas_prod"
VERSION = "2025-05-20"
ACTION = "ListSpeakers"
PAGE_LIMIT = 100  # ListSpeakers 单页上限；用 Limit/Offset 分页拉全


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _request(action: str, query: dict, body: dict) -> dict:
    """火山 OpenAPI V4 签名 POST。query 进 querystring，body 进 JSON。"""
    if not AK or not SK:
        sys.exit("缺 AK/SK：先 set VOLC_AK / VOLC_SK（火山控制台 → 访问控制 → API访问密钥）")

    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    q = dict(query)
    q.update({"Action": action, "Version": VERSION})
    canonical_qs = "&".join(f"{k}={q[k]}" for k in sorted(q))

    payload = json.dumps(body) if body else ""
    payload_hash = _sha256_hex(payload)

    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:application/json\n"
        f"host:{HOST}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{amz_date}\n"
    )
    canonical_request = "\n".join([
        "POST", "/", canonical_qs, canonical_headers, signed_headers, payload_hash,
    ])

    cred_scope = f"{short_date}/{REGION}/{SERVICE}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", amz_date, cred_scope, _sha256_hex(canonical_request),
    ])

    k_date = _sign(SK.encode("utf-8"), short_date)
    k_region = _sign(k_date, REGION)
    k_service = _sign(k_region, SERVICE)
    k_signing = _sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={AK}/{cred_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Content-Type": "application/json",
        "Host": HOST,
        "X-Date": amz_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }
    url = f"https://{HOST}/?{canonical_qs}"
    resp = httpx.post(url, headers=headers, content=payload,
                      proxy=PROXY or None, timeout=30.0)
    if resp.status_code != 200:
        sys.exit(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def main():
    # 分页拉全：body 带 Limit/Offset，直到取满 Result.Total。
    items: list[dict] = []
    offset = 0
    total = None
    while True:
        data = _request(ACTION, {}, {"Limit": PAGE_LIMIT, "Offset": offset})
        result = data.get("Result") or {}
        page = result.get("Speakers") or []
        if total is None:
            total = result.get("Total", len(page))
            print(f"账号授权音色 Total={total}，按每页 {PAGE_LIMIT} 拉取……")
        items.extend(page)
        if not page or len(items) >= (total or 0):
            break
        offset += PAGE_LIMIT
    if not items:
        print("未解析到音色列表，原始返回：")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
        return
    print(f"共拉到 {len(items)} 个音色（VoiceType\t名称\t状态）：\n")
    for it in items:
        vt = it.get("VoiceType", "")
        name = it.get("Name", "")
        status = it.get("Status", "")
        print(f"{vt}\t{name}\t{status}")
    # 导出原始 JSON 供后续照实重建 voices.py
    out = os.path.join(os.path.dirname(__file__), "volcano_timbres.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"\n已导出 → {out}")


if __name__ == "__main__":
    main()
