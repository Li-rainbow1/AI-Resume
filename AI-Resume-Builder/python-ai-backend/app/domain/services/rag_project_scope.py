"""按登记名称确定检索知识库，不调用模型。"""

import re
import unicodedata


class RagProjectScopeError(ValueError):
    """知识库范围不存在或存在歧义。"""


def normalize_knowledge_base_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def resolve_knowledge_base_scope(query: str, projects: list[dict]) -> list[str]:
    """只根据问题中出现的已登记名称或别名返回知识库并集。

    名称匹配在数据库过滤之前完成，避免先取全库 TopK 后再把其他知识库片段排除。
    对英文名称使用词边界，重叠命中保留完整名称，同一文本位置命中多个知识库时直接提示歧义。
    """
    catalog = {str(item["project_id"]): item for item in projects}
    text = normalize_knowledge_base_name(query)
    matches = []
    for project_id, item in catalog.items():
        for name in [item["name"], *(item.get("aliases") or [])]:
            normalized = normalize_knowledge_base_name(name)
            if not normalized:
                continue
            pattern = re.escape(normalized)
            if normalized[0].isascii() and normalized[0].isalnum():
                pattern = r"(?<![a-z0-9_])" + pattern
            if normalized[-1].isascii() and normalized[-1].isalnum():
                pattern += r"(?![a-z0-9_])"
            for match in re.finditer(pattern, text):
                matches.append((match.start(), match.end(), project_id))
    longest = [hit for hit in matches if not any(
        other[0] <= hit[0] and other[1] >= hit[1] and other[1] - other[0] > hit[1] - hit[0]
        for other in matches
    )]
    spans = {}
    for start, end, project_id in longest:
        spans.setdefault((start, end), set()).add(project_id)
    for ids in spans.values():
        if len(ids) > 1:
            raise RagProjectScopeError("问题中的知识库名称有歧义，请写出完整知识库名称")
    recognized = set()
    for ids in spans.values():
        recognized.update(ids)
    return sorted(recognized)


# 旧内部名称保留一版，供历史导入和外部调用继续工作；查询链路不再传入手动选择。
normalize_project_name = normalize_knowledge_base_name


def resolve_project_scope(query: str, projects: list[dict], selected_ids: list[str] | None = None) -> list[str]:
    """旧项目范围兼容入口，忽略手动选择并按问题自动识别。"""
    _ = selected_ids
    return resolve_knowledge_base_scope(query, projects)
