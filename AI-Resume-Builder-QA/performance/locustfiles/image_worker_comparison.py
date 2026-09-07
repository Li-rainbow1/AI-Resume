# author: jf
import json
import os
from pathlib import Path

from locust import events, task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.metrics import record_metric
from performance.locustfiles.common.rag_client import poll_enrichment, upload_document


@events.quitting.add_listener
def write_final_metric_snapshot(environment, **_kwargs) -> None:
    output = os.getenv("PERF_IMAGE_WORKER_SUMMARY_PATH", "").strip()
    if not output:
        return
    concurrency = os.getenv("QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY", "unknown")
    metrics = []
    for name in (f"图片正文入库时间 [c{concurrency}]", "图片队列等待时间", "图片增强完整时间"):
        entry = environment.stats.get(name, "BUSINESS")
        metrics.append(
            {
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

    @task
    def upload_and_wait(self) -> None:
        concurrency = os.getenv("QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY", "unknown")
        document = self.data_factory.document("md", image_count=6)
        self.registry.expect_document(document.file_name)
        result, stream = upload_document(self.client, document)
        if not result or not stream:
            return
        document_id = str(result["document_id"])
        self.registry.documents[document_id] = document.file_name
        record_metric(f"图片正文入库时间 [c{concurrency}]", stream.complete_ms)
        status, elapsed = poll_enrichment(
            self.client,
            document_id,
            self.settings.image_poll_timeout_seconds,
            self.settings.image_poll_interval_seconds,
        )
        expected = status and status.get("status") == "completed" and int(status.get("candidateCount") or 0) == 6
        expected = expected and int(status.get("indexedCount") or 0) == 6 and int(status.get("failedCount") or 0) == 0
        expected = expected and int(status.get("chunkCount") or 0) >= 6
        record_metric(f"图片增强业务校验 [c{concurrency}]", elapsed, None if expected else "图片计数或状态不符合预期")
