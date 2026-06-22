"""模块执行的重试与超时辅助。对应 PRD 11.1。"""
import time
from app.services.llm import LLMError
from app.services.image import ImageError


def with_retry(fn, max_retry: int, backoff=(2, 4), disconnect_retry: int | None = None):
    """执行 fn，遇可重试错误指数退避。返回 (result, attempts)。

    disconnect_retry 非空时：对「连接层断连」(ImageError.disconnect=True) 用这个更高的
    重试预算。断连意味着服务器没受理、没扣费（见 cost.image_billable_units），所以即使
    是 gpt(失败也计费)也能放心多重试——重试不烧钱、还常能把图救回来。其余失败仍用 max_retry。
    """
    attempt = 0
    last_err = None
    while True:
        try:
            return fn(), attempt
        except (LLMError, ImageError) as e:
            last_err = e
            # 断连用更高预算；其余用 max_retry。每次按当前错误类型取上限（断连/非断连可能交替）。
            is_disc = bool(getattr(e, "disconnect", False)) and disconnect_retry is not None
            cap = disconnect_retry if is_disc else max_retry
            if not e.retryable or attempt >= cap:
                raise
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
            attempt += 1
    if last_err:
        raise last_err
