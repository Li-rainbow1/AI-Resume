r"""离线复现业务侧分片流程，统计 RAG 评测语料各文档产出的 Chunk 数。

用途：确认干扰文档与主文档的 Chunk 是否真的构成 TopK 竞争、主文档 Chunk 数是否
够 `top_k` 用。只读取业务仓库的分片代码与评测语料，不连接任何服务、不写任何数据。

用法：在 QA 仓库根目录执行
    .\.venv\Scripts\python.exe scripts\inspect_quality_corpus_chunks.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_ROOT = ROOT.parent / "AI-Resume-Builder"
CORPUS_ROOT = ROOT / "testdata" / "quality" / "corpus"
DATASET_PATH = ROOT / "testdata" / "quality" / "golden_dataset.jsonl"

# 与业务侧默认值一致：DEFAULT_RAG_CHUNK_SIZE / DEFAULT_RAG_CHUNK_OVERLAP。
CHUNK_SIZE = 700
CHUNK_OVERLAP = 70


def _load_chunkers():
    backend_root = BUSINESS_ROOT / "python-ai-backend"
    if not backend_root.is_dir():
        raise SystemExit(f"找不到业务仓库分片代码：{backend_root}")
    sys.path.insert(0, str(backend_root))
    from app.domain.models.rag_document import ExtractedDocument
    from app.domain.services.document_chunking_service import DocumentChunkingService
    from app.domain.services.logical_document_splitter_service import LogicalDocumentSplitterService

    return ExtractedDocument, LogicalDocumentSplitterService(), DocumentChunkingService(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )


def _corpus_files() -> list[str]:
    """主文档在前，其余按文件名排序；主文档靠 quality-corpus.md 后缀被数据集识别。"""
    names = sorted(path.name for path in CORPUS_ROOT.glob("*.md"))
    primary = [name for name in names if name.endswith("quality-corpus.md")]
    return primary + [name for name in names if name not in primary]


def _top_k_distribution() -> dict[int, int]:
    distribution: dict[int, int] = {}
    for line in DATASET_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        top_k = int(json.loads(line)["top_k"])
        distribution[top_k] = distribution.get(top_k, 0) + 1
    return dict(sorted(distribution.items()))


def main() -> None:
    extracted_document_cls, splitter, chunker = _load_chunkers()
    total = 0
    per_document: list[tuple[str, int, int]] = []
    for name in _corpus_files():
        text = (CORPUS_ROOT / name).read_text(encoding="utf-8")
        document = extracted_document_cls(
            source_id="s1",
            original_filename=name,
            original_content_type="text/markdown",
            source_type="markdown",
            ingest_source="text_document",
            content=text,
            metadata={},
        )
        logical_parts = splitter.split_document(document)
        chunks = [chunk for part in logical_parts for chunk in chunker.chunk_document(part)]
        total += len(chunks)
        per_document.append((name, len(text), len(chunks)))
        print(f"{name}: chars={len(text)} logical={len(logical_parts)} chunks={len(chunks)}")
        for index, chunk in enumerate(chunks):
            title = chunk.metadata.get("logicalDocumentTitle") or "(preamble)"
            print(f"   [{index}] title={title!r} len={len(chunk.content)}")

    print()
    print(f"文本 Chunk 合计：{total}（chunk_size={CHUNK_SIZE}, chunk_overlap={CHUNK_OVERLAP}）")
    print(f"数据集 top_k 分布：{_top_k_distribution()}")
    if max(_top_k_distribution(), default=0) < total:
        print("提示：TopK 上限小于语料总 Chunk 数，检索必须做取舍，指标才有区分度。")
    if len(per_document) < 2:
        print("警告：语料只有主文档，Precision@K 与 MRR 会退化为定值。")


if __name__ == "__main__":
    main()
