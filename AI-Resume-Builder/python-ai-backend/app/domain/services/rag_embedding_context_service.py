"""从文档原文提取归属信息，仅用于向量化，不改写引用正文。"""

import re
from urllib.parse import unquote

from app.domain.models.rag_document import RagChunk


def document_title(content: str) -> str:
    """只采用明确的一级标题，无标题时保持原有检索文本。"""
    match = re.search(r"^#\s+(.+?)\s*$", content, re.MULTILINE)
    return match.group(1).strip()[:200] if match else ""


def image_embedding_context(content: str, relative_path: str) -> str:
    """按图片的精确相对路径找到章节和替代文本，缺失时仅保留文档标题。"""
    title = document_title(content)
    headings: list[tuple[int, str]] = []
    target = unquote(relative_path).replace("\\", "/").removeprefix("./")
    for line in content.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            level = len(heading.group(1))
            headings = [(depth, text) for depth, text in headings if depth < level]
            headings.append((level, heading.group(2).strip()[:200]))
        for image in re.finditer(r"!\[([^\]]*)\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", line):
            path = unquote(image.group(2).strip("<>")).replace("\\", "/").removeprefix("./")
            if target and path == target:
                parts = [text for _, text in headings]
                if image.group(1).strip():
                    parts.append(image.group(1).strip()[:200])
                return " > ".join(parts)[:800]
    return title


def chunk_embedding_text(chunk: RagChunk) -> str:
    """将短归属信息加在向量输入前，重分块时沿用元数据中的归属信息。"""
    context = str(chunk.metadata.get("embeddingContext") or "").strip()
    return f"{context}\n{chunk.content}" if context else chunk.content
