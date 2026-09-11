import json
import hashlib
import os
from pathlib import Path
from threading import Lock
from threading import Event, Thread
import time

from locust import between, events, task

from performance.locustfiles.common import locust_compat  # noqa: F401
from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.rag_client import poll_image_parsing, upload_document, upload_failure
from performance.locustfiles.common.data_factory import IMAGE_COUNT


_SAMPLE_LOCK = Lock()
_QUEUE_STOP = Event()
_QUEUE_THREAD: Thread | None = None


def _queue_snapshot() -> dict[str, object]:
    """读取图片 Stream 的长度、未投递数和消费组 pending 数。"""
    from redis import Redis

    host = os.getenv("PERF_IMAGE_QUEUE_REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("PERF_IMAGE_QUEUE_REDIS_PORT", "0"))
    if port <= 0:
        raise RuntimeError("图片解析队列监控缺少 Redis 端口")
    client = Redis(host=host, port=port, db=0, decode_responses=True, socket_timeout=2)
    try:
        stream_name = "rag:image-enrichment"
        group_name = "rag-image-enrichment-workers"
        groups = client.xinfo_groups(stream_name)
        group = next((item for item in groups if str(item.get("name")) == group_name), {})
        pending = client.xpending(stream_name, group_name)
        if isinstance(pending, dict):
            pending_count = int(pending.get("pending") or 0)
        elif isinstance(pending, (list, tuple)):
            pending_count = int(pending[0] or 0) if pending else 0
        else:
            pending_count = 0
        return {
            "timestampEpochMs": round(time.time() * 1000, 3),
            "streamLength": int(client.xlen(stream_name)),
            "undeliveredCount": int(group.get("lag") or 0),
            "pendingCount": pending_count,
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _queue_monitor() -> None:
    target = os.getenv("PERF_IMAGE_QUEUE_METRICS_PATH", "").strip()
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    while not _QUEUE_STOP.is_set():
        try:
            with _SAMPLE_LOCK, path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(_queue_snapshot(), ensure_ascii=False) + "\n")
        except Exception as exc:
            with _SAMPLE_LOCK, path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"error": type(exc).__name__, "timestampEpochMs": round(time.time() * 1000, 3)}, ensure_ascii=False) + "\n")
        _QUEUE_STOP.wait(1.0)


@events.test_start.add_listener
def start_queue_monitor(environment, **_kwargs) -> None:
    global _QUEUE_THREAD
    if os.getenv("PERF_IMAGE_VARIANT", "") == "legacy_serial":
        return
    _QUEUE_STOP.clear()
    _QUEUE_THREAD = Thread(target=_queue_monitor, name="image-queue-monitor", daemon=True)
    _QUEUE_THREAD.start()


@events.quitting.add_listener
def stop_queue_monitor(environment, **_kwargs) -> None:
    _QUEUE_STOP.set()
    if _QUEUE_THREAD is not None:
        _QUEUE_THREAD.join(timeout=3)


def _int_setting(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _append_sample(payload: dict) -> None:
    target = os.getenv("PERF_IMAGE_SAMPLE_PATH", "").strip()
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SAMPLE_LOCK, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_final_metric_snapshot(environment) -> None:
    """把三项业务指标即时落盘为快照，供编排器兜底读取。

    关键决策：写入点必须早于各组 on_stop 的清理逻辑。`test_stopping` 在停止用户之前触发，
    而 `quitting` 只有在进程正常走到 Locust 的 shutdown() 时才会触发；一旦进程在测试结束
    之后被强制中断（例如清理阶段被外部守卫或信号打断），`quitting` 不会执行，快照会连带
    丢失，整轮报告只剩「未生成指标快照」而看不到任何业务数据。提前写可以避免这种证据丢失。
    重复触发是幂等的：同一次运行写入内容一致，后写覆盖前写。
    """
    output = os.getenv("PERF_IMAGE_WORKER_SUMMARY_PATH", "").strip()
    if not output:
        return
    variant = os.getenv("PERF_IMAGE_VARIANT", "unknown")
    metrics = []
    for name in ("图片上传请求完成耗时", "发起上传至图片解析完成总耗时", "图片解析业务校验"):
        entry = environment.stats.get(name, "BUSINESS")
        metrics.append(
            {
                "variant": variant,
                "Type": "BUSINESS",
                "Name": name,
                "Request Count": entry.num_requests,
                "Failure Count": entry.num_failures,
                "Average Response Time": entry.avg_response_time,
                "Median Response Time": entry.median_response_time,
                "Min Response Time": entry.min_response_time,
                "Max Response Time": entry.max_response_time,
                "95%": entry.get_response_time_percentile(0.95),
                "99%": entry.get_response_time_percentile(0.99),
            }
        )
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


@events.test_stopping.add_listener
def snapshot_final_metrics_on_stopping(environment, **_kwargs) -> None:
    # 主要写入点：早于用户 on_stop 清理，能扛住此后发生的强制中断。
    _write_final_metric_snapshot(environment)


@events.quitting.add_listener
def snapshot_final_metrics_on_quitting(environment, **_kwargs) -> None:
    # 兜底写入点：覆盖没有经过 test_stopping 的退出路径，重复写入内容一致。
    _write_final_metric_snapshot(environment)


class ImageWorkerUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_IMAGE_WORKER"
    write_flag = "PERF_ALLOW_RAG_WRITES"
    expected_role = "admin"
    wait_time = between(0, 0)

    def on_start(self) -> None:
        super().on_start()
        self._warmup_count = _int_setting("PERF_IMAGE_WARMUP_SAMPLES", 2)
        self._measure_count = _int_setting("PERF_IMAGE_MEASURE_SAMPLES", 20)
        if self._measure_count <= 0:
            raise RuntimeError("图片解析正式样本数必须大于 0")
        self._target_count = self._warmup_count + self._measure_count
        self._sample_index = 0

    @task
    def upload_and_wait(self) -> None:
        try:
            self._upload_and_wait()
        except Exception as exc:
            phase = "measure" if self._sample_index > self._warmup_count else "warmup"
            _append_sample({
                "variant": os.getenv("PERF_IMAGE_VARIANT", "unknown"),
                "sampleIndex": self._sample_index, "phase": phase,
                "success": False, "error": f"图片请求异常：{type(exc).__name__}",
            })
            if phase == "measure":
                record_metric("图片解析业务校验", 0, "图片请求异常")
            if self._sample_index >= self._target_count:
                locust_compat.finish_run(self.environment)

    def _upload_and_wait(self) -> None:
        if self._sample_index >= self._target_count:
            locust_compat.finish_run(self.environment)
            return
        self._sample_index += 1
        sample_index = self._sample_index
        is_measurement = sample_index > self._warmup_count
        variant = os.getenv("PERF_IMAGE_VARIANT", "unknown")
        document = self.data_factory.document("pdf", image_count=IMAGE_COUNT)
        self.registry.expect_document(document.file_name)
        total_started = monotonic_ms()
        result, stream = upload_document(self.client, document)
        document_id = str((result or {}).get("document_id") or "")
        if document_id:
            self.registry.documents[document_id] = document.file_name
        status = None
        status_elapsed = 0.0
        error = ""
        if not result or not stream or not document_id:
            error = upload_failure(stream)
        elif os.getenv("PERF_IMAGE_USE_STATUS_POLL", "0") == "1":
            status, status_elapsed = poll_image_parsing(
                self.client,
                document_id,
                self.settings.image_poll_timeout_seconds,
                self.settings.image_poll_interval_seconds,
            )
            if not status or str(status.get("status")) != "completed":
                error = f"图片解析状态异常：{(status or {}).get('status') or 'unknown'}"
        upload_ms = stream.complete_ms if stream else 0.0
        total_ms = monotonic_ms() - total_started
        sample = {
            "variant": variant,
            "sampleIndex": sample_index,
            "phase": "measure" if is_measurement else "warmup",
            "documentId": document_id or None,
            "fileName": document.file_name,
            "imageSha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(self.data_factory.root.glob("assets/*")) if document.marker.rsplit('_', 1)[-1] in path.name],
            "uploadRequestMs": round(upload_ms, 3),
            "totalToImageParsedMs": round(total_ms, 3),
            "statusPollMs": round(status_elapsed, 3),
            "status": (status or {}).get("status") if status else "upload-complete",
            "success": not error,
            "error": error or None,
            "startedAtEpochMs": round((time.time() * 1000) - total_ms, 3),
            "finishedAtEpochMs": round(time.time() * 1000, 3),
        }
        _append_sample(sample)
        if is_measurement:
            record_metric("图片上传请求完成耗时", upload_ms, error or None)
            record_metric("发起上传至图片解析完成总耗时", total_ms, error or None)
            record_metric("图片解析业务校验", total_ms, error or None)
        if self._sample_index >= self._target_count:
            locust_compat.finish_run(self.environment)

    def on_stop(self) -> None:
        # 每组对比在编排器完成数据库核验后销毁专属容器与数据卷，避免提前删掉待核验记录。
        if os.getenv("PERF_IMAGE_DEFER_CLEANUP", "0") == "1":
            try:
                if hasattr(self, "data_factory"):
                    self.data_factory.cleanup()
            except Exception:
                self.environment.process_exit_code = 1
                raise
            return
        super().on_stop()
