"""加载冻结片段标注，并严格对应实际返回片段。"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path


class ChunkAnnotationError(ValueError):
    """标注缺失、快照漂移或返回片段无法唯一对应。"""


def attach_annotations(cases, dataset_dir: Path, cases_path: Path):
    folder = dataset_dir / "retrieval-snapshot"
    manifest_path = folder / "annotation-manifest.json"
    if not manifest_path.exists():
        return cases
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for base, entries in ((folder, manifest["files_sha256"]),
                          (dataset_dir, manifest["dataset_files_sha256"])):
        for name, expected in entries.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ChunkAnnotationError("标注文件路径越界")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ChunkAnnotationError(f"冻结文件已变化：{name}")
    chunks = tuple(json.loads(line) for line in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines())
    ids = {chunk["chunk_id"] for chunk in chunks}
    if len(ids) != len(chunks):
        raise ChunkAnnotationError("快照片段ID重复")
    for chunk in chunks:
        if hashlib.sha256(chunk["content"].encode()).hexdigest() != chunk["content_sha256"]:
            raise ChunkAnnotationError("片段内容哈希不一致")
    rows = [json.loads(line) for line in (folder / "qrels.jsonl").read_text(encoding="utf-8").splitlines()]
    qrels = {row["case_id"]: row for row in rows}
    if len(qrels) != len(rows):
        raise ChunkAnnotationError("题目相关性标注重复")
    loaded = []
    for case in cases:
        row = qrels.get(case.case_id)
        if row is None or row["snapshot_version"] != manifest["snapshot_version"]:
            raise ChunkAnnotationError(f"题目缺少对应版本标注：{case.case_id}")
        relevant = tuple(row["relevant_chunk_ids"])
        if (len(set(relevant)) != len(relevant) or not set(relevant) <= ids
                or bool(relevant) != case.answerable or row["answerable"] != case.answerable
                or row["judged_pool_size"] != len(chunks)):
            raise ChunkAnnotationError(f"相关片段标注不一致：{case.case_id}")
        loaded.append(replace(case, relevant_chunk_ids=relevant, chunk_snapshot=chunks,
                              chunk_snapshot_version=manifest["snapshot_version"], judge_metrics=()))
    return loaded


def resolve_chunk(source, snapshot):
    """内容必须逐字一致；图片采用解析ID和偏移，正文采用文档ID和序号。"""
    metadata = source.get("metadata") or {}
    explicit = source.get("chunk_id") or metadata.get("chunkId")
    candidates = []
    for chunk in snapshot:
        if source.get("content") != chunk["content"]:
            continue
        if explicit:
            match = explicit in (chunk["chunk_id"], chunk["runtime_chunk_id"])
        else:
            match = (metadata.get("documentId") == chunk["document_id"]
                     and metadata.get("sourceType") == chunk["source_type"])
            if chunk["source_type"] == "image":
                match = match and metadata.get("imageExtractionId") == chunk["image_extraction_id"] and metadata.get("imageChunkOffset") == chunk["image_chunk_offset"]
            else:
                match = match and metadata.get("chunkIndex") == chunk["chunk_index"]
        if match:
            candidates.append(chunk["chunk_id"])
    if len(candidates) != 1:
        raise ChunkAnnotationError("返回片段无法唯一对应冻结快照；需核对内容、ID与分片版本")
    return candidates[0]
