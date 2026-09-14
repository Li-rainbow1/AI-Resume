"""图片增强独立 Worker 入口。"""

from app.application.services.rag_image_enrichment_worker import run_rag_image_enrichment_worker


if __name__ == "__main__":
    run_rag_image_enrichment_worker()
