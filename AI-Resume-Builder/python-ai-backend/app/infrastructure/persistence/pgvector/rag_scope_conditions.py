"""在向量排序前限定文档可用状态和知识库范围。"""

from sqlalchemy import and_, exists, literal, or_, select


def retrieval_document_condition(documents, chunks, joined_documents, project_ids=None):
    """历史片段按来源关联；指定知识库时要求关联归属一致。"""
    legacy = documents.alias("legacy_scope_documents")
    association = and_(
        legacy.c.source_id.is_not_distinct_from(chunks.c.source_id),
        legacy.c.original_filename.is_not_distinct_from(chunks.c.original_filename),
    )
    blocked = or_(
        legacy.c.archived.is_(True),
        legacy.c.status.in_(["processing", "deleting", "cleanup_failed"]),
    )
    linked = and_(joined_documents.c.status == "ready", joined_documents.c.archived.is_(False))
    legacy_allowed = ~exists(select(literal(1)).select_from(legacy).where(and_(association, blocked)))
    if project_ids:
        linked = and_(linked, joined_documents.c.project_id.in_(project_ids))
        in_scope = and_(legacy.c.scope_kind == "project", legacy.c.project_id.in_(project_ids))
        matching_count = select(literal(1)).select_from(legacy).where(and_(association, in_scope))
        conflicting = or_(legacy.c.project_id.is_(None), ~legacy.c.project_id.in_(project_ids))
        legacy_allowed = and_(
            legacy_allowed,
            exists(matching_count),
            ~exists(select(literal(1)).select_from(legacy).where(and_(association, conflicting))),
        )
    return or_(linked, and_(chunks.c.document_id.is_(None), legacy_allowed))
