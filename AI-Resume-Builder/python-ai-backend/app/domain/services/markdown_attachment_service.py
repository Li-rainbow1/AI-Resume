"""Markdown 本地图片引用的解析、路径规范化和匹配规则。"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from urllib.parse import unquote, urlsplit

SUPPORTED_MARKDOWN_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
_IMAGE_PATTERN = re.compile(
    r"!\[[^\]\r\n]*\]\(\s*(?:<([^>\r\n]+)>|([^\s)\r\n]+))"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)",
    re.UNICODE,
)
_REFERENCE_IMAGE_PATTERN = re.compile(
    r"!\[([^\]\r\n]*)\]\[\s*([^\]\r\n]*)\s*\]",
    re.UNICODE,
)
_SHORTCUT_IMAGE_PATTERN = re.compile(
    r"!\[([^\]\r\n]+)\](?!\s*[\[(])",
    re.UNICODE,
)
_REFERENCE_DEFINITION_PATTERN = re.compile(
    r"^\s{0,3}\[([^\]\r\n]+)\]:\s*"
    r"(?:<([^>\r\n]+)>|([^\s\r\n]+))"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class MarkdownAttachmentMatch:
    """一次 Markdown 图片引用分析的稳定结果。"""

    referenced_paths: list[str]
    matched_paths: list[str]
    missing_paths: list[str]
    unreferenced_paths: list[str]


def analyze_markdown_attachments(
    source_text: str,
    attachment_paths: Iterable[str],
) -> MarkdownAttachmentMatch:
    """按正文中的本地图片引用计算附件匹配结果，不读取任何外部 URL。"""
    referenced_paths = extract_markdown_image_paths(source_text)
    normalized_attachments = _unique_supported_paths(attachment_paths)
    attachment_set = set(normalized_attachments)
    matched_paths = [path for path in referenced_paths if path in attachment_set]
    missing_paths = [path for path in referenced_paths if path not in attachment_set]
    referenced_set = set(referenced_paths)
    unreferenced_paths = [path for path in normalized_attachments if path not in referenced_set]
    return MarkdownAttachmentMatch(
        referenced_paths=referenced_paths,
        matched_paths=matched_paths,
        missing_paths=missing_paths,
        unreferenced_paths=unreferenced_paths,
    )


def extract_markdown_image_paths(source_text: str) -> list[str]:
    """提取 Markdown 图片语法里的本地路径，并按出现顺序去重。"""
    paths: list[str] = []
    seen: set[str] = set()
    lines = (source_text or "").splitlines()
    reference_definitions: dict[str, str] = {}
    in_fence = False
    fence_marker = ""
    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        definition_match = _REFERENCE_DEFINITION_PATTERN.match(line)
        if definition_match:
            reference_definitions[_normalize_reference_label(definition_match.group(1))] = (
                definition_match.group(2) or definition_match.group(3) or ""
            )

    in_fence = False
    fence_marker = ""
    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        for match in _IMAGE_PATTERN.finditer(line):
            _append_supported_path(paths, seen, match.group(1) or match.group(2) or "")
        for match in _REFERENCE_IMAGE_PATTERN.finditer(line):
            label = match.group(2) or match.group(1)
            target = reference_definitions.get(_normalize_reference_label(label), "")
            _append_supported_path(paths, seen, target)
        for match in _SHORTCUT_IMAGE_PATTERN.finditer(line):
            target = reference_definitions.get(_normalize_reference_label(match.group(1)), "")
            _append_supported_path(paths, seen, target)
    return paths


def _append_supported_path(paths: list[str], seen: set[str], raw_path: str) -> None:
    path = normalize_markdown_attachment_path(raw_path)
    if path is None or not is_supported_markdown_image_path(path) or path in seen:
        return
    seen.add(path)
    paths.append(path)


def _normalize_reference_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip()).casefold()


def normalize_markdown_attachment_path(raw_path: str) -> str | None:
    """规范化相对附件路径，并拒绝外部、绝对和目录穿越路径。"""
    value = unquote((raw_path or "").strip()).replace("\\", "/")
    if not value or value.startswith(("/", "//")):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    value = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not value or re.match(r"^[A-Za-z]:/", value):
        return None
    raw_parts = value.split("/")
    if any(part == ".." for part in raw_parts):
        return None
    normalized = posixpath.normpath(value)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        return None
    return normalized


def is_supported_markdown_image_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SUPPORTED_MARKDOWN_IMAGE_EXTENSIONS


def _unique_supported_paths(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = normalize_markdown_attachment_path(raw_path)
        if path is None or not is_supported_markdown_image_path(path) or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result
