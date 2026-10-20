"""Archive validation and extraction (P12.6; phase-12-plan C8.3): every hostile shape is
refused with its fixed code before anything is written, only what the connector reads is
kept, and nothing escapes the staging directory."""

import os
import stat
import unicodedata
import zipfile
import zlib
from pathlib import Path

import pytest

from lattice.imports.archive import extract_zip
from lattice.imports.limits import MIB, ImportLimits, ImportRejected

from .archives import build, declared_entries, entry, files, overlapping, raw_name, zip_tree

DOC = (
    b"---\nid: d1\ntitle: T\nauthor: A\n"
    b"created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\nBody\n"
)
SLACK = {"users.json": b"[]", "channels.json": b"[]"}
LIMITS = ImportLimits()


def _extract(
    tmp_path: Path, data: bytes, kind: str = "document_repository", limits: ImportLimits = LIMITS
) -> tuple[Path, object]:
    archive = tmp_path / "archive"
    archive.write_bytes(data)
    staging = tmp_path / "staging"
    staging.mkdir()
    return staging, extract_zip(archive, staging, kind, limits)


def _refused(tmp_path: Path, data: bytes, code: str, **kwargs: object) -> None:
    with pytest.raises(ImportRejected) as raised:
        _extract(tmp_path, data, **kwargs)  # type: ignore[arg-type]
    assert raised.value.code == code
    # Nothing a refused entry named was written anywhere beside the staging directory.
    assert {p.name for p in tmp_path.iterdir()} <= {"archive", "staging"}


def _tree(staging: Path) -> set[str]:
    return {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}


# --- what is kept ------------------------------------------------------------------------


def test_documents_keep_only_markdown_at_any_depth(tmp_path: Path) -> None:
    staging, result = _extract(
        tmp_path,
        files(
            {
                "a.md": DOC,
                "deep/er/b.md": DOC,
                "image.png": b"\x89PNG",
                "nested.zip": files({"x.md": DOC}),
                "__MACOSX/._a.md": b"junk",
                ".DS_Store": b"junk",
            }
        ),
    )
    assert _tree(staging) == {"a.md", "deep/er/b.md"}
    assert result.entries == 2  # type: ignore[attr-defined]
    assert result.ignored == 4  # type: ignore[attr-defined]
    assert result.expanded_bytes == 2 * len(DOC)  # type: ignore[attr-defined]


def test_tickets_keep_top_level_pages_only(tmp_path: Path) -> None:
    staging, _ = _extract(
        tmp_path,
        files({"tickets.jsonl": b"{}\n", "more/tickets.jsonl": b"{}\n", "notes.txt": b"n"}),
        "ticketing",
    )
    assert _tree(staging) == {"tickets.jsonl"}


def test_slack_keeps_metadata_and_day_files(tmp_path: Path) -> None:
    staging, result = _extract(
        tmp_path,
        files(
            {
                **SLACK,
                "groups.json": b"[]",
                "dms.json": b"[]",
                "general/2026-01-01.json": b"[]",
                "general/files/x.json": b"[]",
                "integration_logs.json": b"[]",
            }
        ),
        "chat_export",
    )
    assert _tree(staging) == {
        "users.json",
        "channels.json",
        "groups.json",
        "general/2026-01-01.json",
    }
    assert result.ignored == 3  # type: ignore[attr-defined]


def test_one_wrapping_folder_is_the_export_root(tmp_path: Path) -> None:
    root = tmp_path / "export"
    (root / "general").mkdir(parents=True)
    for name, data in SLACK.items():
        (root / name).write_bytes(data)
    (root / "general" / "2026-01-01.json").write_bytes(b"[]")
    staging, _ = _extract(tmp_path, zip_tree(root, wrapper="halden-slack"), "chat_export")
    assert _tree(staging) == {"users.json", "channels.json", "general/2026-01-01.json"}


def test_written_files_are_0640_and_never_executable(tmp_path: Path) -> None:
    staging, _ = _extract(tmp_path, build(entry("run.md", DOC, mode=stat.S_IFREG | 0o4777)))
    mode = os.stat(staging / "run.md").st_mode
    assert stat.S_IMODE(mode) == 0o640
    assert stat.S_ISREG(mode)


@pytest.mark.parametrize(
    ("kind", "names"),
    [
        ("document_repository", {"readme.txt": b"t"}),
        ("ticketing", {"a/tickets.jsonl": b"{}", "b/tickets.jsonl": b"{}"}),
        ("chat_export", {"users.json": b"[]", "general/2026-01-01.json": b"[]"}),
    ],
)
def test_an_archive_without_the_expected_structure_is_refused(
    tmp_path: Path, kind: str, names: dict[str, bytes]
) -> None:
    _refused(tmp_path, files(names), "unsupported_layout", kind=kind)


# --- paths -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.md",
        "a/../../escape.md",
        "/etc/escape.md",
        "C:/escape.md",
        "a\\..\\escape.md",
        "a//b.md",
        "./a.md",
        "a/./b.md",
        "/".join(["d"] * 17) + ".md",
        ("x" * 256) + ".md",
        "tab\there.md",
        "bell\x07.md",
    ],
)
def test_unsafe_paths_are_refused_before_anything_is_written(tmp_path: Path, name: str) -> None:
    _refused(tmp_path, files({"ok.md": DOC, name: DOC}), "unsafe_path")
    assert not (tmp_path / "staging" / "ok.md").exists()


def test_a_nul_byte_in_a_name_is_refused(tmp_path: Path) -> None:
    data = raw_name(files({"aXb.md": DOC}), b"aXb.md", b"a\x00b.md")
    _refused(tmp_path, data, "unsafe_path")


def test_a_name_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    data = raw_name(files({"aXYb.md": DOC}), b"aXYb.md", b"a\xff\xfeb.md")
    _refused(tmp_path, data, "unsafe_path")


@pytest.mark.parametrize(
    "mode",
    [
        stat.S_IFLNK | 0o777,
        stat.S_IFCHR | 0o600,
        stat.S_IFBLK | 0o600,
        stat.S_IFIFO | 0o600,
        stat.S_IFSOCK | 0o600,
    ],
)
def test_links_and_special_files_are_refused(tmp_path: Path, mode: int) -> None:
    _refused(
        tmp_path,
        build(entry("a.md", DOC), entry("link.md", b"/etc/passwd", mode=mode)),
        "link_refused",
    )


def test_a_windows_reparse_point_is_refused(tmp_path: Path) -> None:
    info, data = entry("link.md", DOC)
    info.create_system = 0
    info.external_attr = 0x400
    _refused(tmp_path, build((info, data)), "link_refused")


@pytest.mark.parametrize(
    "names",
    [
        ["a.md", "a.md"],
        ["Report.md", "report.md"],
        [unicodedata.normalize("NFC", "café.md"), unicodedata.normalize("NFD", "café.md")],
        ["notes", "notes/a.md"],
        ["dir/", "DIR"],
    ],
)
def test_duplicate_and_conflicting_names_are_refused(tmp_path: Path, names: list[str]) -> None:
    data = build(*(entry(n, DOC) for n in names))
    _refused(tmp_path, data, "duplicate_name")


# --- archive shape and size ----------------------------------------------------------------


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04not really", os.urandom(4096)])
def test_malformed_archives_are_refused(tmp_path: Path, data: bytes) -> None:
    _refused(tmp_path, data, "archive_invalid")


def test_a_truncated_archive_is_refused(tmp_path: Path) -> None:
    data = files({"a.md": DOC * 1000})
    _refused(tmp_path, data[: len(data) // 2], "archive_invalid")


def test_encrypted_entries_are_refused(tmp_path: Path) -> None:
    info, data = entry("a.md", DOC)
    archive = bytearray(build((info, data)))
    # The general-purpose flag's encryption bit, in the local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = archive.find(signature)
        archive[at + offset] |= 0x01
    _refused(tmp_path, bytes(archive), "archive_invalid")


def test_unusual_compression_methods_are_refused(tmp_path: Path) -> None:
    info, data = entry("a.md", DOC)
    info.compress_type = zipfile.ZIP_BZIP2
    _refused(tmp_path, build((info, data)), "archive_invalid")


def test_a_corrupted_entry_fails_its_crc(tmp_path: Path) -> None:
    info, data = entry("a.md", DOC * 50)
    info.compress_type = zipfile.ZIP_STORED
    archive = bytearray(build((info, data)))
    at = archive.find(b"Body")
    archive[at] ^= 0xFF
    _refused(tmp_path, bytes(archive), "archive_invalid")


def test_a_compression_bomb_is_refused_by_ratio(tmp_path: Path) -> None:
    bomb = b"\x00" * (8 * MIB)
    assert len(zlib.compress(bomb)) * 100 < len(bomb)
    _refused(tmp_path, files({"bomb.md": bomb}), "ratio_exceeded")


def test_overlapping_entries_are_refused(tmp_path: Path) -> None:
    _refused(tmp_path, overlapping("a.md", "b.md", DOC), "archive_invalid")


def test_one_file_over_the_limit_is_refused(tmp_path: Path) -> None:
    limits = ImportLimits(max_file_bytes=64 * 1024, max_ratio=1000)
    _refused(tmp_path, files({"big.md": os.urandom(70 * 1024)}), "too_large", limits=limits)


def test_the_expanded_total_is_bounded(tmp_path: Path) -> None:
    limits = ImportLimits(
        max_upload_bytes=1 * MIB, max_expanded_bytes=1 * MIB, max_file_bytes=1 * MIB
    )
    names = {f"{i}.md": os.urandom(300 * 1024) for i in range(4)}
    _refused(tmp_path, files(names), "too_large", limits=limits)


def test_too_many_entries_are_refused_from_the_end_record(tmp_path: Path) -> None:
    limits = ImportLimits(max_entries=10)
    _refused(
        tmp_path, files({f"{i}.md": DOC for i in range(11)}), "too_many_entries", limits=limits
    )
    # The end record is read before the central directory: a claimed count over the limit
    # is refused without parsing the directory (it is never believed to accept).
    second = tmp_path / "second"
    second.mkdir()
    _refused(
        second, declared_entries(files({"a.md": DOC}), 60_000), "too_many_entries", limits=limits
    )


def test_a_declared_size_smaller_than_the_data_is_caught(tmp_path: Path) -> None:
    """Declared sizes are not trusted: the reader stops at the declared size, and the CRC
    of what it read does not match."""
    info, data = entry("a.md", DOC * 100)
    info.compress_type = zipfile.ZIP_STORED
    archive = bytearray(build((info, data)))
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        at = archive.find(signature)
        archive[at + offset : at + offset + 4] = (10).to_bytes(4, "little")
    _refused(tmp_path, bytes(archive), "archive_invalid")
