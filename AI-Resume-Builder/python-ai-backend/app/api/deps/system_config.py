from app.application.services.system_service_config_service import SystemServiceConfigService
from app.bootstrap.container import build_system_service_config_service


def get_system_service_config_service() -> SystemServiceConfigService:
    return build_system_service_config_service()


__all__ = ["get_system_service_config_service"]
