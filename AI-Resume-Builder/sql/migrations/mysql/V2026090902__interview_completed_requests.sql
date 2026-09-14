-- 保留所有已完成请求的标识，旧请求延迟重试时拒绝重复推进会话。
ALTER TABLE interview_sessions
    ADD COLUMN completed_request_ids_json LONGTEXT NULL COMMENT '已完成回合请求标识列表';

-- 兼容上一版仅保存最近一次请求的会话，已有的完成证据继续有效。
UPDATE interview_sessions
SET completed_request_ids_json = JSON_ARRAY(last_request_id)
WHERE last_request_id IS NOT NULL AND last_request_id <> '';
