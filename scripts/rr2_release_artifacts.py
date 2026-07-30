from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, metadata, version
import json
from pathlib import Path
import re
import subprocess

from research_registry.release.artifacts import (
    build_provenance_statement,
    build_spdx_sbom,
    stage_release_artifacts,
    write_sha256_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create offline RR2 SBOM, checksums, and provenance."
    )
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / ".data" / "release")
    args = parser.parse_args()

    project_version = version("research-registry")
    artifact_prefix = f"research_registry-{project_version}"
    artifacts = sorted(
        [
            *args.dist.glob(f"{artifact_prefix}*.whl"),
            *args.dist.glob(f"{artifact_prefix}*.tar.gz"),
        ],
        key=lambda path: path.name,
    )
    if not artifacts:
        raise SystemExit("no wheel or sdist found; run python -m build first")
    output = args.output / project_version
    output.mkdir(parents=True, exist_ok=True)
    staged_artifacts = stage_release_artifacts(
        artifacts,
        output_directory=output,
    )
    dependencies = _installed_dependencies()
    sbom = build_spdx_sbom(
        project_name="research-registry",
        project_version=project_version,
        dependencies=dependencies,
    )
    revision = _git("rev-parse", "HEAD")
    provenance = build_provenance_statement(
        staged_artifacts,
        source_uri="https://github.com/akovanda/Codex-research-skill",
        source_revision=revision,
    )
    provenance["predicate"]["runDetails"]["metadata"]["workingTreeDirty"] = bool(
        _git("status", "--porcelain")
    )
    sbom_path = output / "research-registry.spdx.json"
    provenance_path = output / "research-registry.intoto.jsonl"
    sbom_path.write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provenance_path.write_text(
        json.dumps(provenance, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksummed = [*staged_artifacts, sbom_path, provenance_path]
    checksums_path = output / "SHA256SUMS"
    write_sha256_manifest(
        checksummed,
        output_path=checksums_path,
    )
    print(
        json.dumps(
            {
                "status": "created",
                "publication": "none",
                "signature": "none",
                "working_tree_dirty": provenance["predicate"]["runDetails"][
                    "metadata"
                ]["workingTreeDirty"],
                "files": [
                    str(path)
                    for path in (
                        *staged_artifacts,
                        sbom_path,
                        provenance_path,
                        checksums_path,
                    )
                ],
            },
            sort_keys=True,
        )
    )


def _installed_dependencies() -> dict[str, str]:
    try:
        requirements = metadata("research-registry").get_all("Requires-Dist")
    except PackageNotFoundError:
        requirements = []
    result: dict[str, str] = {}
    for requirement in requirements or []:
        if "extra ==" in requirement:
            continue
        name = re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0]
        if not name:
            continue
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not-installed"
    return result


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    main()
