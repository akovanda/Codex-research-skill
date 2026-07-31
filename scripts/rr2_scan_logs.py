from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_registry.release.security import scan_log_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail when a bounded log contains credential patterns."
    )
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--forbid-file",
        type=Path,
        default=None,
        help="Private newline-delimited sentinel values; never printed.",
    )
    args = parser.parse_args()
    forbidden = (
        args.forbid_file.read_text(encoding="utf-8").splitlines()
        if args.forbid_file is not None
        else ()
    )
    findings = scan_log_text(args.log, forbidden_values=forbidden)
    print(
        json.dumps(
            {
                "status": "clean" if not findings else "sensitive",
                "finding_count": len(findings),
                "findings": [
                    {
                        "kind": finding.kind,
                        "line_number": finding.line_number,
                        "fingerprint": finding.fingerprint,
                    }
                    for finding in findings
                ],
            },
            sort_keys=True,
        )
    )
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
