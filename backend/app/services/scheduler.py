"""进程内任务调度器：全局队列 + 并发闸门，控制同时运行的任务数。

桌面应用是单进程单 worker，用线程池 + Condition 实现轻量调度即可，
无需 Redis/Celery。超出并发上限的任务自动排队，名额释放后按入队顺序执行。

两层并发：
- 任务级（这里）：max_concurrent 个任务同时跑。
- 图片级（image_module.render_images 的 concurrency）：单任务内多张图并发。
总图片请求 ≈ 任务级 × 图片级，调小任一层都能降低绘图平台限流压力。
"""
import threading
from concurrent.futures import ThreadPoolExecutor


class TaskScheduler:
    def __init__(self, max_concurrent: int = 3):
        self._max = max(1, max_concurrent)
        self._running: set[str] = set()      # 正在执行的 task_id
        self._queued: list[str] = []          # 排队中的 task_id（按入队顺序）
        self._cond = threading.Condition()
        # 线程池容量给足，让排队的线程都能挂起等闸门；闸门才是真正的并发控制。
        self._pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="task")

    def set_max(self, n: int):
        """运行时调整并发上限，立即生效：调大则唤醒等待线程补位。"""
        with self._cond:
            self._max = max(1, int(n))
            self._cond.notify_all()

    def get_max(self) -> int:
        with self._cond:
            return self._max

    def submit(self, task_id: str, fn, *args):
        """入队一个任务。fn(*args) 在拿到并发名额后于后台线程执行。
        同一 task_id 已在运行/排队则忽略（防重复提交）。"""
        with self._cond:
            if task_id in self._running or task_id in self._queued:
                return
            self._queued.append(task_id)
        self._pool.submit(self._run, task_id, fn, args)

    def _run(self, task_id: str, fn, args):
        # 等待并发名额
        with self._cond:
            while len(self._running) >= self._max and task_id in self._queued:
                self._cond.wait()
            # 可能在排队期间被移除（取消）。若已不在队列，直接放弃执行。
            if task_id not in self._queued:
                return
            self._queued.remove(task_id)
            self._running.add(task_id)
        try:
            fn(*args)
        finally:
            with self._cond:
                self._running.discard(task_id)
                self._cond.notify_all()

    def cancel_queued(self, task_id: str) -> bool:
        """把仍在排队（未开跑）的任务移出队列。返回是否命中。
        已在运行的任务不在此处理（由任务自身的取消标志/状态控制）。"""
        with self._cond:
            if task_id in self._queued:
                self._queued.remove(task_id)
                self._cond.notify_all()
                return True
            return False

    def stats(self) -> dict:
        with self._cond:
            return {
                "running": list(self._running),
                "queued": list(self._queued),
                "running_count": len(self._running),
                "queued_count": len(self._queued),
                "max_concurrent": self._max,
            }


# 全局单例。max 在 app 启动时按配置 set_max 校正。
scheduler = TaskScheduler(max_concurrent=3)
