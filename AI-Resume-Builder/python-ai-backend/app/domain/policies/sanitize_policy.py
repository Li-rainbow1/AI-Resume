import re

_SUGGESTION_LABELS = {
    "优化建议",
    "建议",
    "后端优化建议",
    "后端建议",
    "suggestion",
    "suggestions",
}
_OPTIMIZED_LABELS = {
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
}
_HEADING_SEP_CLASS = r"[\uFF1A:\-\u2014\u2013]"
_HEADING_SEPS = (":", "\uFF1A", "-", "\u2014", "\u2013")
_SECTION_LABEL_PATTERN = "|".join(
    re.escape(label)
    for label in sorted(_SUGGESTION_LABELS | _OPTIMIZED_LABELS, key=len, reverse=True)
)
_SECTION_LABEL_INLINE_RE = re.compile(
    rf"(?<!^)(?<!\n)\s+(?=(?:#{{1,6}}\s*)?(?:{_SECTION_LABEL_PATTERN}|suggestions?|optimized\s*content|optimized\s*output)\s*{_HEADING_SEP_CLASS})",
    flags=re.IGNORECASE,
)
_HASH_ONLY_ARTIFACT_RE = re.compile(r"^\s*(?:#\s*)+\s*$")


def safe_content(content: str | None) -> str:
    return (content or "").strip()


def sanitize_resume_markdown(markdown: str | None) -> str:
    if not markdown or not markdown.strip():
        return ""

    sanitized = (
        markdown.replace("\ufeff", "")
        .replace("\r\n", "\n")
        .replace("\u3000", " ")
        .replace("\uff03", "#")
    )
    sanitized = _SECTION_LABEL_INLINE_RE.sub("\n", sanitized)
    sanitized = re.sub(r"(?m)(^|\n)(\s*)\*\*(?=\s+)", r"\1\2", sanitized)
    sanitized = re.sub(
        r"(?i)^\s*(?:如下|优化后如下|优化如下|优化后内容如下)\s*[\uFF1A:]?\s*",
        "",
        sanitized,
        count=1,
    )

    rebuilt_lines: list[str] = []
    for raw_line in sanitized.split("\n"):
        line = raw_line
        line = re.sub(r"^(\s*)(\*\*|__)(\s*#{1,6}\s*.+?)\s*\2\s*$", r"\1\3", line)
        line = re.sub(r"^(\s*[-*+]\s*)(\*\*|__)(\s*#{1,6}\s*.+?)\s*\2\s*$", r"\1\3", line)
        line = re.sub(r"^(\s*)\\+(#{1,6})(?=\S)", r"\1\2 ", line)
        line = re.sub(r"^(\s*)\\+(#{1,6})\s+", r"\1\2 ", line)
        line = re.sub(r"([^\n])\s+(#{1,6})\s*(?=\S)", r"\1\n\2 ", line)
        line = re.sub(r"^(\s*)(#{1,6})(\S)", r"\1\2 \3", line)
        line = _normalize_sub_heading_line(line)
        line = _normalize_section_heading_line(line)
        line = re.sub(r"^(\s*)([\u2022\u00B7\u25CF\u25E6\u2023\u25AA\u25AB])\s*", r"\1- ", line)
        line = re.sub(r"^(\s*)([\-*+\uFF0D\u2014\u2013\u2212])(?!\2)(\S)", r"\1- \3", line)
        line = re.sub(r"^(\s*)([\-*+\uFF0D\u2014\u2013\u2212])(?!\2)\s+", r"\1- ", line)
        line = re.sub(r"^(\s*)(\d+)\s*[.\u3002\uFF0E\u3001]\s*(\S)", r"\1\2. \3", line)
        line = re.sub(r"^(\s*)(\d+)\.(?=\S)", r"\1\2. ", line)
        line = re.sub(r"^(\s*)\*\*(?=\s+)", r"\1", line)
        if _HASH_ONLY_ARTIFACT_RE.fullmatch(line):
            continue

        if line.strip():
            rebuilt_lines.append(line)

    sanitized = "\n".join(rebuilt_lines)

    while True:
        next_text = re.sub(r"\*\*\s*\*\*", "", sanitized)
        if next_text == sanitized:
            break
        sanitized = next_text

    if sanitized.count("**") % 2 != 0:
        sanitized = sanitized.replace("**", "")

    return sanitized.strip()


def _is_section_heading_candidate(compact_text: str) -> bool:
    all_labels = _SUGGESTION_LABELS | _OPTIMIZED_LABELS
    if compact_text in all_labels:
        return True
    for label in all_labels:
        if any(compact_text.startswith(f"{label}{sep}") for sep in _HEADING_SEPS):
            return True
    return False


def _normalize_sub_heading_line(line: str) -> str:
    if not line or not line.strip():
        return line

    normalized = line.replace("\uff03", "#").strip()
    normalized = re.sub(r"^(?:[-*+]\s*)+", "", normalized)
    normalized = re.sub(r"^(?:\*\*|__|`)+\s*", "", normalized)
    normalized = re.sub(r"\s*(?:\*\*|__|`)+\s*$", "", normalized)
    normalized = re.sub(r"^(#{1,6})(?=\S)", r"\1 ", normalized).strip()

    if not re.match(r"^#{1,6}\s+.+$", normalized):
        return line

    text = re.sub(r"^#{1,6}\s+", "", normalized).strip()
    text = re.sub(r"^(?:#\s*)+", "", text).strip()
    if not text:
        return line

    compact = re.sub(r"\s+", "", text).lower()
    if _is_section_heading_candidate(compact):
        return line
    return f"### {text}"


def _normalize_section_heading_line(line: str) -> str:
    if not line or not line.strip():
        return line

    normalized = line.replace("\uff03", "#").strip()
    normalized = re.sub(r"^(?:[-*+]\s*)+", "", normalized)
    normalized = re.sub(r"^(?:\*\*|__|`)+\s*", "", normalized)
    normalized = re.sub(r"^#{1,6}\s*", "", normalized)
    normalized = re.sub(r"^(?:\*\*|__|`)+\s*", "", normalized)
    normalized = re.sub(r"\s*(?:\*\*|__|`)+\s*$", "", normalized)
    normalized = re.sub(r"^(?:#\s*)+", "", normalized).strip()
    if not normalized:
        return line

    parts = re.split(_HEADING_SEP_CLASS, normalized, maxsplit=1)
    label = re.sub(r"\s+", "", parts[0]).lower()
    inline = parts[1].strip() if len(parts) > 1 else ""

    canonical_heading: str | None = None
    if label in _SUGGESTION_LABELS:
        canonical_heading = "## 优化建议"
    elif label in _OPTIMIZED_LABELS:
        canonical_heading = "## 优化后内容"

    if canonical_heading is None:
        return line
    if not inline:
        return canonical_heading
    return f"{canonical_heading}\n{inline}"
