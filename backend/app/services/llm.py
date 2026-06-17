"""LLM 客户端。统一 OpenAI 兼容接口，支持 deepseek/openai/qwen/doubao。"""
import httpx
from dataclasses import dataclass

# 各供应商的 OpenAI 兼容端点。
LLM_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
}


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int


class LLMError(Exception):
    """可重试的 LLM 调用错误（超时、5xx、429）。"""
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def call_llm(provider: str, model: str, api_key: str, prompt: str,
             timeout: float = 90.0) -> LLMResult:
    """同步调用 LLM。返回文本与 token 用量。
    超时默认 90s：第三方中转网关（如 packy 等）响应偏慢，30s 易触发 504/超时。"""
    url = LLM_ENDPOINTS.get(provider)
    if not url:
        raise LLMError(f"未知 LLM 供应商: {provider}", retryable=False)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.TimeoutException as e:
        raise LLMError(f"LLM 超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise LLMError(f"LLM 请求错误: {e}", retryable=True)

    if resp.status_code == 401:
        raise LLMError("API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise LLMError("触发限流", retryable=True)
    if resp.status_code in (502, 503, 504):
        raise LLMError(f"大模型网关繁忙({resp.status_code})，请稍后重试", retryable=True)
    if resp.status_code >= 500:
        raise LLMError(f"LLM 服务端错误 {resp.status_code}", retryable=True)
    if resp.status_code >= 400:
        raise LLMError(f"LLM 请求被拒 {resp.status_code}: {resp.text[:200]}", retryable=False)

    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return LLMResult(
        text=text,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
    )


def call_vision(provider: str, model: str, api_key: str, prompt: str,
                image_data_uri: str, timeout: float = 60.0, proxy: str | None = None) -> LLMResult:
    """多模态调用：看图 + 文字提示，返回文本。走 OpenAI 兼容的 content 数组格式
    （text + image_url）。豆包视觉模型与绘图模型同在火山方舟，可复用 image_api_key。
    image_data_uri 形如 data:image/png;base64,xxx。
    proxy 非空时走代理（豆包 ark 域名在受限网络需代理，否则 WinError 10054 断连）。"""
    url = LLM_ENDPOINTS.get(provider)
    if not url:
        raise LLMError(f"未知 LLM 供应商: {provider}", retryable=False)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]}],
        "temperature": 0.3,
    }
    _kw = {"timeout": timeout}
    if proxy:
        _kw["proxy"] = proxy
    try:
        resp = httpx.post(url, json=payload, headers=headers, **_kw)
    except httpx.TimeoutException as e:
        raise LLMError(f"视觉模型超时: {e}", retryable=True)
    except httpx.RequestError as e:
        raise LLMError(f"视觉模型请求错误: {e}", retryable=True)

    if resp.status_code == 401:
        raise LLMError("API Key 无效", retryable=False)
    if resp.status_code == 429:
        raise LLMError("触发限流", retryable=True)
    if resp.status_code >= 500:
        raise LLMError(f"视觉模型服务端错误 {resp.status_code}", retryable=True)
    if resp.status_code >= 400:
        raise LLMError(f"视觉模型请求被拒 {resp.status_code}: {resp.text[:200]}", retryable=False)

    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return LLMResult(
        text=text,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
    )
