from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from shutil import copy2
from typing import Any, Mapping, Sequence


def build_spdx_sbom(
    *,
    project_name: str,
    project_version: str,
    dependencies: Mapping[str, str],
) -> dict[str, Any]:
    """Build a deterministic SPDX 2.3 package inventory without network I/O."""
    all_packages = {
        **{str(name): str(version) for name, version in dependencies.items()},
        project_name: project_version,
    }
    packages = [
        {
            "name": name,
            "SPDXID": _spdx_id(name),
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": (
                "Apache-2.0" if name == project_name else "NOASSERTION"
            ),
            "licenseDeclared": (
                "Apache-2.0" if name == project_name else "NOASSERTION"
            ),
            "copyrightText": "NOASSERTION",
        }
        for name, version in sorted(all_packages.items())
    ]
    relationships = [
        {
            "spdxElementId": _spdx_id(project_name),
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": _spdx_id(name),
        }
        for name in sorted(dependencies)
    ]
    namespace_digest = sha256(
        "\n".join(
            f"{name}=={version}"
            for name, version in sorted(all_packages.items())
        ).encode("utf-8")
    ).hexdigest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_name}-{project_version}",
        "documentNamespace": (
            "https://research-registry.invalid/spdx/" + namespace_digest
        ),
        "creationInfo": {
            "created": "1970-01-01T00:00:00Z",
            "creators": ["Tool: research-registry-offline-sbom/1"],
        },
        "documentDescribes": [_spdx_id(project_name)],
        "packages": packages,
        "relationships": relationships,
    }


def build_provenance_statement(
    artifacts: Sequence[Path],
    *,
    source_uri: str,
    source_revision: str,
) -> dict[str, Any]:
    """Describe already-built artifacts; this does not sign or publish them."""
    if not artifacts:
        raise ValueError("at least one release artifact is required")
    if len(source_revision) != 40 or any(
        character not in "0123456789abcdef"
        for character in source_revision.lower()
    ):
        raise ValueError("source revision must be a 40-character Git SHA")
    subjects = []
    for artifact in sorted(
        (path.expanduser().resolve() for path in artifacts),
        key=lambda path: path.name,
    ):
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        subjects.append(
            {
                "name": artifact.name,
                "digest": {"sha256": _file_sha256(artifact)},
            }
        )
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": (
                    "https://research-registry.invalid/build/python-package/v1"
                ),
                "externalParameters": {
                    "source": {
                        "uri": source_uri,
                        "revision": source_revision.lower(),
                    }
                },
                "internalParameters": {},
                "resolvedDependencies": [],
            },
            "runDetails": {
                "builder": {
                    "id": "https://research-registry.invalid/offline-builder/v1"
                },
                "metadata": {"invocationId": "local-unpublished"},
            },
        },
    }


def stage_release_artifacts(
    artifacts: Sequence[Path],
    *,
    output_directory: Path,
) -> list[Path]:
    """Copy package artifacts into a self-contained release directory."""
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    names: set[str] = set()
    for artifact in sorted(
        (path.expanduser().resolve() for path in artifacts),
        key=lambda path: path.name,
    ):
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        if artifact.name in names:
            raise ValueError(f"duplicate artifact name: {artifact.name}")
        names.add(artifact.name)
        target = output_directory / artifact.name
        if artifact != target:
            copy2(artifact, target)
        staged.append(target)
    return staged


def write_sha256_manifest(
    artifacts: Sequence[Path],
    *,
    output_path: Path,
) -> None:
    """Write checksums that resolve relative to the manifest location."""
    base = output_path.expanduser().resolve().parent
    lines: list[str] = []
    for artifact in sorted(
        (path.expanduser().resolve() for path in artifacts),
        key=lambda path: path.name,
    ):
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        try:
            relative_path = artifact.relative_to(base)
        except ValueError as error:
            raise ValueError(
                f"checksummed artifact is outside manifest directory: {artifact}"
            ) from error
        lines.append(f"{_file_sha256(artifact)}  {relative_path.as_posix()}\n")
    output_path.write_text("".join(lines), encoding="utf-8")


def _spdx_id(name: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in ".-" else "-"
        for character in name
    )
    return f"SPDXRef-Package-{normalized}"


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
