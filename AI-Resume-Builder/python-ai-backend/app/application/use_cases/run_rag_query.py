from app.domain.services.rag_source_filter import filter_sources_by_similarity
from app.application.dto.rag_dto import RagQueryRequestDto, RagQueryResponseDto, RagSourceDto
from app.bootstrap.container import build_rag_retriever, resolve_settings
from app.shared.constants.rag import DEFAULT_RAG_TOP_K


def run_rag_query(request: RagQueryRequestDto) -> RagQueryResponseDto:
    settings = resolve_settings()
    retriever = build_rag_retriever(settings)
    # 普通查询的 TopK 既控制向量召回，也控制最终上下文，统一限制为最多 5 段；
    # API 层已经做过校验，这里再做一次边界保护，避免内部调用绕过 HTTP schema。
    top_k = min(DEFAULT_RAG_TOP_K, max(1, int(request.top_k or settings.app_rag_top_k)))
    # 旧 projectIds 只保留请求解析兼容，不参与范围确定。
    scope = retriever.resolve_scope(request.query)
    answer, raw_sources = retriever.query(query=request.query.strip(), top_k=top_k, project_ids=scope)
    # 与面试使用同一运行时相似度阈值；先取TopK，再过滤、兜底和去重，不补足条数。
    raw_sources = filter_sources_by_similarity(
        raw_sources, threshold=settings.app_interview_rag_similarity_threshold, top_k=top_k,
    )
    # 摘要从筛选后的来源重建，保证回答与返回片段一致，低分内容不残留在摘要里。
    answer = retriever.build_answer_from_sources(raw_sources, max_sources=top_k)
    catalog = {str(item.get("project_id")): str(item.get("name") or "") for item in retriever.list_scope_catalog()}
    return RagQueryResponseDto(
        answer=answer,
        project_ids=scope,
        knowledge_base_ids=scope,
        knowledge_base_names=[catalog[item] for item in scope if catalog.get(item)],
        sources=[
            RagSourceDto(
                source_id=str(item.get("source_id") or ""),
                content=str(item.get("content") or ""),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for item in raw_sources
        ],
    )
