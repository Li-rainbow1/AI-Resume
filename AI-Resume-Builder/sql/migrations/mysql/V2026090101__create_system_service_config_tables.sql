-- 创建管理员系统服务配置、全局修订号和脱敏审计表。

CREATE TABLE IF NOT EXISTS system_service_configs (
    service_key              VARCHAR(32)  NOT NULL COMMENT '服务标识：embedding/chat/vision/realtime/rag/smtp',
    public_config_json       JSON         NOT NULL COMMENT '不含密钥的公开配置',
    encrypted_secrets        TEXT         NOT NULL COMMENT 'AES-GCM 加密后的密钥 JSON',
    version                  BIGINT       NOT NULL DEFAULT 1 COMMENT '服务配置版本',
    last_validation_status   VARCHAR(24)  NOT NULL DEFAULT 'unknown' COMMENT '最近校验状态',
    last_validation_message  VARCHAR(512) NOT NULL DEFAULT '' COMMENT '最近校验脱敏提示',
    last_validated_at        DATETIME(6)  NULL COMMENT '最近校验时间',
    updated_by               VARCHAR(64)  NOT NULL COMMENT '最近修改管理员 ID',
    created_at               DATETIME(6)  NOT NULL COMMENT '创建时间',
    updated_at               DATETIME(6)  NOT NULL COMMENT '更新时间',
    PRIMARY KEY (service_key),
    CONSTRAINT chk_system_service_config_version CHECK (version > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='管理员系统服务配置';

CREATE TABLE IF NOT EXISTS system_config_revisions (
    revision_id  INT        NOT NULL COMMENT '固定单例 ID，始终为 1',
    revision     BIGINT     NOT NULL DEFAULT 0 COMMENT '全局配置修订号',
    updated_at   DATETIME(6) NOT NULL COMMENT '更新时间',
    PRIMARY KEY (revision_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='系统服务配置全局修订号';

INSERT INTO system_config_revisions (revision_id, revision, updated_at)
VALUES (1, 0, CURRENT_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE revision_id = revision_id;

CREATE TABLE IF NOT EXISTS system_service_config_audits (
    audit_id        BIGINT       NOT NULL AUTO_INCREMENT COMMENT '审计记录 ID',
    service_key     VARCHAR(32)  NOT NULL COMMENT '服务标识',
    action          VARCHAR(32)  NOT NULL COMMENT 'save / restore-default',
    result          VARCHAR(24)  NOT NULL COMMENT 'success / failure',
    actor_user_id   VARCHAR(64)  NOT NULL COMMENT '操作者用户 ID',
    config_version  BIGINT       NOT NULL DEFAULT 0 COMMENT '操作涉及的配置版本',
    detail          VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '脱敏操作说明，禁止写入密钥',
    created_at      DATETIME(6)  NOT NULL COMMENT '创建时间',
    PRIMARY KEY (audit_id),
    KEY idx_system_service_config_audits_service_time (service_key, created_at),
    KEY idx_system_service_config_audits_actor_time (actor_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='系统服务配置脱敏审计';
