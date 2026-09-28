from hashlib import sha256
from typing import Any


def normalize_source_content(raw_value: Any) -> str:
    """Normalize only line endings and outer whitespace before hashing/content use."""
    return str(raw_value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def source_dedup_key(item: dict[str, Any]) -> str:
    """Return a stable key for one retrieved chunk.

    The vector store supplies ``chunk_id`` for current PostgreSQL results. Older
    adapters may omit it, so the complete normalized content is hashed instead
    of using a prefix that can collide for distinct chunks.
    """
    metadata = item.get("metadata")
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    chunk_id = str(
        item.get("chunk_id")
        or item.get("chunkId")
        or safe_metadata.get("chunkId")
        or safe_metadata.get("chunk_id")
        or ""
    ).strip()
    if chunk_id:
        return f"id:{chunk_id}"
    content = normalize_source_content(item.get("content"))
    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


def filter_sources_by_similarity(sources: list[dict[str, Any]], *, threshold: float, top_k: int) -> list[dict[str, Any]]:
    # 统一普通查询与面试的筛选规则；过滤后不补齐候选，不修改原始来源。
    # 首位达到0.12可兜底；阈值为0时沿用面试的动态阈值，随后按片段 ID
    # 或完整正文哈希去重。
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

        signature = source_dedup_key(item)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        filtered.append(item)
        if len(filtered) >= top_k:
            break

    return filtered
