class AuthError(RuntimeError):
    """认证链路可预期的业务异常。"""


class AuthValidationError(AuthError):
    pass


class AuthUnauthorizedError(AuthError):
    pass


class AuthForbiddenError(AuthError):
    pass


class AuthConflictError(AuthError):
    pass


class AuthRateLimitError(AuthError):
    pass


class AuthServiceUnavailableError(AuthError):
    pass


class AuthStorageError(AuthError):
    pass


class SystemConfigValidationError(AuthValidationError):
    """系统服务配置的字段或业务校验失败。"""


class SystemConfigConflictError(AuthConflictError):
    """系统服务配置版本已被其他管理员更新。"""


class SystemConfigRepositoryError(AuthStorageError):
    """系统服务配置持久化层不可用。"""


class SystemConfigEncryptionError(AuthStorageError):
    """系统服务密钥无法使用当前加密根密钥解密。"""


class SystemConfigUpstreamError(AuthServiceUnavailableError):
    """系统服务连接测试或保存前验证失败。"""


class AuthUserAlreadyExistsError(AuthConflictError):
    pass


class AuthVerificationWriteConflictError(AuthRateLimitError):
    pass
