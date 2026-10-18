"""Archive validation and extraction (phase-12-plan C8.3).

Untrusted input, so nothing in an archive is believed until checked:

1. **Before opening**: the end-of-central-directory record's entry count (zip64 too) is
   compared with the limit, so a central directory of a million entries is never parsed.
2. **Every entry, before anything is written** (also entries this source type never
   reads): a UTF-8 name of at most ``max_path_bytes``, no control characters, no
   backslash, not absolute, no drive letter, no empty / ``.`` / ``..`` component, at most
   ``max_depth`` components of at most ``max_component_bytes`` each; no symlink, hard
   link, device, FIFO or socket (unix mode bits; a Windows reparse point); not
   encrypted; stored or deflated only; no two entries with the same path, also ignoring
   case and Unicode normal form, and no file where another entry needs a directory; no
   entry whose data overlaps another's (the overlapping-file bomb); declared sizes and
   ratios within the limits.
3. **Only what the connector reads is written**: Markdown files for a Documents source,
   top-level ``.jsonl`` pages for Tickets, ``users.json`` / ``channels.json`` /
   ``groups.json`` and ``<channel>/<day>.json`` for a Slack export. Everything else
   (attachments, ``__MACOSX``, nested archives) is counted as ignored and never
   expanded. A single wrapping folder is treated as the export's root.
4. **Extraction is entry by entry, streamed** (never ``extractall``): each file is created
   exclusively (``O_EXCL | O_NOFOLLOW``, mode 0640) under a fresh staging directory that
   only this process created, while bytes, the total and the ratio are counted (declared
   sizes are not trusted; the CRC is verified by the zip reader). Any failure removes the
   staging directory; the caller publishes the tree with one rename only on success.

Nothing here returns a name from the archive: results are counts, refusals are fixed codes.
"""

import errno
import itertools
import os
import stat
import struct
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from lattice.imports.limits import ImportLimits, ImportRejected, RejectCode

_EOCD = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD = b"PK\x06\x06"
_LOCAL = b"PK\x03\x04"
_LOCAL_HEADER = struct.Struct("<4s2B4HL2L2H")
_CHUNK: Final = 1024 * 1024
_CONTROL = frozenset(range(0x00, 0x20)) | {0x7F}
_UNIX = 3
_DOS_REPARSE_POINT = 0x400
_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_SLACK_ROOT_FILES = frozenset({"users.json", "channels.json", "groups.json"})
ZIP_MAGIC: Final = (b"PK\x03\x04", b"PK\x05\x06")
# The single file a Tickets upload of one .jsonl page is stored as (never the client's).
JSONL_NAME: Final = "export.jsonl"


@dataclass(frozen=True)
class Extracted:
    entries: int  # files written to the tree
    ignored: int  # entries this source type never reads (not written)
    expanded_bytes: int


@dataclass(frozen=True)
class _Entry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


def is_zip(head: bytes) -> bool:
    return head[:4] in ZIP_MAGIC


def _declared_entries(path: Path) -> int:
    """The entry count the end-of-central-directory record declares (zip64 aware)."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        tail_size = min(size, 65_557)  # EOCD (22) + the longest comment (65,535)
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
        at = tail.rfind(_EOCD)
        if at < 0 or len(tail) - at < 22:
            raise ImportRejected("archive_invalid")
        (total,) = struct.unpack_from("<H", tail, at + 10)
        if total != 0xFFFF:
            return int(total)
        locator = at - 20
        if locator < 0 or tail[locator : locator + 4] != _ZIP64_LOCATOR:
            raise ImportRejected("archive_invalid")
        (offset,) = struct.unpack_from("<Q", tail, locator + 8)
        if offset > size - 56:
            raise ImportRejected("archive_invalid")
        handle.seek(offset)
        record = handle.read(56)
        if record[:4] != _ZIP64_EOCD:
            raise ImportRejected("archive_invalid")
        (total64,) = struct.unpack_from("<Q", record, 32)
        return int(total64)


def _name(info: zipfile.ZipInfo) -> str:
    """The entry's name as stored, decoded strictly as UTF-8 (zipfile decodes names
    without the UTF-8 flag as cp437; those must be ASCII or valid UTF-8 bytes)."""
    raw = info.orig_filename
    if not info.flag_bits & 0x800:
        try:
            raw = raw.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            raise ImportRejected("unsafe_path") from None
    return raw


def _parts(name: str, limits: ImportLimits) -> tuple[tuple[str, ...], bool]:
    encoded = name.encode("utf-8", "surrogatepass")
    if not name or len(encoded) > limits.max_path_bytes:
        raise ImportRejected("unsafe_path")
    if any(b in _CONTROL for b in encoded) or "\\" in name:
        raise ImportRejected("unsafe_path")
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":" and name[0].isalpha()):
        raise ImportRejected("unsafe_path")
    is_dir = name.endswith("/")
    parts = tuple(name[:-1].split("/") if is_dir else name.split("/"))
    if len(parts) > limits.max_depth:
        raise ImportRejected("unsafe_path")
    for part in parts:
        if part in ("", ".", "..") or len(part.encode("utf-8")) > limits.max_component_bytes:
            raise ImportRejected("unsafe_path")
    return parts, is_dir


def _kind(info: zipfile.ZipInfo, is_dir: bool) -> bool:
    """True for a directory, False for a regular file; refuses every other kind."""
    if info.create_system == _UNIX:
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFDIR:
            return True
        if kind not in (0, stat.S_IFREG):
            raise ImportRejected("link_refused")  # symlink, device, FIFO, socket
    if info.external_attr & _DOS_REPARSE_POINT:
        raise ImportRejected("link_refused")
    return is_dir


def _key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", p).casefold() for p in parts)


def _entries(archive: zipfile.ZipFile, limits: ImportLimits) -> list[_Entry]:
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        raise ImportRejected("too_many_entries")
    entries: list[_Entry] = []
    files: set[tuple[str, ...]] = set()
    dirs: set[tuple[str, ...]] = set()
    for info in infos:
        if info.flag_bits & 0x1 or info.compress_type not in _COMPRESSION:
            raise ImportRejected("archive_invalid")  # encrypted, or an unusual codec
        parts, is_dir = _parts(_name(info), limits)
        is_dir = _kind(info, is_dir)
        key = _key(parts)
        parents = {key[:i] for i in range(1, len(key))}
        if is_dir:
            if key in files:
                raise ImportRejected("duplicate_name")
            dirs.add(key)
        else:
            if key in files or key in dirs:
                raise ImportRejected("duplicate_name")
            files.add(key)
        if parents & files:
            raise ImportRejected("duplicate_name")  # a file where a directory is needed
        dirs |= parents
        entries.append(_Entry(info, parts, is_dir))
    return entries


def _check_layout_of_data(path: Path, entries: list[_Entry], central_directory: int) -> None:
    """Refuse entries whose compressed data overlap (or run into the central directory):
    an archive whose entries share their data expands without bound."""
    spans: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        for entry in entries:
            info = entry.info
            handle.seek(info.header_offset)
            header = handle.read(_LOCAL_HEADER.size)
            if len(header) != _LOCAL_HEADER.size:
                raise ImportRejected("archive_invalid")
            fields = _LOCAL_HEADER.unpack(header)
            if fields[0] != _LOCAL:
                raise ImportRejected("archive_invalid")
            start = info.header_offset + _LOCAL_HEADER.size + fields[10] + fields[11]
            spans.append((info.header_offset, start + info.compress_size))
    spans.sort()
    for (_, end), (next_start, _) in itertools.pairwise(spans):
        if end > next_start:
            raise ImportRejected("archive_invalid")
    if spans and spans[-1][1] > central_directory:
        raise ImportRejected("archive_invalid")


def _junk(parts: tuple[str, ...]) -> bool:
    return any(p == "__MACOSX" or p == ".DS_Store" or p.startswith("._") for p in parts)


def _strip_wrapper(entries: list[_Entry]) -> list[tuple[_Entry, tuple[str, ...]]]:
    """Each file entry with its path inside the export: a single folder that wraps every
    file (an export zipped from its parent directory) is the export's root."""
    files = [e for e in entries if not e.is_dir and not _junk(e.parts)]
    roots = {e.parts[0] for e in files}
    wrapped = len(roots) == 1 and all(len(e.parts) > 1 for e in files)
    return [(e, e.parts[1:] if wrapped else e.parts) for e in files]


def _reads(connector_type: str, parts: tuple[str, ...]) -> bool:
    """Whether the source type's connector reads this file (connector-design §3)."""
    name = parts[-1]
    if connector_type == "document_repository":
        return name.endswith(".md")
    if connector_type == "ticketing":
        return len(parts) == 1 and name.endswith(".jsonl")
    return (len(parts) == 1 and name in _SLACK_ROOT_FILES) or (
        len(parts) == 2 and name.endswith(".json")
    )


def _layout_ok(connector_type: str, accepted: list[tuple[str, ...]]) -> bool:
    if connector_type == "chat_export":
        root = {p[0] for p in accepted if len(p) == 1}
        return {"users.json", "channels.json"} <= root
    return bool(accepted)


def _mkdirs(staging: Path, parts: tuple[str, ...]) -> Path:
    """Create the parent directories under ``staging`` (which this process made), never
    following a link."""
    current = staging
    for part in parts:
        current = current / part
        try:
            os.mkdir(current, 0o750)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise ImportRejected("duplicate_name") from None
    return current


def _stream(source: IO[bytes], chunk: int) -> Iterator[bytes]:
    while block := source.read(chunk):
        yield block


def extract_zip(
    archive_path: Path, staging: Path, connector_type: str, limits: ImportLimits
) -> Extracted:
    """Validate ``archive_path`` and write what the connector reads into ``staging`` (a
    new, empty directory). Raises ``ImportRejected``; the caller removes ``staging``."""
    if _declared_entries(archive_path) > limits.max_entries:
        raise ImportRejected("too_many_entries")
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError):
        raise ImportRejected("archive_invalid") from None
    with archive:
        entries = _entries(archive, limits)
        _check_layout_of_data(archive_path, [e for e in entries if not e.is_dir], archive.start_dir)
        placed = _strip_wrapper(entries)
        accepted = [(e, p) for e, p in placed if p and _reads(connector_type, p)]
        if not _layout_ok(connector_type, [p for _, p in accepted]):
            raise ImportRejected("unsupported_layout")
        declared = 0
        for entry, _ in accepted:
            size, packed = entry.info.file_size, max(entry.info.compress_size, 1)
            if size > limits.max_file_bytes:
                raise ImportRejected("too_large")
            if size > limits.ratio_floor_bytes and size > limits.max_ratio * packed:
                raise ImportRejected("ratio_exceeded")
            declared += size
        if declared > limits.max_expanded_bytes:
            raise ImportRejected("too_large")

        total = 0
        for entry, parts in accepted:
            target = _mkdirs(staging, parts[:-1]) / parts[-1]
            packed = max(entry.info.compress_size, 1)
            written = 0
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
            except FileExistsError:
                raise ImportRejected("duplicate_name") from None
            try:
                os.fchmod(fd, 0o640)
                with archive.open(entry.info) as source:
                    for block in _stream(source, _CHUNK):
                        written += len(block)
                        total += len(block)
                        if written > limits.max_file_bytes or total > limits.max_expanded_bytes:
                            raise ImportRejected("too_large")
                        if (
                            written > limits.ratio_floor_bytes
                            and written > limits.max_ratio * packed
                        ):
                            raise ImportRejected("ratio_exceeded")
                        view = memoryview(block)
                        while view:
                            view = view[os.write(fd, view) :]
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                raise ImportRejected("archive_invalid") from None
            except OSError as exc:
                raise ImportRejected(storage_or_invalid(exc)) from None
            finally:
                os.close(fd)
    return Extracted(
        entries=len(accepted), ignored=len(entries) - len(accepted), expanded_bytes=total
    )


def storage_or_invalid(exc: OSError) -> RejectCode:
    """A full or quota-limited disk is ``storage_full``; any other I/O error on our own
    staging files is treated as an unusable archive."""
    return "storage_full" if exc.errno in (errno.ENOSPC, errno.EDQUOT) else "archive_invalid"
