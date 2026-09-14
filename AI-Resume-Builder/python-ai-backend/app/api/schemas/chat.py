from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    sanitizeOutput: bool = False
    optimizeSections: bool = False


class ChatResponse(BaseModel):
    answer: str

