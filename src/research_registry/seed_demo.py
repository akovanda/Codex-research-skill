from __future__ import annotations

from .config import load_settings
from .service import RegistryService


def main() -> None:
    settings = load_settings()
    service = RegistryService(settings.db_path)
    service.initialize()
    seeded = service.seed_demo()
    if seeded:
        print(f"seeded report {seeded['report_id']}")
    else:
        print("demo data already present")


if __name__ == "__main__":
    main()
