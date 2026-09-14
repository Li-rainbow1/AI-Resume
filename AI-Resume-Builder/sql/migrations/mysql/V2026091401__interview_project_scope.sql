-- 空数组代表自动识别，历史会话继续使用自动范围。
ALTER TABLE interview_sessions
    ADD COLUMN project_ids_json JSON NULL COMMENT '手动选择的知识库项目 ID 列表';
