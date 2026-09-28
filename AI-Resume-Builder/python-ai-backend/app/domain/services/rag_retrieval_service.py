from typing import Any

from app.application.ports.vector_store_port import VectorStorePort
from app.application.ports.rag_project_repository_port import RagProjectRepositoryPort
from app.domain.services.rag_project_scope import resolve_knowledge_base_scope
from app.domain.services.rag_source_filter import normalize_source_content, source_dedup_key
from app.shared.constants.rag import DEFAULT_RAG_TOP_K


class RagRetrieverService:
    def __init__(self, vector_store: VectorStorePort, project_repository: RagProjectRepositoryPort | None = None) -> None:
        self.vector_store = vector_store
        self.project_repository = project_repository
        # 避免在请求链路构建阶段做重量级 import 探测，防止阻塞“结束并评分”流。
        self.llamaindex_available = False

    def query(self, query: str, top_k: int, project_ids: list[str] | None = None, scope_query: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        # 普通查询由 use case 约束为最多 5 段；面试链路需要先取 15 个向量候选，
        # 再交给重排模型筛选，因此这里不提前把内部候选数截回 5。
        safe_top_k = max(1, int(top_k or DEFAULT_RAG_TOP_K))
        # project_ids 仅为旧调用兼容参数；实际范围始终由问题自动识别。
        _ = project_ids
        scope = self.resolve_scope(query if scope_query is None else scope_query)
        sources = self.vector_store.similarity_search(query=query, top_k=safe_top_k, project_ids=scope)
        return self.build_answer_from_sources(sources, max_sources=safe_top_k), sources

    def resolve_scope(self, query: str, project_ids: list[str] | None = None) -> list[str]:
        projects = self.project_repository.list_projects() if self.project_repository else []
        _ = project_ids
        return resolve_knowledge_base_scope(query, projects)

    def list_scope_catalog(self) -> list[dict[str, Any]]:
        return self.project_repository.list_projects() if self.project_repository else []

    def ingest_documents(self, documents: list[dict[str, Any]]) -> int:
        return self.vector_store.add_documents(documents)

    def build_answer_from_sources(
        self,
        sources: list[dict[str, Any]],
        *,
        max_sources: int = DEFAULT_RAG_TOP_K,
    ) -> str:
        # 这里把检索命中的原始片段组装成可直接注入 prompt 的上下文，
        # 保留正文、换行、列表和表格结构，不在注入前截断证据。
        # max_sources 由调用方按业务场景传入：普通问答最多 5 段，面试最多 4 段。
        if not isinstance(sources, list) or not sources:
            return ""

        safe_limit = min(DEFAULT_RAG_TOP_K, max(1, int(max_sources or DEFAULT_RAG_TOP_K)))

        sections: list[str] = []
        seen_signatures: set[str] = set()
        for index, item in enumerate(sources, start=1):
            if not isinstance(item, dict):
                continue

            metadata = item.get("metadata")
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            source_id = str(item.get("source_id") or "").strip()
            source_name = str(
                safe_metadata.get("originalFilename")
                or safe_metadata.get("sourceName")
                or source_id
                or f"片段{index}"
            ).strip()
            content = normalize_source_content(item.get("content"))
            if not content:
                continue

            signature = source_dedup_key({**item, "content": content})
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            similarity_text = self._format_similarity(safe_metadata.get("similarity"))
            prefix_parts = [f"[参考片段{len(sections) + 1}] 来源:{source_name}"]
            if similarity_text:
                prefix_parts.append(f"相似度:{similarity_text}")
            sections.append(f"{' | '.join(prefix_parts)}\n{content}")
            if len(sections) >= safe_limit:
                break

        return "\n\n".join(sections)

    @staticmethod
    def _format_similarity(raw_value: Any) -> str:
        try:
            similarity = float(raw_value)
        except (TypeError, ValueError):
            return ""
        if similarity <= 0:
            return ""
        return f"{similarity:.2f}"
