"""仅用本地替身验证编排，无 Docker 写入、上传或真实模型请求。"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.performance import run_image_worker_comparison as image_runner


def test_upload_error_keeps_backend_reason():
    from tests.performance.locustfiles.common.rag_client import upload_failure

    stream = SimpleNamespace(events=[{"event": "file-result", "result": {"status": "failed", "error_message": "文件超过大小限制 10MB"}}])
    assert upload_failure(stream) == "文件超过大小限制 10MB"


def test_command_nonzero_keeps_log_and_can_be_summarized(tmp_path):
    log = tmp_path / "command.log"
    result = image_runner._run([sys.executable, "-c", "print('evidence'); raise SystemExit(1)"], dict(os.environ), log, check=False)
    assert result.returncode == 1
    assert "evidence" in log.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="退出码 1"):
        image_runner._run([sys.executable, "-c", "raise SystemExit(1)"], dict(os.environ))


def test_database_accounts_for_all_five_candidates():
    row = dict(status="completed", extractionCount=5, indexedCount=5, skippedCount=0, failedCount=0, imageChunkCount=5, duplicateChunkCount=0)
    image_runner._validate_database_rows({"doc": row}, ["doc"])
    # 后端把 decorative/empty 图片视为合法成功终态：4 张入库 + 1 张跳过应通过。
    image_runner._validate_database_rows(
        {"doc": {**row, "indexedCount": 4, "skippedCount": 1, "imageChunkCount": 4}}, ["doc"]
    )
    # 无跳过却少一张入库，说明存在非终态残留，仍须失败。
    with pytest.raises(RuntimeError):
        image_runner._validate_database_rows({"doc": {**row, "indexedCount": 4}}, ["doc"])
    # 入库与跳过合计超过候选数，说明计数异常。
    with pytest.raises(RuntimeError):
        image_runner._validate_database_rows({"doc": {**row, "skippedCount": 1}}, ["doc"])
    with pytest.raises(RuntimeError):
        image_runner._validate_database_rows({"doc": {**row, "duplicateChunkCount": 1}}, ["doc"])


@pytest.mark.parametrize("remaining", ["", "container-id"])
def test_cleanup_checks_residue(monkeypatch, remaining):
    monkeypatch.setattr(image_runner, "_run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=remaining))
    assert image_runner._verify_stack_removed("arb-perf-image-offline", {}) == bool(remaining)


def test_locust_finishes_without_waiting_and_closes_writer(tmp_path):
    # 独立解释器内验证真实 Locust 生命周期；用户任务不发送任何 HTTP 请求。
    code = '''
import gevent, sys
from locust import User, task
from locust.env import Environment
from locust.stats import StatsCSVFileWriter
from tests.performance.locustfiles.common.locust_compat import finish_run
class OfflineUser(User):
    @task
    def one(self):
        finish_run(self.environment)
        gevent.sleep(1)
    def on_stop(self):
        self.environment.stopped = True
env = Environment(user_classes=[OfflineUser], stop_timeout=0)
runner = env.create_local_runner()
writer = StatsCSVFileWriter(env, [0.95], sys.argv[1])
greenlet = gevent.spawn(writer.stats_writer)
runner.start(1, 1)
runner.greenlet.join(timeout=3)
assert len(runner.greenlet) == 0 and env.stopped
writer.close_files()
assert greenlet.dead and greenlet.exception is None
'''
    environment = {key: value for key, value in os.environ.items() if key != "LOCUST_SKIP_MONKEY_PATCH"}
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "offline")], env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_image_failed_upload_still_writes_summary_and_cleans(tmp_path, monkeypatch):
    def run(command, environment, *args, **kwargs):
        if "locust" in command:
            Path(environment["PERF_IMAGE_SAMPLE_PATH"]).write_text(json.dumps({"phase": "measure", "success": False, "error": "文件超限", "uploadRequestMs": 2, "totalToImageParsedMs": 3}), encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="")
        if command[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(image_runner, "_run", run)
    monkeypatch.setattr(image_runner, "_wait_for_health", lambda *a: None)
    monkeypatch.setattr(image_runner, "_docker_image_id", lambda *a: "fixed-image")
    monkeypatch.setattr(image_runner, "_save_service_errors", lambda *a: None)
    cleanups = []
    monkeypatch.setattr(image_runner, "_cleanup_stack", lambda *a: cleanups.append(True) or 0)
    with pytest.raises(RuntimeError, match="业务失败样本"):
        image_runner._run_variant(variant="legacy_serial", profile="legacy", concurrency=0, use_status_poll=False,
            base_environment={}, run_id="offline", current_image="image", legacy_source=tmp_path,
            report_root=tmp_path, port=19681, warmup_samples=0, measure_samples=1,
            vision_delay_seconds=1, embedding_delay_seconds=0.1, model_mode="mock")
    summary = json.loads((tmp_path / "legacy_serial/summary.json").read_text(encoding="utf-8"))
    assert cleanups and summary["locustExitCode"] == 1
    assert summary["samples"]["failed"] == 1 and summary["cleanupExitCode"] == 0
