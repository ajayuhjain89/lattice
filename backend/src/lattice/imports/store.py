"""The import store (phase-12-plan C8.1): where uploaded exports live, by opaque ID.

``ImportStore`` is the contract; ``FilesystemImportStore`` its first backend (an object
store can implement the same calls later without touching ingestion: the worker only
needs a read-only directory for the connector, which an object backend would stage).

Layout under the operator-configured root (``LATTICE_IMPORTS_ROOT``), every name derived
from IDs the database generated -- never from a file name, a client path or a header::

    <root>/<organization uuid>/<import uuid>/archive.partial   while the body streams
                                             archive           sealed (size, SHA-256)
                                             staging/          extraction in progress
                                             tree/             validated, published

* A tree appears only by one ``rename`` of a fully validated staging directory; a failed
  or interrupted upload never leaves a usable tree.
* Directories 0750, files 0640, owned by the service user; no execute bits, nothing is
  ever served back to a browser, and the web tier has no access.
* An organization's imports never leave its directory: both IDs are UUIDs (hex and
  dashes), checked again here, and every resolved path is confirmed under the root.
* Removal (``delete``) uses ``shutil.rmtree``'s descriptor-based walk, which does not
  follow symlinks.
"""

import contextlib
import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from lattice.imports.archive import JSONL_NAME, Extracted, extract_zip, storage_or_invalid
from lattice.imports.limits import ImportLimits, ImportRejected

_PARTIAL: Final = "archive.partial"
_SEALED: Final = "archive"
_STAGING: Final = "staging"
_TREE: Final = "tree"


class ImportUnavailable(Exception):
    """The import's tree is not there (not ready, removed, or the store is not mounted)."""


class UploadWriter(Protocol):
    def append(self, data: bytes) -> None: ...
    @property
    def size(self) -> int: ...
    def seal(self) -> tuple[int, bytes]: ...
    def abort(self) -> None: ...


class ImportStore(Protocol):
    backend: str

    def free_bytes(self) -> int: ...
    def begin(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> UploadWriter: ...
    def extract(
        self,
        organization_id: uuid.UUID,
        import_id: uuid.UUID,
        *,
        connector_type: str,
        fmt: str,
        limits: ImportLimits,
    ) -> Extracted: ...
    def tree(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> Path: ...
    def delete(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> None: ...
    def organizations(self) -> list[uuid.UUID]: ...
    def stored(self, organization_id: uuid.UUID) -> list[tuple[uuid.UUID, float]]: ...


def _uuid(value: uuid.UUID) -> str:
    if not isinstance(value, uuid.UUID):
        raise TypeError("import store locations are derived from UUIDs only")
    return str(value)


class _FileWriter:
    """Streams the body into ``archive.partial``, hashing as it arrives; ``seal`` renames
    it to ``archive``. Not thread-safe: one request writes one upload."""

    def __init__(self, directory: Path, limit: int) -> None:
        self.directory = directory
        self.limit = limit
        self._size = 0
        self._digest = hashlib.sha256()
        self._fd = os.open(
            directory / _PARTIAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640
        )
        os.fchmod(self._fd, 0o640)

    @property
    def size(self) -> int:
        return self._size

    def append(self, data: bytes) -> None:
        if self._size + len(data) > self.limit:
            raise ImportRejected("too_large")
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(self._fd, view) :]
        except OSError as exc:
            raise ImportRejected(storage_or_invalid(exc)) from None
        self._size += len(data)
        self._digest.update(data)

    def seal(self) -> tuple[int, bytes]:
        try:
            os.fsync(self._fd)
        except OSError as exc:
            raise ImportRejected(storage_or_invalid(exc)) from None
        finally:
            self._close()
        os.rename(self.directory / _PARTIAL, self.directory / _SEALED)
        return self._size, self._digest.digest()

    def abort(self) -> None:
        self._close()
        (self.directory / _PARTIAL).unlink(missing_ok=True)

    def _close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


@dataclass
class FilesystemImportStore:
    """The filesystem backend. ``root`` is a directory the API (read-write) and the worker
    (read, and delete for retention) share; nothing else mounts it."""

    root: Path
    max_upload_bytes: int = ImportLimits().max_upload_bytes
    backend: str = "filesystem"

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError("the imports root must be an absolute path")

    # --- locations ---------------------------------------------------------------------

    def _org_dir(self, organization_id: uuid.UUID, *, create: bool) -> Path:
        if not self.root.is_dir():
            raise ImportUnavailable("imports root missing")
        path = self.root / _uuid(organization_id)
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir(path, 0o750)
        self._confined(path)
        return path

    def _import_dir(
        self, organization_id: uuid.UUID, import_id: uuid.UUID, *, create: bool = False
    ) -> Path:
        name = _uuid(import_id)  # checked before anything touches the filesystem
        path = self._org_dir(organization_id, create=create) / name
        if create:
            os.mkdir(path, 0o750)  # a fresh import: it must not exist yet
        self._confined(path)
        return path

    def _confined(self, path: Path) -> None:
        """The path is a real directory directly under the root's tree (no link anywhere)."""
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise ImportUnavailable("missing") from None
        if not stat.S_ISDIR(info.st_mode):
            raise ImportUnavailable("not a directory")
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ImportUnavailable("outside the imports root")

    # --- the contract ------------------------------------------------------------------

    def free_bytes(self) -> int:
        if not self.root.is_dir():
            raise ImportUnavailable("imports root missing")
        return shutil.disk_usage(self.root).free

    def begin(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> UploadWriter:
        directory = self._import_dir(organization_id, import_id, create=True)
        return _FileWriter(directory, self.max_upload_bytes)

    def extract(
        self,
        organization_id: uuid.UUID,
        import_id: uuid.UUID,
        *,
        connector_type: str,
        fmt: str,
        limits: ImportLimits,
    ) -> Extracted:
        """Validate the sealed archive into ``staging/`` and publish it as ``tree/`` with
        one rename; the archive is removed either way (the tree is what syncs read)."""
        directory = self._import_dir(organization_id, import_id)
        sealed = directory / _SEALED
        staging = directory / _STAGING
        os.mkdir(staging, 0o750)
        try:
            if fmt == "zip":
                result = extract_zip(sealed, staging, connector_type, limits)
            else:
                result = self._place_jsonl(sealed, staging, limits)
            os.rename(staging, directory / _TREE)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            sealed.unlink(missing_ok=True)
        return result

    @staticmethod
    def _place_jsonl(sealed: Path, staging: Path, limits: ImportLimits) -> Extracted:
        """A Tickets upload of one JSON Lines page: stored under a fixed name."""
        size = os.lstat(sealed).st_size
        if size > limits.max_file_bytes:
            raise ImportRejected("too_large")
        if size == 0:
            raise ImportRejected("unsupported_layout")
        target = staging / JSONL_NAME
        os.rename(sealed, target)
        os.chmod(target, 0o640)
        return Extracted(entries=1, ignored=0, expanded_bytes=size)

    def tree(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> Path:
        """The read-only directory the connector reads (``ImportUnavailable`` if absent)."""
        directory = self._import_dir(organization_id, import_id)
        tree = directory / _TREE
        self._confined(tree)
        return tree

    def delete(self, organization_id: uuid.UUID, import_id: uuid.UUID) -> None:
        try:
            directory = self._import_dir(organization_id, import_id)
        except ImportUnavailable:
            return
        shutil.rmtree(directory, ignore_errors=True)

    def organizations(self) -> list[uuid.UUID]:
        """Organization directories present (for the worker's orphan sweep)."""
        if not self.root.is_dir():
            return []
        found: list[uuid.UUID] = []
        for entry in os.scandir(self.root):
            try:
                value = uuid.UUID(entry.name)
            except ValueError:
                continue
            if entry.is_dir(follow_symlinks=False) and str(value) == entry.name:
                found.append(value)
        return sorted(found)

    def stored(self, organization_id: uuid.UUID) -> list[tuple[uuid.UUID, float]]:
        """(import ID, modification time) of every import directory of an organization."""
        try:
            directory = self._org_dir(organization_id, create=False)
        except ImportUnavailable:
            return []
        found: list[tuple[uuid.UUID, float]] = []
        for entry in os.scandir(directory):
            try:
                value = uuid.UUID(entry.name)
            except ValueError:
                continue
            if entry.is_dir(follow_symlinks=False) and str(value) == entry.name:
                found.append((value, entry.stat(follow_symlinks=False).st_mtime))
        return sorted(found)
