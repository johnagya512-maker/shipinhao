"""模块执行的重试与超时辅助。对应 PRD 11.1。"""
import time
from app.services.llm import LLMError
from app.services.image import ImageError


def with_retry(fn, max_retry: int, backoff=(2, 4)):
    """执行 fn，遇可重试错误指数退避。返回 (result, attempts)。"""
    attempt = 0
    last_err = None
    while attempt <= max_retry:
        try:
            return fn(), attempt
        except (LLMError, ImageError) as e:
            last_err = e
            if not e.retryable or attempt == max_retry:
                raise
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
            attempt += 1
    if last_err:
        raise last_err
