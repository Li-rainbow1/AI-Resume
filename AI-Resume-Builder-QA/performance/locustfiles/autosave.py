# author: jf
from locust import constant, task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.config import SETTINGS


class AutosaveUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_AUTOSAVE"
    write_flag = "PERF_ALLOW_RESUME_WRITES"
    wait_time = constant(SETTINGS.autosave_interval_seconds)

    def on_start(self) -> None:
        super().on_start()
        self.sequence = 0
        self.resume_name = self.data_factory.resume_name()
        with self.client.post(
            "/api/resumes",
            json={"name": self.resume_name, "data": self.data_factory.resume_data()},
            name="/api/resumes [setup]",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"创建简历 HTTP {response.status_code}")
                raise RuntimeError("自动保存测试简历创建失败")
            body = response.json()
            self.resume_id = str(body.get("resumeId") or "")
            if not self.resume_id or body.get("name") != self.resume_name:
                response.failure("创建简历响应字段无效")
                raise RuntimeError("自动保存测试简历创建失败")
            self.registry.resumes[self.resume_id] = self.resume_name
            response.success()

    @task
    def autosave(self) -> None:
        self.sequence += 1
        expected = self.data_factory.resume_data(self.sequence)
        with self.client.put(
            f"/api/resumes/{self.resume_id}",
            json={"name": self.resume_name, "data": expected},
            name="/api/resumes/{id} [autosave]",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"自动保存 HTTP {response.status_code}")
                return
            body = response.json()
            if body.get("resumeId") != self.resume_id or body.get("data") != expected:
                response.failure("自动保存响应与写入内容不一致")
                return
            response.success()
