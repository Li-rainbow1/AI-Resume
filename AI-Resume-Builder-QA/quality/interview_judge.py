"""面试链路的 Judge 兼容入口。

原先是这里的独立实现（自己建 `OpenAI` 客户端 + 自己拼 JSON 指令）。现在三条链路
共用一份实现，本文件退化为别名，只保留类名与文件位置，避免改 `interview_runner`
的 import 面。

实现位置：`quality/deepeval_judge.py`（配方在 `quality/judge.py`）。
"""

from quality.deepeval_judge import DeepEvalJudgeLLM

# 同一个类，不是子类：任何一侧的行为变化都必须同时体现在另一侧。
InterviewJudge = DeepEvalJudgeLLM

__all__ = ["InterviewJudge"]
