"""旧集（`evidence-v3`）的语料工厂。

只服务单主文档那一代数据集：一篇带图主文档 + 三张附件、外加三篇纯文本干扰文档。
主文档与干扰文档分两批上传，便于分别登记与清理。

新集（多篇正文、附件按 Markdown 相对路径各挂各的正文）的编排在
`quality/corpus.py::build_notes_corpus`；两者的产物是同一个 `QualityCorpus`。
"""

from pathlib import Path
from uuid import uuid4

from PIL import Image, PngImagePlugin

from clients.rag import UploadAsset
from quality.models import QualityCorpus


# 干扰文档素材：纯文本、不带附件，用于让检索侧指标具备区分度。
_NOISE_SOURCES = ("noise-alpha.md", "noise-beta.md", "legacy-archive.md")

_IMAGE_NAMES = ("quality-ocr-badge.png", "quality-capacity-table.png", "quality-review-flow.png")

# 主文档与干扰文档共用的运行时文件名片段；干扰文档一律避开 `quality-corpus.md`
# 后缀，否则会被 `endswith` 式的预期文档匹配误判成主文档。
_PRIMARY_SUFFIX = "quality-corpus.md"


class QualityAssetFactory:
    """旧集的语料工厂；素材从 `testdata/quality/corpus` 逐字节复制。"""

    def __init__(self, root: Path, run_id: str, source_root: Path | None = None) -> None:
        self.root = root
        self.run_id = run_id
        self.source_root = source_root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> QualityCorpus:
        uid = uuid4().hex
        asset_dir = self.root / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        source_root = self.source_root or Path(__file__).resolve().parents[1] / "testdata" / "quality" / "corpus"
        # 图片只重写 PNG 元数据（本轮 run_id），像素与源素材一致，避免系统字体差异
        # 改变评测素材。
        image_paths: list[Path] = []
        for image_name in _IMAGE_NAMES:
            target = asset_dir / image_name
            self._copy_image(source_root / "assets" / image_name, target)
            image_paths.append(target)
        file_name = f"qa-rag-{self.run_id}-{uid}-{_PRIMARY_SUFFIX}"
        markdown = self.root / file_name
        # 人工可查阅的正文与真实评测使用同一份素材，避免两份事实漂移。
        self._copy_source(source_root / _PRIMARY_SUFFIX, markdown)

        noise_file_names: list[str] = []
        noise_assets: list[UploadAsset] = []
        for source_name in _NOISE_SOURCES:
            noise_path = self.root / f"qa-rag-{self.run_id}-{uid}-{source_name}"
            self._copy_source(source_root / source_name, noise_path)
            noise_file_names.append(noise_path.name)
            noise_assets.append(UploadAsset(noise_path, "text/markdown", noise_path.name))

        return QualityCorpus(
            primary_assets=[
                UploadAsset(markdown, "text/markdown", markdown.name),
                *(
                    UploadAsset(path, "image/png", f"assets/{path.name}", role="attachment")
                    for path in image_paths
                ),
            ],
            noise_assets=noise_assets,
            document_file_names=[file_name],
            noise_file_names=noise_file_names,
        )

    @staticmethod
    def _copy_source(source: Path, target: Path) -> None:
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _copy_image(self, source: Path, target: Path) -> None:
        with Image.open(source) as image:
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("QA-Run-ID", self.run_id)
            image.save(target, format="PNG", pnginfo=metadata)
