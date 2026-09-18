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

# 未引用附件的**已知例外**（见 `_expected_unreferenced`）。本集是空集：冻结副本在制作时
# 已把 `6-计算机网络.md` 第 74 行的 Obsidian `![[...]]` 归一化成常规 Markdown 链接
# （图片名含空格，故空格编码为 `%20`），64 张附件全部有正文归属。声明保留为空，是为了
# 让「附件都必须有归属」这条门禁继续生效。
#
# ⚠️ 归一化后的链接**必须不含裸空格**：后端 `_IMAGE_PATTERN` 用 `[^\s)\r\n]+` 捕获路径，
# `](` 之后遇空格即断，`![](附件/Pasted image ….png)` 会被静默丢弃（正文有图、后端零引用）。
# 初版就是漏了这一步，导致检索评测卡在下面的「未引用附件数 ≠ 声明数」门禁。
_NOTES_UNREFERENCED_ATTACHMENTS: tuple[str, ...] = ()

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
    r"""面试八股集：5 篇正文 + 各自的附件，同批上传。

    三条约束都不允许「顺手优化」：

    1. **正文与附件同批上传**，且附件的 `relativePath` 必须等于正文里写的 Markdown
       相对路径。后端靠它把附件关联到正文；分批上传或改路径都会让图片静默挂不上，
       图片题直接变成无解。
    2. **正文按数据集声明的 `sha256` 逐字节复制**。哈希是「证据行号可信」的前提，落盘
       后必须与清单一致（`_copy_verified` 逐文件核对）。唯一一次有意的改写（Obsidian
       `![[...]]` → 常规链接，并把文件名里的空格编码成 `%20`——不编码会被后端的
       `[^\s)\r\n]+` 截断）发生在**冻结生成时**并已记入 `corpus_manifest.json` 的
       `transformations`（含 before/after 哈希），行数不变，因此证据行号依旧成立。
       运行期不许再改正文——临时副本里的任何改动都会绕开清单校验。
    3. **未引用附件按数据集点名**（`_expected_unreferenced`）。本集声明为空集：归一化后
       64 张附件全部有正文归属，多一张少一张都算语料漂移。
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
    r"""数据集里已上传、但正文没有任何 Markdown 引用指向的附件。

    `interview-notes-v1` 声明的是**空集**。原先把 `6-计算机网络.md` 第 74 行的 Obsidian
    `![[附件/...]]` 列为已知例外（后端只认 `![](path)`、引用式与快捷式，不认 `![[...]]`），
    但那份写法会让该图既挂不上正文、又在知识库里留成无归属附件；核对过全部 8 个图片证据
    单元、确认无题依赖它之后，改为**在冻结生成时**把它归一化成 `![](附件/Pasted%20image
    %2020260818090016.png)`，并写入 `corpus_manifest.json` 的 `transformations` 留痕。
    注意路径必须是 `%20` 编码或尖括号包裹——图片名本身含空格，裸空格会被后端
    `[^\s)\r\n]+` 截断、解析不到（2026-09-17 修的正是这个）。于是 64 张附件全部有正文归属。

    这里保留声明位与校验：一旦某个数据集确实存在挂不上的附件，必须点名登记；点名项若从
    素材里消失则报错——「声明的例外已经失效」同样要有人来重算。
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
