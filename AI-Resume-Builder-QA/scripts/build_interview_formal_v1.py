# -*- coding: utf-8 -*-
"""生成 interview-formal-v1/cases.jsonl：30 道 normal（面试口吻改写）+ 5 道 no_evidence。"""
import json
import os

QA = r"D:/javalearn/秋招准备/项目/AI-Resume/AI-Resume-Builder-QA"
SRC = os.path.join(QA, "testdata", "quality", "interview-notes-v1", "formal.jsonl")
DST_DIR = os.path.join(QA, "testdata", "quality", "interview-formal-v1")

# 挑题映射：原 case_id -> 面试口吻问题（语义同源、措辞与检索评测刻意不同）
INTERVIEW_QUESTIONS = {
    # ---- 测试（6）----
    "INTN-F-001": "假如一个迭代快收尾了，你怎么确认产品提的每一条需求都被测试用例覆盖到了？说说你实际的做法。",
    "INTN-F-002": "UI 自动化最头疼的就是页面元素定位天天变，几十条用例跟着改。你在项目里是怎么组织自动化代码，把这种维护成本压下来的？",
    "INTN-F-016": "有的项目需求天天变、生命周期就两周，这种情况下你会不会马上铺一套大规模 UI 自动化？说说你对投入和维护的判断。",
    "INTN-F-017": "你写 Playwright 脚本时有没有遇到点了之后偶尔失败、加个固定 sleep 又时好时坏的情况？后来你是怎么把等待策略改对的？",
    "INTN-F-031": "支付回调这种场景，对方可能重试也可能并发打到两次。你怎么实现幂等？设计好之后又怎么验证它真的不会重复扣款？",
    "INTN-F-032": "压测的时候某个接口报 504、P99 也飙了，你怀疑是数据库拖慢的。从接口现象一路追到具体那条 SQL，你的排查路径是什么？",
    # ---- MySQL（6）----
    "INTN-F-004": "线上出问题需要把数据恢复到某个事务修改之前，数据库是靠什么把旧版本数据找回来的？",
    "INTN-F-005": "有同事建议把表里所有字段都加上索引，说查询快。从存储和写入的角度，你会跟他说清楚哪些代价？",
    "INTN-F-020": "EXPLAIN 的输出里看到 type=ALL、key 是 NULL，你怎么理解这个执行计划？接下来还会看哪些字段判断扫描和排序的开销？",
    "INTN-F-021": "表上建了 (name, age) 的联合索引，现在有个查询只用 name 条件但要查 age，有没有机会不用回表？怎么从执行计划上确认？",
    "INTN-F-022": "同一个事务里一条 INSERT 报了重复键错误，能直接断定前面已经执行成功的语句都自动回滚了吗？为什么？",
    "INTN-F-034": "数据库误删要恢复到误删前的时间点、已提交事务在宕机后恢复、未提交修改要回滚——这三种场景分别靠哪种日志？",
    # ---- Redis（6）----
    "INTN-F-007": "做个排行榜，每个用户存一个分数还要按分数排序，你会用 Redis 的哪种结构？member 和 score 各自能不能重复？",
    "INTN-F-008": "Redis 内存到了 maxmemory、淘汰策略是 noeviction，这时候再写一条需要额外内存的数据会发生什么？",
    "INTN-F-023": "有人拿一堆根本不存在的商品 ID 反复刷你的接口，缓存和数据库都查不到，数据库却被持续打。这是什么问题？怎么挡？",
    "INTN-F-024": "一个爆款商品的缓存刚好过期，瞬间大量并发一起回源。怎么保证不是每个请求都去打数据库？",
    "INTN-F-025": "分布式锁的场景：客户端 A 的锁过期了，B 拿到了同名锁，这时 A 执行完直接 DEL 会出什么事？正确的释放方式是什么？",
    "INTN-F-037": "Redis 重启要考虑恢复速度和数据丢失风险，RDB 和 AOF 各自的取舍是什么？AOF 的刷盘频率又是怎么影响这个取舍的？",
    # ---- 计算机网络（6）----
    "INTN-F-010": "一个网址永久搬家了，另一个只是临时跳一下，这两种重定向分别该用什么状态码？浏览器从哪里拿到跳转目标？",
    "INTN-F-011": "用 TCP 连续发两条业务消息，接收端一次 read 把两条粘到一起了。协议层应该怎么划消息边界？",
    "INTN-F-012": "主动关闭一条 TCP 连接之后，为什么还要在 TIME_WAIT 状态停留一段时间？不做会怎样？",
    "INTN-F-026": "文件下载要求一个字节都不能丢，实时语音却更在乎低延迟——这两类需求在 TCP 和 UDP 之间你会怎么选？",
    "INTN-F-027": "同一个接口，一次返回 401、一次返回 403。排查的时候这两者的侧重点有什么不一样？",
    "INTN-F-028": "登录状态要跨页面保持，Cookie 和 Session 分别存在哪一侧？服务端的会话什么时候会失效？",
    # ---- AI（6）----
    "INTN-F-013": "给 AI 助手挂的历史记录和工具结果越滚越长，上下文工程要对这些信息做什么处理？",
    "INTN-F-014": "给助手装了很多 Skill 之后，为什么不必一开始就把每份说明的全文都塞进上下文？Skill 是怎么按需加载的？",
    "INTN-F-015": "用 LoRA 让一个已有的模型适配某个具体任务，训练的是什么参数？为什么这样能把训练成本降下来？",
    "INTN-F-029": "接入 MCP 之后，是不是随便什么模型都能直接调工具、应用层就再也不用适配各家模型接口了？说说你的理解。",
    "INTN-F-030": "模型已经能生成工具调用的参数了，是不是就能让它直接执行删库这类操作？重点要防哪些风险？",
    "INTN-F-043": "知识库刚更新了一条规则，你希望助手回答时用的是最新资料。从资料入库到最终回答，中间有哪些关键步骤？资料不够的时候应该怎么约束回答？",
}

# B 组：语料（测试/MySQL/Redis/计算机网络/AI 五篇笔记）不覆盖的主题
NO_EVIDENCE = [
    ("INTV-B-001", "Kubernetes 里一个 Pod 被调度到某个节点的过程是怎样的？节点资源不足时调度器会怎么处理？"),
    ("INTV-B-002", "前端框架的虚拟 DOM diff 算法大致是怎么工作的？key 在里面起什么作用？"),
    ("INTV-B-003", "消息队列要保证同一业务键的消息按顺序消费，你会怎么设计？消费端失败重试又怎么不影响顺序？"),
    ("INTV-B-004", "OAuth2 授权码模式的完整流程走一遍：为什么要有 code 换 token 这一步，而不是直接返回 token？"),
    ("INTV-B-005", "让你从零搭一条 CI/CD 流水线，从代码提交到生产发布，你会划分哪些阶段？每个阶段的门禁怎么设？"),
]


def main() -> None:
    rows = [json.loads(line) for line in open(SRC, encoding="utf-8")]
    by_id = {row["case_id"]: row for row in rows}
    missing = [cid for cid in INTERVIEW_QUESTIONS if cid not in by_id]
    assert not missing, f"映射表引用了不存在的题目: {missing}"

    cases = []
    seq = 0
    topic_seen: dict[str, int] = {}
    for src_id, question in INTERVIEW_QUESTIONS.items():
        src = by_id[src_id]
        assert len(question) <= 240, f"{src_id} 面试问题超 240 字: {len(question)}"
        seq += 1
        topic = src["topic"]
        topic_seen[topic] = topic_seen.get(topic, 0) + 1
        cases.append({
            "schema_version": "interview-formal-v1",
            "case_id": f"INTV-F-{seq:03d}",
            "group": "normal",
            "topic": topic,
            "question_type": src.get("question_type", "text"),
            "request": {
                "mode": "interviewer",
                "command": "continue",
                "userInput": question,
                "sessionId": "",
                "history": [],
                "resumeSnapshot": {},
                "durationMinutes": 60,
                "elapsedSeconds": 0,
            },
            "reference_answer": src.get("reference_answer") or "",
            "reference_source": f"{','.join(src.get('expected_documents') or [])}（依据行号见 notes-v1 evidence_annotations {src_id}）",
            "source_case_id": src_id,
            "should_refuse": False,
        })
    for case_id, question in NO_EVIDENCE:
        seq += 1
        assert len(question) <= 240
        cases.append({
            "schema_version": "interview-formal-v1",
            "case_id": case_id,
            "group": "no_evidence",
            "topic": "语料外",
            "question_type": "text",
            "request": {
                "mode": "interviewer",
                "command": "continue",
                "userInput": question,
                "sessionId": "",
                "history": [],
                "resumeSnapshot": {},
                "durationMinutes": 60,
                "elapsedSeconds": 0,
            },
            "reference_answer": "",
            "reference_source": "语料无覆盖，期望承认没把握而非编造",
            "source_case_id": "",
            "should_refuse": True,
        })

    out = os.path.join(DST_DIR, "cases.jsonl")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    print("写入:", out)
    print("总题数:", len(cases), "| 主题分布:", topic_seen, "| B 组:", len(NO_EVIDENCE))


if __name__ == "__main__":
    main()
