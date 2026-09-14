from typing import Any


def filter_sources_by_similarity(sources: list[dict[str, Any]], *, threshold: float, top_k: int) -> list[dict[str, Any]]:
    # 统一普通查询与面试的筛选规则；过滤后不补齐候选，不修改原始来源。
    # 首位达到0.12可兜底；阈值为0时沿用面试的动态阈值，随后按来源与正文去重。
    if not isinstance(sources, list):
        return []

    normalized_sources: list[tuple[float, dict[str, Any]]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue

        metadata = item.get("metadata")
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        similarity = safe_metadata.get("similarity")
        try:
            safe_similarity = float(similarity)
        except (TypeError, ValueError):
            safe_similarity = 0.0

        normalized_sources.append((safe_similarity, item))

    if not normalized_sources:
        return []

    normalized_sources.sort(key=lambda pair: pair[0], reverse=True)
    top_similarity = normalized_sources[0][0]
    effective_threshold = threshold
    if effective_threshold <= 0:
        effective_threshold = max(0.18, top_similarity - 0.12)

    filtered: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for index, (safe_similarity, item) in enumerate(normalized_sources):
        if safe_similarity < effective_threshold and not (index == 0 and safe_similarity >= 0.12):
            continue

        source_id = str(item.get("source_id") or "").strip()
        content = str(item.get("content") or "").strip()
        content_signature = content if len(content) <= 120 else content[:117] + "..."
        signature = f"{source_id}:{content_signature}"
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        filtered.append(item)
        if len(filtered) >= top_k:
            break

    return filtered

