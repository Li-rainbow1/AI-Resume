import json


def validate_interview_response(raw: str, state: dict) -> dict:
    # 只有完整且满足业务契约的 JSON 才能推进会话；半截正文仅允许前端临时展示。
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError('面试生成结果不完整，请重试') from exc
    if not isinstance(value, dict) or not isinstance(value.get('assistantReply'), str) or not value['assistantReply'].strip():
        raise ValueError('面试回答为空或格式无效，请重试')
    if value.get('phase') not in {'opening', 'skills', 'work', 'projects', 'scenario', 'written', 'summary'} or value.get('nextAction') not in {'continue', 'finish'}:
        raise ValueError('面试阶段或下一步动作无效，请重试')
    if 'turnScore' not in value or 'finalEvaluation' not in value:
        raise ValueError('面试结果缺少评分字段，请重试')
    score = value['turnScore']
    if score is not None and (not isinstance(score, dict) or type(score.get('score')) not in {int, float}
                              or not 0 <= score['score'] <= 100 or not isinstance(score.get('comment'), str)):
        raise ValueError('面试评分格式无效，请重试')
    evaluation = value['finalEvaluation']
    finishing = state.get('command') == 'finish' or value['nextAction'] == 'finish'
    if finishing and state.get('mode') != 'interviewer' and evaluation is None:
        raise ValueError('结束面试缺少完整评价，请重试')
    if evaluation is not None:
        fields = ['projectScore', 'skillScore', 'workScore', 'educationScore']
        if (not isinstance(evaluation, dict)
            or any(type(evaluation.get(k)) not in {int, float} or not 0 <= evaluation[k] <= 100 for k in fields)
            or not isinstance(evaluation.get('summary'), str) or not evaluation['summary'].strip()
            or not isinstance(evaluation.get('improvements'), list)
            or any(not isinstance(item, str) for item in evaluation['improvements'])):
            raise ValueError('面试最终评价格式无效，请重试')
    return value
