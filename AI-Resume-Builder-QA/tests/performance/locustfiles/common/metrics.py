import time


def monotonic_ms() -> float:
    return time.perf_counter() * 1000


def record_metric(name: str, elapsed_ms: float, failure: str | None = None) -> None:
    # 纯编排和数据工厂测试不需要启动 Locust，延迟导入避免测试进程触发 Gevent 导入链。
    from locust import events

    events.request.fire(
        request_type="BUSINESS",
        name=name,
        response_time=elapsed_ms,
        response_length=0,
        exception=RuntimeError(failure) if failure else None,
        context={},
    )
