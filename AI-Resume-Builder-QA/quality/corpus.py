"""按 `schema_version` 分发「语料怎么入库」。

加载器（`quality/loaders.py`）解决「题目怎么读」，这里解决它之后的另一半：数据集声明的
素材怎么变成一轮可上传的语料。两件事分开是因为变化频率不同——加题目不影响上传编排，
换数据集却往往要换上传方式。

历史背景：`runner.py` 原先写死 `_require_supported_corpus`，只认 `evidence-v3` 的
「一篇主文档 + 三张附件」，新数据集（5 篇正文 + 64 张附件、附件按 Markdown 相对路径挂
到各自正文）根本进不来。现在编排与 schema 一起分发，未登记的数据集在**上传之前**报错。

新增一个数据集 = `quality/loaders.py` 加一个加载器 + 这里加一个语料工厂 + 一行注册。
"""

import hashlib
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from clients.rag import UploadAsset
from quality.models import LEGACY_SCHEMA_VERSION, CorpusAsset, QualityCorpus

if TYPE_CHECKING:
    from quality.loaders import CaseSet

CorpusFactory = Callable[["CaseSet", Path, str], QualityCorpus]

_FACTORIES: dict[str, CorpusFactory] = {}

_NOTES_SCHEMA_VERSION = "interview-notes-v1"

# 未引用附件的**已知例外**（见 `_expected_unreferenced`）。
_NOTES_UNREFERENCED_ATTACHMENTS = ("附件/Pasted image 20260818090016.png",)

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def register_corpus_factory(schema_version: str) -> Callable[[CorpusFactory], CorpusFactory]:
    def decorator(factory: CorpusFactory) -> CorpusFactory:
        _FACTORIES[schema_version] = factory
        return factory

    return decorator


def supported_corpus_schemas() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def corpus_for(case_set: "CaseSet", temp_root: Path, run_id: str) -> QualityCorpus:
    """按数据集的 schema_version 生成这一轮要上传的语料。

    未登记的 schema 在**上传前**拒绝：语料一旦按别的形状传进知识库，清理和重跑都很贵。
    """
    if case_set.schema_version not in _FACTORIES:
        raise ValueError(
            f"schema_version={case_set.schema_version} 的语料编排尚未接线；"
            f"已支持：{', '.join(supported_corpus_schemas()) or '（空）'}。"
            "离线加载与判分不受影响。"
        )
    return _FACTORIES[case_set.schema_version](case_set, temp_root, run_id)


def expected_unreferenced_for(case_set: "CaseSet") -> tuple[str, ...]:
    """数据集声明「已上传但正文没引用」的附件；未登记的数据集按「不允许有」处理。"""
    return _expected_unreferenced(case_set) if case_set.schema_version == _NOTES_SCHEMA_VERSION else ()


@register_corpus_factory(LEGACY_SCHEMA_VERSION)
def build_legacy_corpus(case_set: "CaseSet", temp_root: Path, run_id: str) -> QualityCorpus:
    """旧集：单主文档 + 三张附件 + 三份纯文本干扰文档。

    素材不是从数据集目录读的（旧集语料已不在本机），而是由工厂现场生成，因此
    `case_set.assets` 在这里不参与。
    """
    from quality.assets import QualityAssetFactory

    return QualityAssetFactory(temp_root, run_id).create()


@register_corpus_factory(_NOTES_SCHEMA_VERSION)
def build_notes_corpus(case_set: "CaseSet", temp_root: Path, run_id: str) -> QualityCorpus:
    """面试八股集：5 篇正文 + 各自的附件，同批上传。

    三条约束都不允许「顺手优化」：

    1. **正文与附件同批上传**，且附件的 `relativePath` 必须等于正文里写的 Markdown
       相对路径。后端靠它把附件关联到正文；分批上传或改路径都会让图片静默挂不上，
       图片题直接变成无解。
    2. **正文逐字节复制**。数据集声明的 `sha256` 是「证据行号可信」的前提，任何改写
       （包括把 Obsidian 的 `![[...]]` 转成常规链接）都会让行号与冻结哈希失配。
    3. **未引用附件按数据集点名**（`_expected_unreferenced`），不静默容忍。
    """
    documents = [asset for asset in case_set.assets if asset.kind == "document"]
    attachments = [asset for asset in case_set.assets if asset.kind != "document"]
    if not documents:
        raise ValueError(f"{case_set.schema_version} 数据集没有正文素材：{case_set.dataset_dir}")
    corpus_root = _corpus_root(case_set.assets)

    asset_dir = temp_root / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    primary: list[UploadAsset] = []
    document_file_names: list[str] = []
    for asset in documents:
        # 正文必须保留原名后缀：数据集用 `endswith("1-测试.md")` 匹配预期文档，
        # 所以只在前面加 run 前缀，不动名字本身。
        target = temp_root / f"qa-rag-{run_id}-{Path(asset.relative_path).name}"
        _copy_verified(case_set.dataset_dir, asset, target)
        document_file_names.append(target.name)
        primary.append(UploadAsset(target, "text/markdown", target.name))

    for asset in attachments:
        source = Path(asset.relative_path)
        target = asset_dir / source.name
        _copy_verified(case_set.dataset_dir, asset, target)
        primary.append(
            UploadAsset(
                target,
                _content_type_for(source),
                _markdown_relative(asset.relative_path, corpus_root),
                role="attachment",
            )
        )

    return QualityCorpus(
        primary_assets=primary,
        noise_assets=[],
        document_file_names=document_file_names,
        noise_file_names=[],
        expected_unreferenced_attachments=_expected_unreferenced(case_set),
    )


def _corpus_root(assets: tuple[CorpusAsset, ...]) -> str:
    """数据集里所有素材共享的那一层前缀目录名（本集是 `corpus`）。

    正文里的 Markdown 图片引用是**相对正文**的，所以上传时必须把这一层剥掉。要求所有
    素材同属一层前缀，否则「相对正文的路径」根本无定义。
    """
    roots = {PurePosixPath(asset.relative_path).parts[0] for asset in assets}
    if len(roots) != 1:
        raise ValueError(f"素材不在同一个语料根目录下，无法推导 Markdown 相对路径：{sorted(roots)}")
    return roots.pop()


def _markdown_relative(declared: str, corpus_root: str) -> str:
    """把数据集相对路径转成正文里的 Markdown 相对路径。"""
    parts = PurePosixPath(declared).parts
    if not parts or parts[0] != corpus_root:
        raise ValueError(f"附件路径不在语料根目录 {corpus_root} 下：{declared}")
    relative = "/".join(parts[1:])
    if not relative:
        raise ValueError(f"附件路径无法转成相对正文的引用：{declared}")
    return relative


def _copy_verified(dataset_dir: Path, asset: CorpusAsset, target: Path) -> None:
    """逐字节复制并核对数据集声明的哈希；正文被改写会让证据行号失效。"""
    source = dataset_dir / asset.relative_path
    if not source.is_file():
        raise ValueError(f"语料文件缺失：{asset.relative_path}")
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != asset.sha256:
        raise ValueError(f"语料已变化，需重新核对证据：{asset.relative_path}")
    target.write_bytes(payload)


def _content_type_for(path: Path) -> str:
    return _IMAGE_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _expected_unreferenced(case_set: "CaseSet") -> tuple[str, ...]:
    """数据集里已上传、但正文没有任何 Markdown 引用指向的附件。

    目前只有一张：`6-计算机网络.md` 第 74 行用的是 Obsidian 的 `![[附件/...]]` 写法，
    后端按 CommonMark 解析（只认 `![](path)`、引用式与快捷式，快捷式还要靠引用定义
    才能解析出目标），它不会产生任何引用路径 ⇒ 那张图不会被关联、也不会进入检索。

    **不需要转换**：数据集里没有任何答案单元引用它（8 张图片证据全部指向别的附件），
    转换它只会让正文偏离冻结哈希，去换一张不参与评分的图。这里把它列为显式已知例外；
    一旦它不再是数据集素材，说明例外已经失效，必须回来重算。
    """
    declared = [PurePosixPath(asset.relative_path).as_posix() for asset in case_set.assets]
    for relative in _NOTES_UNREFERENCED_ATTACHMENTS:
        if not any(path.endswith(f"/{relative}") for path in declared):
            raise ValueError(f"未引用附件已不在数据集素材里，需重新核对例外：{relative}")
    return _NOTES_UNREFERENCED_ATTACHMENTS


__all__ = [
    "corpus_for",
    "expected_unreferenced_for",
    "register_corpus_factory",
    "supported_corpus_schemas",
]
