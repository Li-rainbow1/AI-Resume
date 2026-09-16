"""冻结清单一套实现：该冻哪些文件、怎么核。

「冻结」= 在跑正式评测之前，把会影响分数的输入钉死成一份字节级哈希清单，运行时逐项
比对，任一项对不上就拒绝开跑。目的是让报告里的数字**可归因**：数据集、评分代码、被测
模型配置、判分器任意一项在无人察觉时变了，分数差异就没法归因到任何一项。

为什么必须是一份实现：清单的**生成方**（`scripts/freeze_quality_dataset.py`）与
**校验方**（`runner.py`、`interview_runner.py`）如果各自维护一份「该冻哪些文件」，
两边迟早会漂移，而漂移的方向永远是「校验比生成宽松」——那等于门禁悄悄失效。

三个易踩的点，都由本模块统一收口：

1. **键的基准不同**：`files_sha256` 的键相对**数据集目录**，`qa_code_sha256` 的键相对
   **QA 仓库根**。
2. **清单与历史归档不能冻自己**：否则每次写清单都会让上一次的校验失败。
3. **字节级**：算的是 `read_bytes()` 的 sha256，换行风格与文件末尾有无换行都算。本仓库
   `metrics.py` 是 CRLF 与裸 LF 混合、`reporting.py` 纯 CRLF，用编辑器随手保存一次就会
   让哈希全变。所以清单本身按 **CRLF + 无末尾换行** 写。
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

FREEZE_MANIFEST_NAME = "freeze-manifest.json"
FREEZE_HISTORY_DIR = "freeze-history"

# 参与冻结的评测代码：这几处任一改动都可能改变分数（`quality/` 是评分与编排，
# `clients/` 是请求形状，`fixtures/` 是运行环境读取）。
_FROZEN_CODE_DIRS = ("quality", "clients", "fixtures")
# 隔离环境定义本身也影响结果（模型地址、依赖版本），改名会让哈希锚点失效。
_FROZEN_CODE_FILES = ("compose.interview-quality.yml",)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def iter_dataset_files(dataset_dir: Path) -> list[Path]:
    """数据集目录里参与冻结的文件，按相对路径排序。

    排除清单自身与 `freeze-history/`：前者每次写都会变，后者是历史归档、本就会增长。
    """
    root = dataset_dir.resolve()
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == FREEZE_MANIFEST_NAME or relative.startswith(f"{FREEZE_HISTORY_DIR}/"):
            continue
        if "__pycache__" in path.parts:
            continue
        files.append(path)
    return files


def iter_code_files(qa_root: Path) -> list[Path]:
    """QA 仓库里参与冻结的评测代码，按相对路径排序。"""
    root = qa_root.resolve()
    files: list[Path] = []
    for directory in _FROZEN_CODE_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        files.extend(
            path
            for path in sorted(base.rglob("*.py"))
            if path.is_file() and "__pycache__" not in path.parts
        )
    files.extend(
        root / name for name in _FROZEN_CODE_FILES if (root / name).is_file()
    )
    return files


def file_hashes(root: Path, files: Iterable[Path]) -> dict[str, str]:
    """以 `root` 为基准的相对路径为键、字节哈希为值。"""
    base = root.resolve()
    return {path.resolve().relative_to(base).as_posix(): sha256_file(path) for path in files}


def read_manifest(dataset_dir: Path) -> dict[str, Any] | None:
    """读数据集目录下的冻结清单；不存在返回 None（表示这一轮不做冻结校验）。"""
    path = dataset_dir / FREEZE_MANIFEST_NAME
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"冻结清单不是 JSON 对象：{path}")
    return payload


def write_manifest(dataset_dir: Path, payload: Mapping[str, Any]) -> Path:
    """按约定写清单：CRLF + 无末尾换行（与门禁的字节级读取对齐）。"""
    path = dataset_dir / FREEZE_MANIFEST_NAME
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    return path


def verify_files(dataset_dir: Path, qa_root: Path, manifest: Mapping[str, Any]) -> None:
    """核 `files_sha256` 与 `qa_code_sha256`；路径越界与哈希不符都拒绝。"""
    for base, key in ((dataset_dir, "files_sha256"), (qa_root, "qa_code_sha256")):
        declared = manifest.get(key)
        if declared is None:
            raise ValueError(f"冻结清单缺少 {key}")
        if not isinstance(declared, dict):
            raise ValueError(f"冻结清单的 {key} 不是对象")
        root = base.resolve()
        for name, expected in declared.items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"冻结文件缺失或路径越界：{name}")
            if sha256_file(path) != expected:
                raise ValueError(f"正式评测冻结文件不一致，请核对版本：{name}")


def verify_services(manifest: Mapping[str, Any], actual: Mapping[str, Mapping[str, Any]]) -> None:
    """核被测系统的模型/检索配置：只比清单点名的键，避免把无关配置也卷进来。"""
    declared = manifest.get("services")
    if declared is None:
        raise ValueError("冻结清单缺少 services")
    for key, expected in declared.items():
        for name, value in expected.items():
            if (actual.get(key) or {}).get(name) != value:
                raise ValueError("正式评测模型或检索配置已变化")


def verify_judge(manifest: Mapping[str, Any], key: str, actual: Mapping[str, Any] | None) -> None:
    """核判分器配置与请求配方（整体相等比对）。`actual` 为 None 表示本轮不含判分。"""
    if actual is None:
        return
    declared = manifest.get(key)
    if declared is None:
        raise ValueError(f"冻结清单没有记录 {key}，无法核对判分配置")
    if declared != actual:
        raise ValueError("正式评测 Judge 配置与冻结版本不一致")


__all__ = [
    "FREEZE_HISTORY_DIR",
    "FREEZE_MANIFEST_NAME",
    "file_hashes",
    "iter_code_files",
    "iter_dataset_files",
    "read_manifest",
    "sha256_bytes",
    "sha256_file",
    "verify_files",
    "verify_judge",
    "verify_services",
    "write_manifest",
]
