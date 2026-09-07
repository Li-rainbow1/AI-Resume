# author: jf
import pymysql


class ResourceRegistry:
    def __init__(self, client, settings, user_id: str) -> None:
        self.client = client
        self.settings = settings
        self.user_id = user_id
        self.documents: dict[str, str] = {}
        self.expected_document_names: set[str] = set()
        self.resumes: dict[str, str] = {}
        self.sessions: set[str] = set()

    def expect_document(self, name: str) -> None:
        if not name.startswith(f"qa-rag-{self.settings.run_id}-"):
            raise ValueError("拒绝登记缺少当前运行前缀的知识文档")
        self.expected_document_names.add(name)

    def cleanup(self) -> None:
        self._cleanup_documents()
        self._cleanup_resumes()
        self._cleanup_sessions()

    def _cleanup_documents(self) -> None:
        if not self.expected_document_names and not self.documents:
            return
        found = dict(self.documents)
        page = 1
        while True:
            response = self.client.get(
                "/api/ai/rag/documents",
                params={"page": page, "pageSize": 100, "fileType": "all"},
                name="/api/ai/rag/documents [cleanup]",
            )
            if response.status_code != 200:
                break
            body = response.json()
            for item in response.json().get("items") or []:
                name = str(item.get("fileName") or "")
                if name in self.expected_document_names:
                    found[str(item.get("documentId"))] = name
            if page * int(body.get("pageSize") or 100) >= int(body.get("total") or 0):
                break
            page += 1
        for document_id, name in found.items():
            if name not in self.expected_document_names or not name.startswith(f"qa-rag-{self.settings.run_id}-"):
                continue
            response = self.client.delete(f"/api/ai/rag/documents/{document_id}", name="/api/ai/rag/documents/{id} [cleanup]")
            if response.status_code == 200:
                self.documents.pop(document_id, None)

    def _cleanup_resumes(self) -> None:
        for resume_id, name in list(self.resumes.items()):
            if not name.startswith(f"qa-resume-{self.settings.run_id}-"):
                continue
            with self.client.delete(
                f"/api/resumes/{resume_id}",
                name="/api/resumes/{id} [cleanup]",
                catch_response=True,
            ) as response:
                if response.status_code == 204:
                    self.resumes.pop(resume_id, None)
                    response.success()
                elif response.status_code == 409 and self._delete_exact_resume(resume_id, name):
                    response.success()
                else:
                    response.failure(f"简历清理 HTTP {response.status_code}")

    def _cleanup_sessions(self) -> None:
        for session_id in list(self.sessions):
            if not session_id.startswith(f"qa-interview-{self.settings.run_id}-"):
                continue
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM interview_sessions WHERE session_id=%s AND user_id=%s",
                    (session_id, self.user_id),
                )
                connection.commit()
                if cursor.rowcount == 1:
                    self.sessions.discard(session_id)

    def _delete_exact_resume(self, resume_id: str, name: str) -> bool:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_resumes WHERE resume_id=%s AND user_id=%s AND resume_name=%s",
                (resume_id, self.user_id, name),
            )
            connection.commit()
            if cursor.rowcount == 1:
                self.resumes.pop(resume_id, None)
                return True
        return False

    def _connection(self):
        return pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user="root",
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
        )
