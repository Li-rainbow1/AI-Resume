import re

_SUGGESTION_LABELS = (
    "优化建议",
    "建议",
    "后端优化建议",
    "后端建议",
    "suggestion",
    "suggestions",
)
_OPTIMIZED_LABELS = (
    "优化后内容",
    "优化后的内容",
    "优化内容",
    "优化结果",
    "后端优化后内容",
    "后端优化后的内容",
    "后端优化内容",
    "后端优化结果",
    "optimizedcontent",
    "optimizedoutput",
)
_SECTION_HEADING_ONLY_LINE_RE = re.compile(
    rf"^\s*(?:#{{1,6}}\s*)?(?:{'|'.join(re.escape(label) for label in (*_SUGGESTION_LABELS, *_OPTIMIZED_LABELS))}|optimized\s*content|optimized\s*output)\s*$",
    flags=re.IGNORECASE,
)
_HASH_ONLY_ARTIFACT_LINE_RE = re.compile(r"^\s*(?:#\s*)+\s*$")


def contains_only_section_headings(text: str) -> bool:
    lines = [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not _HASH_ONLY_ARTIFACT_LINE_RE.fullmatch(line.strip())
    ]
    return bool(lines) and all(_SECTION_HEADING_ONLY_LINE_RE.fullmatch(line) for line in lines)
