# author: jf
import time

from locust import events


def monotonic_ms() -> float:
    return time.perf_counter() * 1000


def record_metric(name: str, elapsed_ms: float, failure: str | None = None) -> None:
    events.request.fire(
        request_type="BUSINESS",
        name=name,
        response_time=elapsed_ms,
        response_length=0,
        exception=RuntimeError(failure) if failure else None,
        context={},
    )
