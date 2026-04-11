from __future__ import annotations

from .backend_client import create_backend
from .config import load_settings
from .mcp_tools import create_mcp_server
from .service import RegistryService

settings = load_settings()
backend = create_backend(settings)
service = backend if isinstance(backend, RegistryService) else None
mcp = create_mcp_server(
    backend,
    settings=settings,
    service=service,
    default_api_key=settings.backend_api_key,
    allow_admin_fallback=True,
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
