from __future__ import annotations

from .config import load_settings
from .service import RegistryService


def main() -> None:
    settings = load_settings()
    service = RegistryService(settings.database_url)
    service.initialize()
    print(f"migrated {service.database.label}")


if __name__ == "__main__":
    main()
