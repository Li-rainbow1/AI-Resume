"""百炼实时语音热词的本地规则校验。"""

from __future__ import annotations


class BailianHotwordValidationError(ValueError):
    """热词不符合百炼文本或数量限制时抛出的领域异常。"""


# 当前项目支持的两个实时模型均属于百炼文档中的 2000 条上限模型。
_MAX_HOTWORDS_BY_MODEL = {
    "qwen-audio-3.0-asr-flash-streaming": 2_000,
    "fun-asr-realtime": 2_000,
}
_MAX_NON_ASCII_HOTWORD_CHARS = 15
_MAX_ASCII_HOTWORD_SEGMENTS = 7


def normalize_bailian_hotwords(value: object, *, model: str) -> str:
    """按百炼规则清洗热词并返回换行分隔的规范文本。"""
    raw = str(value or "")
    terms: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        term = " ".join(line.strip().split())
        if not term:
            continue
        _validate_term(term, line_number)
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)

    max_terms = _MAX_HOTWORDS_BY_MODEL.get(str(model or "").strip().lower())
    if max_terms is None:
        raise BailianHotwordValidationError("百炼 ASR 模型不受支持")
    if len(terms) > max_terms:
        raise BailianHotwordValidationError(
            f"热词最多配置 {max_terms} 条，当前为 {len(terms)} 条"
        )
    return "\n".join(terms)


def _validate_term(term: str, line_number: int) -> None:
    # 百炼规定只要包含非 ASCII 字符，就按全部字符数限制；中英混合文本也走这条规则。
    if any(ord(char) > 0x7F for char in term):
        if len(term) > _MAX_NON_ASCII_HOTWORD_CHARS:
            raise BailianHotwordValidationError(
                f"第 {line_number} 行热词含非 ASCII 字符，长度不能超过 {_MAX_NON_ASCII_HOTWORD_CHARS} 个字符"
            )
        return

    segments = term.split(" ")
    if len(segments) > _MAX_ASCII_HOTWORD_SEGMENTS:
        raise BailianHotwordValidationError(
            f"第 {line_number} 行纯 ASCII 热词按空格分隔后不能超过 {_MAX_ASCII_HOTWORD_SEGMENTS} 个片段"
        )
