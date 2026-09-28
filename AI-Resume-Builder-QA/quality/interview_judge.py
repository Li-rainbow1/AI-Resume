"""面试链路的 Judge 兼容入口。

原先是这里的独立实现（自己建 `OpenAI` 客户端 + 自己拼 JSON 指令）。现在三条链路
共用一份实现，本文件退化为别名，只保留类名与文件位置，避免改 `interview_runner`
的 import 面。

实现位置：`quality/deepeval_judge.py`（配方在 `quality/judge.py`）。
"""

from quality.deepeval_judge import DeepEvalJudgeLLM

# 同一个类，不是子类：任何一侧的行为变化都必须同时体现在另一侧。
InterviewJudge = DeepEvalJudgeLLM


class EvidenceFaithfulnessJudge(DeepEvalJudgeLLM):
    """仅忠实度追加来源约束；回答相关性继续使用原适配器。"""

    def _system_prompt(self, schema):
        from deepeval.metrics.faithfulness.schema import Claims, Truths, Verdicts

        system = super()._system_prompt(schema)
        # 按实际响应类型区分阶段，避免证据约束污染待评回答的声明提取。
        if schema is Claims:
            return system + (
                "当前仅执行回答声明提取，不判断正确性、来源可靠性或证据是否充分。"
                "仅提取 actual_output 实际表达的主张，尽量沿用原文，并拆成单独可核对的事实。"
                "保留否定词、条件、数字、范围和不确定程度；即使原回答有错也不得纠正。"
                "不得补充回答中没有的知识、评价或‘缺少可靠来源’等免责声明。"
                "技术主张直接表述，不额外加‘AI声称’‘根据AI输出’等归属前缀。"
                "原文明确涉及个人经历或引用他人时，保留原有归属，不改写为普遍事实。"
                "输出前逐项对照回答原文，删除新增内容，恢复被改动的原意。"
            )
        if schema is Truths:
            return system + (
                "当前仅提取给定上下文中的事实依据，不补充外部知识。"
                "保留与判断有关的条件、否定、列表和代码示例含义，避免遗漏明确给出的细节。"
                "按上下文标签区分来源：检索资料可支持文档事实；简历仅支持个人自述；"
                "当前问题、历史用户技术说法、历史AI输出和摘要不能独立证明技术事实。"
                "问题中的待核查内容不得改写成已确认结论。"
            )
        if schema is Verdicts:
            return system + (
                "当前核对每条声明的内容是否得到给定事实依据支持。"
                "待评回答不需要再次出现在检索资料里；不要把核对技术内容变成核对‘AI是否说过’。"
                "按来源标签判断：简历仅支持个人自述；用户技术断言、历史AI输出及摘要"
                "不能独立证明技术事实。允许同义表达及多条依据共同支持，保留条件和否定。"
                "支持为yes，明确矛盾为no，缺少依据为idk；资料没提到不等于明确矛盾。"
                "不得纠正、补写或替换待评声明；每条声明按原顺序恰好给出一个判定。"
            )
        return system

__all__ = ["InterviewJudge", "EvidenceFaithfulnessJudge"]
