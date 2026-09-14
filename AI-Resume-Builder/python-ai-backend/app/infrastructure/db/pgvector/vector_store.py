"""保留旧导入路径，具体实现位于 persistence/pgvector。"""

from app.infrastructure.persistence.pgvector.sqlalchemy_vector_store import PgVectorStoreAdapter

__all__ = ["PgVectorStoreAdapter"]
