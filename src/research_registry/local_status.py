from __future__ import annotations

from .local_manager import format_status, local_runtime_status


def main() -> None:
    print(format_status(local_runtime_status()))


if __name__ == "__main__":
    main()
