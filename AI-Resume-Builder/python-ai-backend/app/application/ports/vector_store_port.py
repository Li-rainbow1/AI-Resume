from typing import Any, Protocol


class VectorStorePort(Protocol):
    # 最基础的写入接口，适用于旧的纯文本入库场景。
    def add_documents(self, documents: list[dict[str, Any]]) -> int: ...

    # 新的写入接口，允许上游自己先算好 embedding 再传入。
    def add_documents_with_embeddings(
        self,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int: ...

    # 查询接口统一返回 source_id/content/metadata 结构，供 RAG 检索层消费。
    def similarity_search(self, query: str, top_k: int, project_ids: list[str] | None = None) -> list[dict[str, Any]]: ...
