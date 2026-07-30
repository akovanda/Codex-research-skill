from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from hashlib import sha1, sha256
from pathlib import Path, PurePosixPath
import struct
from typing import Mapping
import zlib

from ..application.source_versions import (
    SourceVersionCreateResult,
    SourceVersionService,
)
from ..contracts.common import SnapshotPolicy
from ..domain.evidence import (
    EvidenceResolution,
    SourceVersionProvenance,
    resolve_exact_evidence,
)
from ..domain.sources import SourceVersionSpec


class GitIngestionError(RuntimeError):
    """A repository cannot be captured under the configured local policy."""


class GitRepositoryDenied(GitIngestionError):
    pass


class GitObjectNotFound(GitIngestionError):
    pass


class GitObjectInvalid(GitIngestionError):
    pass


@dataclass(frozen=True)
class GitIngestionPolicy:
    allowed_roots: tuple[Path, ...]
    repositories: Mapping[str, Path]
    max_object_bytes: int = 20_000_000
    max_tree_entries: int = 100_000
    max_pack_index_bytes: int = 50_000_000

    def __post_init__(self) -> None:
        if not self.allowed_roots:
            raise ValueError("at least one explicit Git root is required")
        if not self.repositories:
            raise ValueError("at least one explicit repository is required")
        if any(
            limit <= 0
            for limit in (
                self.max_object_bytes,
                self.max_tree_entries,
                self.max_pack_index_bytes,
            )
        ):
            raise ValueError("Git ingestion limits must be positive")
        for repository_id in self.repositories:
            if (
                not repository_id
                or len(repository_id) > 200
                or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in repository_id)
            ):
                raise ValueError("repository IDs must be bounded identifiers")


@dataclass(frozen=True)
class GitBlob:
    repository_id: str
    commit_sha: str
    blob_sha: str
    path: str
    file_mode: str
    content: bytes


@dataclass(frozen=True)
class CapturedGitSource:
    version: SourceVersionCreateResult
    content: bytes
    file_mode: str


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    name: str
    oid: str


class GitSourceIngestor:
    """Read immutable Git objects directly; never execute Git or repository code."""

    def __init__(
        self,
        policy: GitIngestionPolicy,
        versions: SourceVersionService,
    ) -> None:
        self.policy = policy
        self.versions = versions

    def capture(
        self,
        *,
        source_id: str,
        repository_id: str,
        commit_sha: str,
        path: str,
        snapshot_policy: SnapshotPolicy,
    ) -> CapturedGitSource:
        blob = self.resolve_blob(
            repository_id=repository_id,
            commit_sha=commit_sha,
            path=path,
        )
        try:
            blob.content.decode("utf-8")
            media_type = "text/plain"
        except UnicodeDecodeError:
            media_type = "application/octet-stream"
            if snapshot_policy == "extracted_text":
                raise GitObjectInvalid(
                    "PARSER_FAILED: Extracted-text Git capture requires UTF-8 content."
                )
        content_hash = sha256(blob.content).hexdigest()
        result = self.versions.create_or_reuse(
            SourceVersionSpec(
                source_id=source_id,
                version_key=f"git:{blob.commit_sha}:{blob.blob_sha}",
                version_kind="git_blob",
                retrieved_at=_utc_now_text(),
                content_sha256=content_hash,
                canonical_locator=(
                    f"git:{repository_id}:{blob.commit_sha}:{blob.path}"
                ),
                snapshot_policy=snapshot_policy,
                snapshot_bytes=(
                    blob.content
                    if snapshot_policy in {"extracted_text", "full_content"}
                    else None
                ),
                media_type=media_type,
                byte_count=len(blob.content),
                parser_name="research-registry-git",
                parser_version="2",
                repository_locator=f"git:{repository_id}",
                commit_sha=blob.commit_sha,
                blob_sha=blob.blob_sha,
                path=blob.path,
                metadata={
                    "repository_id": repository_id,
                    "object_type": "blob",
                    "file_mode": blob.file_mode,
                    "untrusted_content": True,
                },
            )
        )
        return CapturedGitSource(
            version=result,
            content=blob.content,
            file_mode=blob.file_mode,
        )

    def resolve_blob(
        self,
        *,
        repository_id: str,
        commit_sha: str,
        path: str,
    ) -> GitBlob:
        normalized_path = _normalize_path(path)
        git_dir = self._git_directory(repository_id)
        oid = _validate_oid(commit_sha)
        reader = _GitObjectReader(git_dir, len(oid) // 2, self.policy)
        kind, commit_body = reader.read(oid)
        if kind != "commit":
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Object is not a commit.")
        tree_oid = _commit_tree(commit_body, len(oid))
        current_oid = tree_oid
        parts = PurePosixPath(normalized_path).parts
        for index, part in enumerate(parts):
            kind, tree_body = reader.read(current_oid)
            if kind != "tree":
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Path is not a tree.")
            entries = _parse_tree(tree_body, len(oid) // 2, self.policy.max_tree_entries)
            entry = next((item for item in entries if item.name == part), None)
            if entry is None:
                raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Git path was not found.")
            if index < len(parts) - 1:
                if entry.mode not in {"40000", "040000"}:
                    raise GitObjectNotFound(
                        "GIT_OBJECT_NOT_FOUND: Git path traversal is not a tree."
                    )
            elif entry.mode in {"120000", "160000"}:
                raise GitObjectNotFound(
                    "GIT_OBJECT_NOT_FOUND: Symlinks and submodules are not capturable files."
                )
            current_oid = entry.oid
            final_mode = entry.mode
        kind, content = reader.read(current_oid)
        if kind != "blob" or final_mode not in {"100644", "100755"}:
            raise GitObjectNotFound(
                "GIT_OBJECT_NOT_FOUND: Git path does not identify a regular blob."
            )
        return GitBlob(
            repository_id=repository_id,
            commit_sha=oid,
            blob_sha=current_oid,
            path=normalized_path,
            file_mode=final_mode,
            content=content,
        )

    def find_blob_paths(
        self,
        *,
        repository_id: str,
        commit_sha: str,
        blob_sha: str,
    ) -> tuple[str, ...]:
        git_dir = self._git_directory(repository_id)
        commit_oid = _validate_oid(commit_sha)
        expected_blob = _validate_oid(blob_sha, expected_length=len(commit_oid))
        reader = _GitObjectReader(git_dir, len(commit_oid) // 2, self.policy)
        kind, commit_body = reader.read(commit_oid)
        if kind != "commit":
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Object is not a commit.")
        root_tree = _commit_tree(commit_body, len(commit_oid))
        found: list[str] = []
        visited = 0
        pending: list[tuple[str, str]] = [("", root_tree)]
        while pending:
            prefix, tree_oid = pending.pop()
            kind, body = reader.read(tree_oid)
            if kind != "tree":
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Tree object is invalid.")
            for entry in _parse_tree(
                body,
                len(commit_oid) // 2,
                self.policy.max_tree_entries,
            ):
                visited += 1
                if visited > self.policy.max_tree_entries:
                    raise GitObjectInvalid(
                        "GIT_OBJECT_NOT_FOUND: Repository tree exceeds the scan limit."
                    )
                path = f"{prefix}/{entry.name}" if prefix else entry.name
                if entry.mode in {"40000", "040000"}:
                    pending.append((path, entry.oid))
                elif entry.mode in {"100644", "100755"} and entry.oid == expected_blob:
                    found.append(path)
        return tuple(sorted(found))

    def current_commit(self, repository_id: str) -> str:
        """Resolve HEAD using bounded metadata reads, without invoking Git."""
        git_dir = self._git_directory(repository_id)
        head = _read_bounded(git_dir / "HEAD", 4096).decode(
            "ascii",
            errors="strict",
        ).strip()
        if not head.startswith("ref: "):
            return _validate_oid(head)
        reference = head.removeprefix("ref: ").strip()
        if (
            not reference.startswith("refs/")
            or reference.startswith("/")
            or "\\" in reference
            or ".." in PurePosixPath(reference).parts
            or str(PurePosixPath(reference)) != reference
        ):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git HEAD reference is invalid.")
        loose_ref = git_dir.joinpath(*PurePosixPath(reference).parts)
        if loose_ref.is_symlink():
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Git references may not be symlinks."
            )
        if loose_ref.is_file():
            return _validate_oid(
                _read_bounded(loose_ref, 4096).decode("ascii").strip()
            )
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_symlink() or not packed_refs.is_file():
            raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Git HEAD was not found.")
        for raw_line in _read_bounded(
            packed_refs,
            self.policy.max_pack_index_bytes,
        ).splitlines():
            if not raw_line or raw_line.startswith((b"#", b"^")):
                continue
            oid_bytes, separator, ref_bytes = raw_line.partition(b" ")
            if separator and ref_bytes.decode("ascii", errors="strict") == reference:
                return _validate_oid(oid_bytes.decode("ascii"))
        raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Git HEAD was not found.")

    @staticmethod
    def resolve_selector(
        captured: CapturedGitSource,
        selector: object,
        quote_text: str,
    ) -> EvidenceResolution:
        record = captured.version.record
        return resolve_exact_evidence(
            captured.content,
            selector,
            quote_text,
            provenance=SourceVersionProvenance(
                path=record.path,
                commit_sha=record.commit_sha,
                blob_sha=record.blob_sha,
            ),
        )

    def _git_directory(self, repository_id: str) -> Path:
        configured = self.policy.repositories.get(repository_id)
        if configured is None:
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Repository ID is not explicitly configured."
            )
        raw_repository = Path(configured).expanduser()
        if raw_repository.is_symlink():
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Repository symlinks are not allowed."
            )
        repository = raw_repository.resolve(strict=True)
        roots = tuple(Path(root).expanduser().resolve(strict=True) for root in self.policy.allowed_roots)
        if not any(_is_relative_to(repository, root) for root in roots):
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Repository is outside configured roots."
            )
        git_dir = repository / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Repository must have a contained .git directory."
            )
        resolved_git = git_dir.resolve(strict=True)
        if not _is_relative_to(resolved_git, repository):
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Git metadata escapes the repository root."
            )
        objects = resolved_git / "objects"
        if objects.is_symlink() or not objects.is_dir():
            raise GitRepositoryDenied(
                "CAPTURE_POLICY_DENIED: Git object storage is unavailable or redirected."
            )
        return resolved_git


class _GitObjectReader:
    def __init__(
        self,
        git_dir: Path,
        oid_bytes: int,
        policy: GitIngestionPolicy,
    ) -> None:
        if oid_bytes not in {20, 32}:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Object ID length is invalid.")
        self.git_dir = git_dir
        self.oid_bytes = oid_bytes
        self.policy = policy
        self._offset_cache: dict[tuple[Path, int], tuple[str, bytes]] = {}
        self._reading_oids: set[str] = set()

    def read(self, oid: str) -> tuple[str, bytes]:
        _validate_oid(oid, expected_length=self.oid_bytes * 2)
        if oid in self._reading_oids:
            raise GitObjectInvalid(
                "GIT_OBJECT_NOT_FOUND: Git delta cycle is invalid."
            )
        self._reading_oids.add(oid)
        try:
            loose = self.git_dir / "objects" / oid[:2] / oid[2:]
            if loose.is_file() and not loose.is_symlink():
                compressed = _read_bounded(
                    loose, self.policy.max_object_bytes + 1024
                )
                raw = _decompress_loose_object(
                    compressed,
                    self.policy.max_object_bytes,
                )
                kind, body = _split_object(raw, self.policy.max_object_bytes)
                _verify_oid(oid, kind, body)
                return kind, body
            packed = self._read_packed(oid)
            if packed is not None:
                return packed
            raise GitObjectNotFound(
                "GIT_OBJECT_NOT_FOUND: Git object was not found."
            )
        finally:
            self._reading_oids.remove(oid)

    def _read_packed(self, oid: str) -> tuple[str, bytes] | None:
        pack_dir = self.git_dir / "objects" / "pack"
        if not pack_dir.is_dir() or pack_dir.is_symlink():
            return None
        for index_path in sorted(pack_dir.glob("pack-*.idx")):
            if index_path.is_symlink():
                continue
            match = _find_pack_offset(
                index_path,
                oid,
                self.oid_bytes,
                self.policy.max_pack_index_bytes,
            )
            if match is None:
                continue
            pack_path = index_path.with_suffix(".pack")
            if pack_path.is_symlink() or not pack_path.is_file():
                raise GitObjectInvalid(
                    "GIT_OBJECT_NOT_FOUND: Pack file is unavailable."
                )
            pack_data = _read_bounded(
                pack_path,
                max(self.policy.max_pack_index_bytes, self.policy.max_object_bytes * 4),
            )
            kind, body = self._read_pack_entry(
                pack_path,
                pack_data,
                match,
                depth=0,
                visiting=frozenset(),
            )
            _verify_oid(oid, kind, body)
            return kind, body
        return None

    def _read_pack_entry(
        self,
        pack_path: Path,
        pack: bytes,
        offset: int,
        *,
        depth: int,
        visiting: frozenset[int],
    ) -> tuple[str, bytes]:
        if depth > 64 or offset in visiting:
            raise GitObjectInvalid(
                "GIT_OBJECT_NOT_FOUND: Git delta chain is invalid."
            )
        visiting = visiting | {offset}
        cache_key = (pack_path, offset)
        cached = self._offset_cache.get(cache_key)
        if cached is not None:
            return cached
        if len(pack) < 12 or pack[:4] != b"PACK" or offset < 12 or offset >= len(pack):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack file is invalid.")
        position = offset
        first = pack[position]
        position += 1
        object_type = (first >> 4) & 0x07
        size = first & 0x0F
        shift = 4
        byte = first
        while byte & 0x80:
            if position >= len(pack):
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack header is truncated.")
            byte = pack[position]
            position += 1
            size |= (byte & 0x7F) << shift
            shift += 7
            if shift > 63:
                raise GitObjectInvalid(
                    "GIT_OBJECT_NOT_FOUND: Pack header is invalid."
                )
        if size > self.policy.max_object_bytes:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object exceeds the size limit.")

        base: tuple[str, bytes] | None = None
        if object_type == 6:
            if position >= len(pack):
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Delta base is missing.")
            byte = pack[position]
            position += 1
            distance = byte & 0x7F
            while byte & 0x80:
                if position >= len(pack):
                    raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Delta base is invalid.")
                byte = pack[position]
                position += 1
                distance = ((distance + 1) << 7) | (byte & 0x7F)
            base = self._read_pack_entry(
                pack_path,
                pack,
                offset - distance,
                depth=depth + 1,
                visiting=visiting,
            )
        elif object_type == 7:
            end = position + self.oid_bytes
            if end > len(pack):
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Delta base is truncated.")
            base_oid = pack[position:end].hex()
            position = end
            base = self.read(base_oid)

        try:
            inflater = zlib.decompressobj()
            data = inflater.decompress(
                pack[position:],
                self.policy.max_object_bytes + 1,
            )
        except zlib.error as exc:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Packed object is invalid.") from exc
        if len(data) > self.policy.max_object_bytes:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object exceeds the size limit.")
        if not inflater.eof:
            raise GitObjectInvalid(
                "GIT_OBJECT_NOT_FOUND: Packed object exceeds the size limit."
            )
        if object_type in {1, 2, 3, 4}:
            kind = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}[object_type]
            body = data
        elif object_type in {6, 7} and base is not None:
            kind, base_body = base
            body = _apply_delta(base_body, data, self.policy.max_object_bytes)
        else:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack object type is unsupported.")
        if len(body) != size:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Packed object size is invalid.")
        self._offset_cache[cache_key] = (kind, body)
        return kind, body


def _find_pack_offset(
    index_path: Path,
    oid: str,
    oid_bytes: int,
    maximum: int,
) -> int | None:
    data = _read_bounded(index_path, maximum)
    if len(data) < 8 + (256 * 4) or data[:4] != b"\xfftOc":
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack index format is unsupported.")
    version = struct.unpack(">I", data[4:8])[0]
    if version != 2:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack index version is unsupported.")
    fanout_start = 8
    fanout = struct.unpack(">256I", data[fanout_start : fanout_start + 1024])
    count = fanout[-1]
    names_start = fanout_start + 1024
    names_end = names_start + count * oid_bytes
    crc_end = names_end + count * 4
    offsets_end = crc_end + count * 4
    if offsets_end > len(data):
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack index is truncated.")
    target = bytes.fromhex(oid)
    first_byte = target[0]
    lower = fanout[first_byte - 1] if first_byte else 0
    upper = fanout[first_byte]
    names = [
        data[names_start + index * oid_bytes : names_start + (index + 1) * oid_bytes]
        for index in range(lower, upper)
    ]
    relative = bisect_left(names, target)
    if relative >= len(names) or names[relative] != target:
        return None
    index = lower + relative
    offset_value = struct.unpack(
        ">I",
        data[crc_end + index * 4 : crc_end + (index + 1) * 4],
    )[0]
    if offset_value & 0x80000000:
        large_index = offset_value & 0x7FFFFFFF
        large_start = offsets_end + large_index * 8
        if large_start + 8 > len(data):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Pack offset is invalid.")
        return struct.unpack(">Q", data[large_start : large_start + 8])[0]
    return offset_value


def _apply_delta(base: bytes, delta: bytes, maximum: int) -> bytes:
    position = 0
    base_size, position = _delta_size(delta, position)
    result_size, position = _delta_size(delta, position)
    if base_size != len(base) or result_size > maximum:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta size is invalid.")
    result = bytearray()
    while position < len(delta):
        opcode = delta[position]
        position += 1
        if opcode & 0x80:
            copy_offset = 0
            copy_size = 0
            for bit, shift in ((0x01, 0), (0x02, 8), (0x04, 16), (0x08, 24)):
                if opcode & bit:
                    if position >= len(delta):
                        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta is truncated.")
                    copy_offset |= delta[position] << shift
                    position += 1
            for bit, shift in ((0x10, 0), (0x20, 8), (0x40, 16)):
                if opcode & bit:
                    if position >= len(delta):
                        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta is truncated.")
                    copy_size |= delta[position] << shift
                    position += 1
            if copy_size == 0:
                copy_size = 0x10000
            end = copy_offset + copy_size
            if end > len(base):
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta copy is invalid.")
            result.extend(base[copy_offset:end])
        elif opcode:
            end = position + opcode
            if end > len(delta):
                raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta insert is invalid.")
            result.extend(delta[position:end])
            position = end
        else:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta opcode is invalid.")
        if len(result) > maximum:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta exceeds the size limit.")
    if len(result) != result_size:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta result is invalid.")
    return bytes(result)


def _delta_size(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if position >= len(data):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta size is truncated.")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7
        if shift > 63:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git delta size is invalid.")


def _split_object(raw: bytes, maximum: int) -> tuple[str, bytes]:
    header, separator, body = raw.partition(b"\0")
    if not separator:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object header is invalid.")
    kind_bytes, space, size_bytes = header.partition(b" ")
    if not space or kind_bytes not in {b"commit", b"tree", b"blob", b"tag"}:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object type is invalid.")
    try:
        size = int(size_bytes)
    except ValueError as exc:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object size is invalid.") from exc
    if size != len(body) or size > maximum:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object size is invalid.")
    return kind_bytes.decode("ascii"), body


def _decompress_loose_object(compressed: bytes, maximum: int) -> bytes:
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, maximum + 257)
    except zlib.error as exc:
        raise GitObjectInvalid(
            "GIT_OBJECT_NOT_FOUND: Loose Git object is invalid."
        ) from exc
    if (
        len(raw) > maximum + 256
        or not inflater.eof
        or inflater.unconsumed_tail
        or inflater.unused_data
    ):
        raise GitObjectInvalid(
            "GIT_OBJECT_NOT_FOUND: Loose Git object exceeds the size limit."
        )
    return raw


def _verify_oid(oid: str, kind: str, body: bytes) -> None:
    raw = f"{kind} {len(body)}\0".encode() + body
    digest = sha1(raw).hexdigest() if len(oid) == 40 else sha256(raw).hexdigest()
    if digest != oid:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object hash is invalid.")


def _commit_tree(body: bytes, oid_length: int) -> str:
    first = body.split(b"\n", 1)[0]
    prefix = b"tree "
    if not first.startswith(prefix):
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Commit tree is missing.")
    try:
        oid = first[len(prefix) :].decode("ascii")
    except UnicodeDecodeError as exc:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Commit tree is invalid.") from exc
    return _validate_oid(oid, expected_length=oid_length)


def _parse_tree(body: bytes, oid_bytes: int, maximum: int) -> tuple[_TreeEntry, ...]:
    entries: list[_TreeEntry] = []
    position = 0
    while position < len(body):
        space = body.find(b" ", position)
        nul = body.find(b"\0", space + 1)
        oid_end = nul + 1 + oid_bytes
        if space < 0 or nul < 0 or oid_end > len(body):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Tree object is invalid.")
        try:
            mode = body[position:space].decode("ascii")
            name = body[space + 1 : nul].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Tree entry is invalid.") from exc
        if (
            not name
            or "/" in name
            or name in {".", ".."}
            or "\x00" in name
        ):
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Tree entry name is invalid.")
        entries.append(
            _TreeEntry(
                mode=mode,
                name=name,
                oid=body[nul + 1 : oid_end].hex(),
            )
        )
        if len(entries) > maximum:
            raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Tree exceeds the entry limit.")
        position = oid_end
    return tuple(entries)


def _normalize_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value.startswith("/")
        or "\\" in value
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
    ):
        raise GitRepositoryDenied(
            "CAPTURE_POLICY_DENIED: Git path must be normalized and relative."
        )
    return value


def _validate_oid(value: str, *, expected_length: int | None = None) -> str:
    if not isinstance(value, str):
        raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Full object ID is required.")
    lowered = value.lower()
    length = expected_length or len(lowered)
    if (
        len(lowered) not in {40, 64}
        or len(lowered) != length
        or value != lowered
        or any(char not in "0123456789abcdef" for char in lowered)
    ):
        raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Full object ID is required.")
    return lowered


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Git object is unavailable.") from exc
    if size > maximum:
        raise GitObjectInvalid("GIT_OBJECT_NOT_FOUND: Git object exceeds the size limit.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GitObjectNotFound("GIT_OBJECT_NOT_FOUND: Git object is unavailable.") from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _utc_now_text() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
