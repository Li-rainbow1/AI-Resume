# author: jf
from locust import HttpUser, between

from performance.locustfiles.common.auth import encrypted_login
from performance.locustfiles.common.config import SETTINGS, enabled
from performance.locustfiles.common.data_factory import PerformanceDataFactory
from performance.locustfiles.common.lifecycle import ResourceRegistry


class QaPerformanceUser(HttpUser):
    abstract = True
    host = SETTINGS.base_url
    wait_time = between(SETTINGS.task_wait_min_seconds, SETTINGS.task_wait_max_seconds)
    scenario_flag = ""
    write_flag = ""
    additional_write_flags: tuple[str, ...] = ()
    expected_role = "user"

    def on_start(self) -> None:
        SETTINGS.require(self.scenario_flag, self.write_flag)
        for flag in self.additional_write_flags:
            if not enabled(flag):
                raise RuntimeError(f"性能场景附加写入门禁未开启：{flag}")
        self.settings = SETTINGS
        username = SETTINGS.admin_username if self.expected_role == "admin" else SETTINGS.user_username
        password = SETTINGS.admin_password if self.expected_role == "admin" else SETTINGS.user_password
        session = encrypted_login(self.client, username, password, self.expected_role)
        self.client.headers.update({"Authorization": f"Bearer {session.token}"})
        self.data_factory = PerformanceDataFactory(SETTINGS.run_id)
        self.registry = ResourceRegistry(self.client, SETTINGS, session.user_id)

    def on_stop(self) -> None:
        if hasattr(self, "registry"):
            self.registry.cleanup()
        if hasattr(self, "data_factory"):
            self.data_factory.cleanup()
