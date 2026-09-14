import re


class MarkdownStructureNormalizer:
    def normalize(self, content: str) -> str:
        # OCR 输出经常有多余空格、断裂列表项、过多空行。
        # 这里做轻量清洗，尽量不改变原意，只修正结构噪声。
        text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(?m)^([*-])([^\s])", r"\1 \2", text)
        text = re.sub(r"(?m)^(\d+\.)([^\s])", r"\1 \2", text)
        return text.strip()
