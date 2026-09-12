import re
from typing import Any

from quality.models import GoldenCase


def normalize_text(value: object) -> str:
    return re.sub(r"\s+|[，。！？、；：,.!?;:\-_/]", "", str(value or "")).lower()


def source_matches(source: dict[str, Any], expected_document: str, location: dict[str, Any] | None = None) -> bool:
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    filename = str(metadata.get("originalFilename") or "")
    if expected_document and not filename.endswith(expected_document):
        return False
    if not location:
        return True
    for key, expected in location.items():
        candidates = [metadata.get(key)]
        if key == "imageLocator":
            candidates = [metadata.get("imageSourceLocator"), metadata.get("relativePath")]
        if not any(str(expected) in str(candidate or "") for candidate in candidates):
            return False
    return True


def recall_at_k(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    if case.question_type == "no_answer":
        return None
    expected = case.expected_source_location
    hits = sum(any(source_matches(source, case.expected_document, location) for source in sources[: case.top_k]) for location in expected)
    return hits / len(expected) if expected else 0.0


def precision_at_k(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    """Precision@K：TopK 结果中属于预期文档的来源占比，分母固定为 `top_k`。

    分母取 `top_k` 而非实际返回条数，是为了对齐业界标准口径——检索返回不足 K 条时
    （语料 Chunk 总数小于 top_k）如实扣分，不掩盖召回不足。`dataset.py` 已强制校验
    `1 <= top_k <= 5`，因此分母不会为零。

    无答案题不适用，返回 None 以免稀释汇总均值。单项是否判失败由 `runner.py` 的
    `> 0` 门禁决定，该条件等价于「TopK 内至少命中一条预期文档来源」。
    """
    if case.question_type == "no_answer":
        return None
    considered = sources[: case.top_k]
    relevant = sum(source_matches(source, case.expected_document) for source in considered)
    return relevant / case.top_k


def mrr(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    """单条 MRR 贡献：首个命中预期来源的排名倒数（rank 1 → 1.0、rank 3 → 0.33）。

    相关性判据与 Recall@K 完全一致——命中任一 `expected_source_location` 即相关，复用
    `source_matches`，因此 MRR 不需要新增任何标注。`summary.json` 对各题取算术平均即为
    MRR。它补的是 Recall@K 缺的「排序」维度：来源都在 TopK 内时 Recall@K 恒为 1，而
    MRR 会因正确来源排在后面而下降。

    无答案题不适用，返回 None 以免稀释汇总均值。
    """
    if case.question_type == "no_answer":
        return None
    for rank, source in enumerate(sources[: case.top_k], start=1):
        if any(
            source_matches(source, case.expected_document, location)
            for location in case.expected_source_location
        ):
            return 1.0 / rank
    return 0.0


def evaluate_case(case: GoldenCase, answer: str, sources: list[dict[str, Any]]) -> dict[str, float | None]:
    """三项确定性指标全部在检索侧：只读 sources 与数据集标注，不读模型回答。

    `answer` 仍保留在签名里，但当前没有任何指标消费它——加入生成侧检查时它才是入口。
    回答质量目前完全由 DeepEval 的四项指标承担。
    """
    return {
        "recall_at_k": recall_at_k(case, sources),
        "precision_at_k": precision_at_k(case, sources),
        "mrr": mrr(case, sources),
    }
