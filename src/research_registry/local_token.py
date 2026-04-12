from __future__ import annotations

from .local_manager import format_tokens, local_runtime_tokens


def main() -> None:
    print(format_tokens(local_runtime_tokens()))


if __name__ == "__main__":
    main()
