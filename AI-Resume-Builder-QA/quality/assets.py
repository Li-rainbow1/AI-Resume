from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from clients.rag import UploadAsset


@dataclass(frozen=True)
class QualityCorpus:
    assets: list[UploadAsset]
    expected_file_name: str


class QualityAssetFactory:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> QualityCorpus:
        uid = uuid4().hex
        asset_dir = self.root / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        source_root = Path(__file__).resolve().parents[1] / "testdata" / "quality" / "corpus"
        image_names = ("quality-ocr-badge.png", "quality-capacity-table.png", "quality-review-flow.png")
        # 固定图片像素，运行时只写入本轮元数据，避免系统字体差异改变评测素材。
        for image_name in image_names:
            with Image.open(source_root / "assets" / image_name) as image:
                self._save(image, asset_dir / image_name)
        badge, table, flow = (asset_dir / image_name for image_name in image_names)
        file_name = f"qa-rag-{self.run_id}-{uid}-quality-corpus.md"
        markdown = self.root / file_name
        # 人工可查阅的正文与真实评测使用同一份素材，避免两份事实漂移。
        source = source_root / "quality-corpus.md"
        markdown.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return QualityCorpus(
            assets=[
                UploadAsset(markdown, "text/markdown", markdown.name),
                UploadAsset(badge, "image/png", "assets/quality-ocr-badge.png", role="attachment"),
                UploadAsset(table, "image/png", "assets/quality-capacity-table.png", role="attachment"),
                UploadAsset(flow, "image/png", "assets/quality-review-flow.png", role="attachment"),
            ],
            expected_file_name=file_name,
        )

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
        except OSError:
            return ImageFont.load_default()

    def _draw_text_image(self, path: Path, lines: list[str]) -> Path:
        image = Image.new("RGB", (1000, 420), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((18, 18, 982, 402), outline="black", width=4)
        font = self._font(42)
        for index, line in enumerate(lines):
            draw.text((70, 80 + index * 95), line, fill="black", font=font)
        self._save(image, path)
        return path

    def _draw_table(self, path: Path) -> Path:
        image = Image.new("RGB", (1000, 500), "white")
        draw = ImageDraw.Draw(image)
        font = self._font(34)
        rows = [("QUEUE", "LIMIT", "SLA"), ("ALPHA", "12", "2 DAYS"), ("BETA", "18", "4 DAYS"), ("GAMMA", "9", "1 DAY")]
        x_values = (40, 430, 700, 960)
        y_values = (40, 140, 240, 340, 440)
        for x in x_values:
            draw.line((x, y_values[0], x, y_values[-1]), fill="black", width=3)
        for y in y_values:
            draw.line((x_values[0], y, x_values[-1], y), fill="black", width=3)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                draw.text((x_values[column_index] + 25, y_values[row_index] + 25), value, fill="black", font=font)
        self._save(image, path)
        return path

    def _draw_flow(self, path: Path) -> Path:
        image = Image.new("RGB", (1200, 520), "white")
        draw = ImageDraw.Draw(image)
        font = self._font(31)
        nodes = [(40, 100, "INTAKE"), (320, 100, "REVIEW"), (600, 100, "APPROVE"), (880, 100, "ARCHIVE")]
        for x, y, label in nodes:
            draw.rounded_rectangle((x, y, x + 220, y + 100), radius=14, outline="black", width=4)
            draw.text((x + 35, y + 30), label, fill="black", font=font)
        for x in (260, 540, 820):
            draw.line((x, 150, x + 60, 150), fill="black", width=4)
            draw.polygon(((x + 60, 150), (x + 42, 138), (x + 42, 162)), fill="black")
        draw.rounded_rectangle((320, 340, 540, 440), radius=14, outline="black", width=4)
        draw.text((365, 370), "REVISE", fill="black", font=font)
        draw.line((430, 200, 430, 340), fill="black", width=4)
        draw.text((450, 250), "REJECT", fill="black", font=self._font(24))
        self._save(image, path)
        return path

    def _save(self, image: Image.Image, path: Path) -> None:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("QA-Run-ID", self.run_id)
        image.save(path, format="PNG", pnginfo=metadata)
