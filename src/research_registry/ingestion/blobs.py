from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256 as sha256_digest
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Protocol
from uuid import uuid4


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STORAGE_KEY = re.compile(
    r"^sha256/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{64})$"
)
_MEDIA_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$"
)
_STAGE_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


class BlobStoreError(RuntimeError):
    """Base error for content-addressed blob storage."""


class BlobValidationError(BlobStoreError):
    """Raised when declared blob metadata does not match the bytes."""


class BlobContainmentError(BlobStoreError):
    """Raised when a path is not a generated, contained regular file."""


class BlobIntegrityError(BlobStoreError):
    """Raised when persisted bytes do not match their database reference."""


@dataclass(frozen=True)
class BlobReference:
    sha256: str
    storage_key: str
    byte_count: int
    media_type: str | None


@dataclass(frozen=True)
class StagedBlob:
    token: str
    sha256: str
    storage_key: str
    byte_count: int
    media_type: str | None


@dataclass(frozen=True)
class FinalizedBlob:
    sha256: str
    storage_key: str
    byte_count: int
    media_type: str | None
    path: Path
    reused: bool


@dataclass(frozen=True)
class BlobHealthReport:
    referenced_objects: int
    stored_objects: int
    staged_objects: int
    missing_keys: tuple[str, ...]
    corrupt_keys: tuple[str, ...]
    orphan_keys: tuple[str, ...]
    unsafe_keys: tuple[str, ...]
    database_reference_errors: int = 0

    @property
    def healthy(self) -> bool:
        return not (
            self.missing_keys
            or self.corrupt_keys
            or self.unsafe_keys
            or self.database_reference_errors
        )

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "healthy": self.healthy,
        }


class BlobStore(Protocol):
    """Storage interface retained for future backend adapters."""

    def stage_bytes(
        self,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_byte_count: int | None = None,
        media_type: str | None = None,
    ) -> StagedBlob: ...

    def finalize(self, staged: StagedBlob) -> FinalizedBlob: ...

    def discard(self, staged: StagedBlob) -> None: ...

    def read(self, reference: BlobReference, *, verify: bool = True) -> bytes: ...

    def inspect(self, references: Iterable[BlobReference]) -> BlobHealthReport: ...


def storage_key_for_sha256(value: str) -> str:
    digest = validate_sha256(value)
    return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"


def validate_sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BlobValidationError("SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def validate_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
        raise BlobValidationError("media type must be a bounded type/subtype value")
    return value.lower()


class FilesystemBlobStore:
    """A generated-key-only, content-addressed filesystem blob store."""

    def __init__(self, root: Path):
        raw_root = root.expanduser()
        if not raw_root.is_absolute():
            raw_root = Path.cwd() / raw_root
        if raw_root == Path(raw_root.anchor) or raw_root == Path.home():
            raise BlobContainmentError(
                "blob root must be a dedicated storage directory"
            )
        _reject_existing_symlink_components(raw_root)
        raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not raw_root.is_dir():
            raise BlobContainmentError("blob root must be a directory")
        _reject_symlink(raw_root)
        self.root = raw_root.resolve(strict=True)
        self._staging_root = self.root / ".staging"
        self._ensure_private_directory(self._staging_root)
        self._staged: dict[str, StagedBlob] = {}
        self._chmod_private(self.root, 0o700)
        self._chmod_private(self._staging_root, 0o700)

    def stage_bytes(
        self,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_byte_count: int | None = None,
        media_type: str | None = None,
    ) -> StagedBlob:
        if not isinstance(content, bytes):
            raise BlobValidationError("blob content must be bytes")
        actual_sha256 = sha256_digest(content).hexdigest()
        actual_byte_count = len(content)
        if expected_sha256 is not None:
            validate_sha256(expected_sha256)
            if actual_sha256 != expected_sha256:
                raise BlobValidationError("SHA-256 does not match staged content")
        if (
            expected_byte_count is not None
            and (
                not isinstance(expected_byte_count, int)
                or isinstance(expected_byte_count, bool)
                or expected_byte_count < 0
            )
        ):
            raise BlobValidationError("expected byte count must be non-negative")
        if (
            expected_byte_count is not None
            and actual_byte_count != expected_byte_count
        ):
            raise BlobValidationError("byte count does not match staged content")
        normalized_media_type = validate_media_type(media_type)
        token = uuid4().hex
        stage_path = self._stage_path(token)
        staged = StagedBlob(
            token=token,
            sha256=actual_sha256,
            storage_key=storage_key_for_sha256(actual_sha256),
            byte_count=actual_byte_count,
            media_type=normalized_media_type,
        )
        try:
            descriptor = os.open(
                stage_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._chmod_private(stage_path, 0o600)
            self._staged[token] = staged
            return staged
        except Exception:
            try:
                stage_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def finalize(self, staged: StagedBlob) -> FinalizedBlob:
        self._require_owned_stage(staged)
        stage_path = self._stage_path(staged.token)
        actual_sha256, actual_byte_count = self._digest_regular_file(stage_path)
        if (
            actual_sha256 != staged.sha256
            or actual_byte_count != staged.byte_count
        ):
            raise BlobIntegrityError("staged blob integrity verification failed")

        final_path = self._path_for_reference(
            BlobReference(
                sha256=staged.sha256,
                storage_key=staged.storage_key,
                byte_count=staged.byte_count,
                media_type=staged.media_type,
            ),
            require_exists=False,
        )
        self._ensure_private_directory(self.root / "sha256")
        self._ensure_private_directory(self.root / "sha256" / staged.sha256[:2])
        self._ensure_private_directory(final_path.parent)
        reused = False
        try:
            _reject_symlink(final_path)
            existing_sha256, existing_byte_count = self._digest_regular_file(
                final_path
            )
        except FileNotFoundError:
            if stage_path.stat().st_dev != final_path.parent.stat().st_dev:
                raise BlobStoreError(
                    "staged and final blob paths are not on the same filesystem"
                )
            os.replace(stage_path, final_path)
            self._chmod_private(final_path, 0o600)
            self._fsync_directory(final_path.parent)
        else:
            if (
                existing_sha256 != staged.sha256
                or existing_byte_count != staged.byte_count
            ):
                raise BlobIntegrityError(
                    "existing content-addressed blob failed integrity verification"
                )
            stage_path.unlink()
            reused = True
        self._staged.pop(staged.token, None)
        return FinalizedBlob(
            sha256=staged.sha256,
            storage_key=staged.storage_key,
            byte_count=staged.byte_count,
            media_type=staged.media_type,
            path=final_path,
            reused=reused,
        )

    def discard(self, staged: StagedBlob) -> None:
        owned = self._staged.get(staged.token)
        if owned != staged:
            return
        stage_path = self._stage_path(staged.token)
        try:
            _reject_symlink(stage_path)
        except FileNotFoundError:
            self._staged.pop(staged.token, None)
            return
        stage_path.unlink(missing_ok=True)
        self._staged.pop(staged.token, None)

    def read(self, reference: BlobReference, *, verify: bool = True) -> bytes:
        path = self._path_for_reference(reference, require_exists=True)
        descriptor = os.open(path, _READ_FLAGS)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise BlobContainmentError("blob reference is not a regular file")
            _require_private_file_mode(file_stat.st_mode)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read()
        finally:
            os.close(descriptor)
        if verify:
            if (
                sha256_digest(content).hexdigest() != reference.sha256
                or len(content) != reference.byte_count
            ):
                raise BlobIntegrityError(
                    "persisted blob does not match its hash and byte count"
                )
        return content

    def inspect(self, references: Iterable[BlobReference]) -> BlobHealthReport:
        unique_references = {
            reference.storage_key: reference for reference in references
        }
        missing: list[str] = []
        corrupt: list[str] = []
        unsafe: list[str] = []
        for key, reference in sorted(unique_references.items()):
            try:
                self.read(reference, verify=True)
            except FileNotFoundError:
                missing.append(key)
            except BlobContainmentError:
                unsafe.append(key)
            except BlobIntegrityError:
                corrupt.append(key)

        stored, stored_unsafe = self._stored_keys()
        unsafe.extend(stored_unsafe)
        referenced_keys = set(unique_references)
        return BlobHealthReport(
            referenced_objects=len(unique_references),
            stored_objects=len(stored),
            staged_objects=self.staged_count(),
            missing_keys=tuple(sorted(set(missing))),
            corrupt_keys=tuple(sorted(set(corrupt))),
            orphan_keys=tuple(sorted(stored - referenced_keys)),
            unsafe_keys=tuple(sorted(set(unsafe))),
        )

    def staged_count(self) -> int:
        self._ensure_private_directory(self._staging_root)
        count = 0
        for path in self._staging_root.iterdir():
            if path.is_symlink():
                raise BlobContainmentError("staging directory contains a symlink")
            if path.is_file() and path.name.endswith(".stage"):
                count += 1
        return count

    def _path_for_reference(
        self,
        reference: BlobReference,
        *,
        require_exists: bool,
    ) -> Path:
        validate_sha256(reference.sha256)
        if (
            not isinstance(reference.byte_count, int)
            or isinstance(reference.byte_count, bool)
            or reference.byte_count < 0
        ):
            raise BlobValidationError("blob byte count must be non-negative")
        validate_media_type(reference.media_type)
        match = _STORAGE_KEY.fullmatch(reference.storage_key)
        expected_key = storage_key_for_sha256(reference.sha256)
        if match is None or reference.storage_key != expected_key:
            raise BlobContainmentError(
                "blob storage key is not the generated content-addressed key"
            )
        if match.groups() != (
            reference.sha256[:2],
            reference.sha256[2:4],
            reference.sha256,
        ):
            raise BlobContainmentError("blob storage key hash path is inconsistent")
        path = self.root.joinpath(*reference.storage_key.split("/"))
        self._reject_symlinks_below_root(path.parent)
        if require_exists:
            _reject_symlink(path)
            if not path.exists():
                raise FileNotFoundError(reference.storage_key)
        return path

    def _stage_path(self, token: str) -> Path:
        if _STAGE_TOKEN.fullmatch(token) is None:
            raise BlobContainmentError("invalid staged blob token")
        self._ensure_private_directory(self._staging_root)
        return self._staging_root / f"{token}.stage"

    def _require_owned_stage(self, staged: StagedBlob) -> None:
        if not isinstance(staged, StagedBlob) or self._staged.get(staged.token) != staged:
            raise BlobValidationError("blob is not staged by this store")
        if staged.storage_key != storage_key_for_sha256(staged.sha256):
            raise BlobContainmentError("staged blob key is not generated from its hash")
        validate_media_type(staged.media_type)

    def _ensure_private_directory(self, path: Path) -> None:
        self._reject_symlinks_below_root(path.parent)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _reject_symlink(path)
        if not path.is_dir():
            raise BlobContainmentError("blob path component is not a directory")
        self._chmod_private(path, 0o700)

    def _reject_symlinks_below_root(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise BlobContainmentError("blob path escapes the configured root") from exc
        current = self.root
        _reject_symlink(current)
        for part in relative.parts:
            current = current / part
            try:
                _reject_symlink(current)
            except FileNotFoundError:
                break

    def _stored_keys(self) -> tuple[set[str], list[str]]:
        object_root = self.root / "sha256"
        try:
            _reject_symlink(object_root)
        except FileNotFoundError:
            return set(), []
        if not object_root.is_dir():
            return set(), ["sha256"]
        stored: set[str] = set()
        unsafe: list[str] = []
        for first in object_root.iterdir():
            first_key = f"sha256/{first.name}"
            if first.is_symlink() or not first.is_dir():
                unsafe.append(first_key)
                continue
            if _has_group_or_other_permissions(first.lstat().st_mode):
                unsafe.append(first_key)
            for second in first.iterdir():
                second_key = f"{first_key}/{second.name}"
                if second.is_symlink() or not second.is_dir():
                    unsafe.append(second_key)
                    continue
                if _has_group_or_other_permissions(second.lstat().st_mode):
                    unsafe.append(second_key)
                for candidate in second.iterdir():
                    key = f"{second_key}/{candidate.name}"
                    if candidate.is_symlink() or not candidate.is_file():
                        unsafe.append(key)
                        continue
                    match = _STORAGE_KEY.fullmatch(key)
                    if (
                        match is None
                        or match.group(1) != candidate.name[:2]
                        or match.group(2) != candidate.name[2:4]
                    ):
                        unsafe.append(key)
                        continue
                    stored.add(key)
                    if _has_group_or_other_permissions(candidate.lstat().st_mode):
                        unsafe.append(key)
        return stored, unsafe

    @staticmethod
    def _digest_regular_file(path: Path) -> tuple[str, int]:
        descriptor = os.open(path, _READ_FLAGS)
        digest = sha256_digest()
        byte_count = 0
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise BlobContainmentError("blob path is not a regular file")
            _require_private_file_mode(file_stat.st_mode)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    byte_count += len(chunk)
        finally:
            os.close(descriptor)
        return digest.hexdigest(), byte_count

    @staticmethod
    def _chmod_private(path: Path, mode: int) -> None:
        if os.name != "nt":
            path.chmod(mode, follow_symlinks=False)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _reject_existing_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            _reject_symlink(current)
        except FileNotFoundError:
            break


def _reject_symlink(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(mode):
        raise BlobContainmentError("blob storage symlink escape is not permitted")


def _has_group_or_other_permissions(mode: int) -> bool:
    return os.name != "nt" and bool(stat.S_IMODE(mode) & 0o077)


def _require_private_file_mode(mode: int) -> None:
    if _has_group_or_other_permissions(mode):
        raise BlobContainmentError("blob file permissions are not private")
