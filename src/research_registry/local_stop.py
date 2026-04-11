from __future__ import annotations

from .local_manager import format_status, local_runtime_status, stop_local_runtime


def main() -> None:
    stop_local_runtime()
    print(format_status(local_runtime_status()))


if __name__ == "__main__":
    main()
