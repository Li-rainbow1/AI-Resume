import json
import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from docx import Document
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont


PERFORMANCE_DATA = Path(__file__).resolve().parents[4] / "testdata" / "performance"
IMAGE_COUNT = 5


def write_image_pdf(path: Path, marker: str, image_paths: list[Path]) -> None:
    # 预览与每轮实测共用排版，中文使用 PDF 标准中文字体，图片等比放置。
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.setTitle("图片解析多图性能样本")
    for index, image_path in enumerate(image_paths, start=1):
        pdf.setFont("STSong-Light", 17)
        pdf.drawString(54, 785, "图片解析性能测试固定样本")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(54, 759, f"QA marker: {marker}")
        pdf.setFont("STSong-Light", 11)
        pdf.drawString(54, 733, "以下素材用于文字识别、表格读取与流程描述的处理耗时比较。")
        pdf.drawString(54, 712, "每轮保持相同正文、图片内容和顺序，仅变更隔离标识。")
        with Image.open(image_path) as image:
            width, height = image.size
        scale = min(487 / width, 540 / height)
        drawn_width, drawn_height = width * scale, height * scale
        pdf.drawImage(ImageReader(str(image_path)), (A4[0] - drawn_width) / 2,
                      150 + (540 - drawn_height) / 2, width=drawn_width, height=drawn_height)
        pdf.setFont("STSong-Light", 10)
        pdf.drawString(54, 100, f"图片 {index} / {len(image_paths)}；每页一张独立图片。")
        pdf.drawRightString(A4[0] - 54, 55, f"第 {index} 页")
        pdf.showPage()
    if not image_paths:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(54, 780, f"QA marker: {marker}")
    pdf.save()


@dataclass(frozen=True)
class UploadAsset:
    path: Path
    content_type: str
    relative_path: str
    role: str = "document"


@dataclass(frozen=True)
class GeneratedDocument:
    kind: str
    marker: str
    file_name: str
    assets: list[UploadAsset]


class PerformanceDataFactory:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.root = Path(__file__).resolve().parents[4] / "reports" / "runtime" / "performance" / run_id / uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)

    def document(self, kind: str = "md", image_count: int = 0) -> GeneratedDocument:
        uid = uuid4().hex
        marker = f"QA_PERF_{self.run_id}_{uid}"
        image_paths = [self._image(uid, index) for index in range(image_count or (1 if kind != "md" else 0))]
        if kind == "md":
            path = self.root / f"qa-rag-{self.run_id}-{uid}.md"
            lines = ["# QA 性能测试知识文档", "", f"唯一标记：{marker}"]
            for index, image_path in enumerate(image_paths, start=1):
                lines.extend(["", f"图片 {index}", f"![QA 图片 {index}](assets/{image_path.name})"])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            assets = [UploadAsset(path, "text/markdown", path.name)]
            assets.extend(
                UploadAsset(item, mimetypes.guess_type(item.name)[0] or "application/octet-stream", f"assets/{item.name}", "attachment")
                for item in image_paths
            )
        elif kind == "pdf":
            path = self.root / f"qa-rag-{self.run_id}-{uid}.pdf"
            write_image_pdf(path, marker, image_paths)
            assets = [UploadAsset(path, "application/pdf", path.name)]
        elif kind == "docx":
            path = self.root / f"qa-rag-{self.run_id}-{uid}.docx"
            doc = Document()
            doc.add_heading("QA 性能测试知识文档", 1)
            doc.add_paragraph(f"唯一标记：{marker}")
            for index, image_path in enumerate(image_paths, start=1):
                doc.add_paragraph(f"图片序号：{index}")
                doc.add_picture(str(image_path))
            doc.save(path)
            assets = [UploadAsset(path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", path.name)]
        else:
            raise ValueError(f"不支持的文档类型：{kind}")
        return GeneratedDocument(kind, marker, path.name, assets)

    def cleanup(self) -> None:
        runtime_root = (Path(__file__).resolve().parents[4] / "reports" / "runtime" / "performance").resolve()
        target = self.root.resolve()
        if runtime_root not in target.parents or self.run_id not in target.parts:
            raise RuntimeError("本地生成数据目录不满足隔离清理规则")
        if target.exists():
            shutil.rmtree(target)
        run_root = target.parent
        if run_root.parent == runtime_root and run_root.name == self.run_id and run_root.exists() and not any(run_root.iterdir()):
            run_root.rmdir()

    def _image(self, uid: str, index: int) -> Path:
        directory = self.root / "assets"
        directory.mkdir(parents=True, exist_ok=True)
        # 使用清单中的固定图片；校验失败时拒绝静默换成占位图。
        corpus_root = PERFORMANCE_DATA / "image_parser"
        manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
        if not 0 <= index < len(manifest["images"]):
            raise ValueError(f"固定性能样本仅包含{len(manifest['images'])}张图片")
        entry = manifest["images"][index]
        source = corpus_root / entry["path"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"固定性能图片内容变化：{entry['path']}")
        suffix = source.suffix.lower()
        path = directory / f"qa-image-{self.run_id}-{uid}-{index}{suffix if suffix in {'.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.bmp'} else '.png'}"
        if path.suffix == suffix:
            shutil.copyfile(source, path)
        else:
            # PPM 等 PDF 原生图片格式交给 Pillow 转成 DOCX 可识别的 PNG，像素内容保持不变。
            with Image.open(source) as image:
                image.convert("RGB").save(path, format="PNG", optimize=False)
        return path
