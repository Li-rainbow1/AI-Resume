from dataclasses import dataclass


@dataclass(slots=True)
class ChatRequestDto:
    message: str
    sanitize_output: bool = False
    optimize_sections: bool = False


@dataclass(slots=True)
class ChatResponseDto:
    answer: str
