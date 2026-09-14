from typing import Protocol


class EmbeddingPort(Protocol):
    # 向量化抽象。
    # 上层只关心“给一批文本，拿回一批向量”，不关心具体模型厂商。
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
