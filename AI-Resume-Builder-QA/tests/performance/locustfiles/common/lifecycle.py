class ResourceRegistry:
    def __init__(self, client, settings, user_id: str) -> None:
        self.client = client
        self.settings = settings
        self.user_id = user_id
        self.documents: dict[str, str] = {}
        self.expected_document_names: set[str] = set()

    def expect_document(self, name: str) -> None:
        if not name.startswith(f"qa-rag-{self.settings.run_id}-"):
            raise ValueError("拒绝登记缺少当前运行前缀的知识文档")
        self.expected_document_names.add(name)

    def cleanup(self) -> None:
        errors: list[str] = []
        for cleanup in (self._cleanup_documents,):
            try:
                cleanup()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("性能测试数据清理失败：" + "；".join(errors))

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
                raise RuntimeError(f"知识文档清理列表 HTTP {response.status_code}")
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
            else:
                raise RuntimeError(f"知识文档清理 HTTP {response.status_code}")
