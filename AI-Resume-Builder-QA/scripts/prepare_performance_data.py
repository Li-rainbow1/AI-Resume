# author: jf
"""仅在本地制作固定性能素材，不启动服务、不上传、不调用模型。"""

import hashlib
import json
import shutil
import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from performance.locustfiles.common.data_factory import IMAGE_COUNT, write_image_pdf


QA_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = QA_ROOT / "testdata" / "performance"


DEFAULT_SOURCE_PDF = Path.home() / "Desktop" / "Omni_Contextual_Aggregation_Networks_for_High-Fidelity_Image_Inpainting.pdf"


def _extract_real_images(source_pdf: Path, assets: Path) -> list[dict]:
    pdfimages = shutil.which("pdfimages")
    if not pdfimages:
        raise RuntimeError("未找到 Poppler 的 pdfimages，无法从真实 PDF 提取内嵌图片")
    selected_pages = (4, 5, 6, 9)
    entries: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="qa-pdfimages-") as temp_dir:
        temp_root = Path(temp_dir)
        for page_number in selected_pages:
            prefix = temp_root / f"page-{page_number:02d}"
            completed = subprocess.run(
                [pdfimages, "-j", "-f", str(page_number), "-l", str(page_number), str(source_pdf), str(prefix)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"提取 PDF 第 {page_number} 页图片失败：{completed.stderr.strip()}")
            extracted = sorted(
                path for path in prefix.parent.glob(f"{prefix.name}-*")
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".ppm"}
            )
            for image_index, source in enumerate(extracted, start=1):
                suffix = source.suffix.lower()
                target = assets / f"source-page-{page_number:02d}-image-{image_index:02d}{suffix}"
                shutil.copyfile(source, target)
                with Image.open(target) as preview:
                    width, height = preview.size
                data = target.read_bytes()
                entries.append(
                    {
                        "path": f"assets/{target.name}",
                        "kind": "real_embedded_image",
                        "sourcePage": page_number,
                        "sourceObject": source.name,
                        "width": width,
                        "height": height,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    if len(entries) != 6:
        raise RuntimeError(f"真实 PDF 指定页应提取 6 张图片，实际得到 {len(entries)} 张")
    if len({entry["sha256"] for entry in entries}) != 6:
        raise RuntimeError("真实 PDF 提取出的六张图片存在重复内容")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="准备图片解析性能测试的真实图片素材")
    parser.add_argument("--source-pdf", type=Path, default=DEFAULT_SOURCE_PDF)
    parser.add_argument("--replace", action="store_true", help="替换已有的固定性能素材")
    parser.add_argument("--reuse-assets", action="store_true", help="复用已固定图片，生成五页 PDF，保留原素材")
    args = parser.parse_args()
    source_pdf = args.source_pdf.resolve()
    if not args.reuse_assets and not source_pdf.is_file():
        raise SystemExit(f"找不到真实源 PDF：{source_pdf}")
    root = DATA_ROOT / "image_parser"
    if root.exists() and not args.replace and not args.reuse_assets:
        raise SystemExit("固定素材目录已存在，拒绝覆盖；请保留已有版本并另行规划更新")
    if root.exists() and not args.reuse_assets:
        for target in (root / "assets", root / "manifest.json"):
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    previous = json.loads((root / "manifest.json").read_text(encoding="utf-8")) if args.reuse_assets else None
    entries = (previous["images"] if previous else _extract_real_images(source_pdf, assets))[:IMAGE_COUNT]
    if len(entries) != IMAGE_COUNT:
        raise RuntimeError("固定图片数量不足")
    for entry in entries:
        if hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("固定素材摘要不匹配")
    pdf_path = root / "five-images.pdf"
    write_image_pdf(pdf_path, "QA_PERF_real_source_preview", [root / entry["path"] for entry in entries])
    manifest = {
        "author": "jf", "version": "performance-image-real-v2-five-pages", "imageCount": IMAGE_COUNT, "pageCount": IMAGE_COUNT,
        "sourcePdf": previous["sourcePdf"] if previous else {
            "fileName": source_pdf.name,
            "sha256": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
            "selectedPages": [4, 5, 6, 9],
        },
        "images": entries,
        "pdf": {"path": pdf_path.name, "bytes": pdf_path.stat().st_size, "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest()},
        "status": "已固定真实 PDF 内嵌图片，未执行性能测试",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if pdf_path.stat().st_size > 10 * 1024 * 1024:
        raise RuntimeError("五页 PDF 仍超过 10 MiB，不能用于当前上传测试")
    print(f"已准备五图 PDF：{pdf_path.stat().st_size} 字节；未调用模型")


if __name__ == "__main__":
    main()
