"""模块执行的重试与超时辅助。对应 PRD 11.1。"""
import time
from app.services.llm import LLMError
from app.services.image import ImageError


def with_retry(fn, max_retry: int, backoff=(2, 4), disconnect_retry: int | None = None):
    """执行 fn，遇可重试错误指数退避。返回 (result, attempts)。

    disconnect_retry 非空时：对「连接层断连」(ImageError.disconnect=True) 用这个单独的
    重试预算。是否该多重试取决于协议（调用方按模型传不同的值）：
    - 豆包等失败 $0：断连=没受理没扣费，给高预算放心多重试，不烧钱还常能成。
    - gpt-image（tu-zi 等中转站）：断连实测照样扣费，调用方传低预算（同其它失败），别按次烧钱。
    其余失败仍用 max_retry。
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
