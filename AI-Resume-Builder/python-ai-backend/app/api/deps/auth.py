from functools import lru_cache

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.dto.auth_dto import AuthUserContext
from app.application.services.auth_service import AuthService
from app.bootstrap.container import build_auth_service


# 使用 FastAPI 标准 Bearer 安全方案，让 OpenAPI/Swagger 能正确注入 Authorization 请求头。
_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    # 认证服务必须进程内复用，确保登录公钥和 requestId 防重放缓存保持稳定。
    return build_auth_service()


def require_auth_user_context(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUserContext:
    return auth_service.require_user(_to_authorization_header(credentials))


def require_admin_user_context(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUserContext:
    return auth_service.require_admin(_to_authorization_header(credentials))


def _to_authorization_header(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials is None:
        return None
    return f"{credentials.scheme} {credentials.credentials}"


__all__ = [
    "AuthUserContext",
    "get_auth_service",
    "require_admin_user_context",
    "require_auth_user_context",
]
