# author: jf
from locust import task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.rag_client import upload_document


class FileUploadUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_FILE_UPLOAD"
    write_flag = "PERF_ALLOW_RAG_WRITES"
    expected_role = "admin"

    def on_start(self) -> None:
        super().on_start()
        self.kind_index = 0

    @task
    def upload(self) -> None:
        kinds = ("md", "pdf", "docx")
        document = self.data_factory.document(kinds[self.kind_index % len(kinds)])
        self.kind_index += 1
        self.registry.expect_document(document.file_name)
        result, _ = upload_document(self.client, document)
        if result:
            self.registry.documents[str(result["document_id"])] = document.file_name
