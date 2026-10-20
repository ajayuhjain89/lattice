"""Building hostile and well-formed zip archives for the import tests (P12.6)."""

import io
import stat
import struct
import zipfile
from pathlib import Path


def entry(
    name: str, data: bytes = b"x", *, mode: int | None = None
) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = ((mode if mode is not None else stat.S_IFREG | 0o644) & 0xFFFF) << 16
    return info, data


def build(*entries: tuple[zipfile.ZipInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def files(mapping: dict[str, bytes]) -> bytes:
    return build(*(entry(name, data) for name, data in mapping.items()))


def zip_tree(root: Path, *, wrapper: str | None = None) -> bytes:
    """A directory as a deflated zip (optionally inside one wrapping folder)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                archive.write(path, f"{wrapper}/{name}" if wrapper else name)
    return buffer.getvalue()


def raw_name(data: bytes, old: bytes, new: bytes) -> bytes:
    """Replace a stored entry name (same length) in both its headers: names zipfile would
    refuse to write (backslashes, NUL, invalid UTF-8)."""
    assert len(old) == len(new)
    return data.replace(old, new)


def overlapping(name_a: str, name_b: str, payload: bytes) -> bytes:
    """Two central-directory entries that point at the same local data (the overlapping-
    file bomb): built from a valid one-entry archive by duplicating its central record."""
    single = build(entry(name_a, payload))
    eocd = single.rfind(b"PK\x05\x06")
    (cd_size, cd_offset) = struct.unpack_from("<LL", single, eocd + 12)
    central = single[cd_offset : cd_offset + cd_size]
    second = central.replace(name_a.encode(), name_b.encode())
    new_cd = central + second
    eocd_record = bytearray(single[eocd:])
    struct.pack_into("<HHLL", eocd_record, 8, 2, 2, len(new_cd), cd_offset)
    return single[:cd_offset] + new_cd + bytes(eocd_record)


def declared_entries(data: bytes, total: int) -> bytes:
    """An archive whose end record claims ``total`` entries (no central directory parse)."""
    eocd = data.rfind(b"PK\x05\x06")
    record = bytearray(data)
    struct.pack_into("<HH", record, eocd + 8, min(total, 0xFFFF), min(total, 0xFFFF))
    return bytes(record)
