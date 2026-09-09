# author: jf
import json
import hashlib
import os
from pathlib import Path
from threading import Lock

from locust import between, events, task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.rag_client import poll_image_parsing, upload_document


_SAMPLE_LOCK = Lock()


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


@events.quitting.add_listener
def write_final_metric_snapshot(environment, **_kwargs) -> None:
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
                self.environment.runner.quit()

    def _upload_and_wait(self) -> None:
        if self._sample_index >= self._target_count:
            self.environment.runner.quit()
            return
        self._sample_index += 1
        sample_index = self._sample_index
        is_measurement = sample_index > self._warmup_count
        variant = os.getenv("PERF_IMAGE_VARIANT", "unknown")
        document = self.data_factory.document("pdf", image_count=6)
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
            error = "上传流未返回有效文档 ID"
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
        }
        _append_sample(sample)
        if is_measurement:
            record_metric("图片上传请求完成耗时", upload_ms, error or None)
            record_metric("发起上传至图片解析完成总耗时", total_ms, error or None)
            record_metric("图片解析业务校验", total_ms, error or None)
        if self._sample_index >= self._target_count:
            self.environment.runner.quit()

    def on_stop(self) -> None:
        # 三组对比在编排器完成数据库核验后销毁其专属容器与数据卷，避免提前删掉待核验记录。
        if os.getenv("PERF_IMAGE_DEFER_CLEANUP", "0") == "1":
            try:
                if hasattr(self, "data_factory"):
                    self.data_factory.cleanup()
            except Exception:
                self.environment.process_exit_code = 1
                raise
            return
        super().on_stop()
