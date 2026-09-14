-- 保留原始消息，独立记录摘要覆盖范围和并发版本；旧会话版本为零。
ALTER TABLE interview_sessions
    MODIFY COLUMN memory_summary TEXT NULL COMMENT '服务端增量对话摘要',
    ADD COLUMN summary_through_seq INT NOT NULL DEFAULT 0 COMMENT '摘要已覆盖的消息序号',
    ADD COLUMN context_version BIGINT NOT NULL DEFAULT 0 COMMENT '上下文乐观锁版本',
    ADD COLUMN last_request_id VARCHAR(64) NULL COMMENT '最近一次成功回合请求 ID',
    ADD COLUMN last_response_json LONGTEXT NULL COMMENT '最近一次成功回合结果，用于重试幂等';
