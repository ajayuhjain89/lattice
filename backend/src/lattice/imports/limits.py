"""Import limits (phase-12-plan C8.3, C8.4) and the fixed codes of a refused upload.

Defaults are derived from the reference machine, not from the contract's maxima (C8.3
named 200 MiB / 1 GiB / 50,000 entries / 100 MiB per file): the connectors read each file
whole into memory, and the reference stack runs API, worker and PostgreSQL inside a 3 GB
Docker VM on an 8 GB laptop. A 32 MiB file is a few hundred MB of parsed records at
most; 512 MiB expanded is far above the synthetic corpora (1.2 MB) and a sizeable real
export. Every limit is a setting (``LATTICE_IMPORT_*``) an operator may raise for a
larger host, within the hard ceilings below.
"""

from dataclasses import dataclass
from typing import Final, Literal

MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB

# Why an upload was refused. Fixed identifiers, shown to administrators with a fixed
# explanation; never a file name, a path or content.
RejectCode = Literal[
    "archive_invalid",  # not a readable zip, encrypted, unsupported compression, overlaps
    "too_large",  # the upload, the expanded total or one file is over its limit
    "too_many_entries",
    "ratio_exceeded",  # an entry expands more than the ratio allows (a zip bomb)
    "unsafe_path",  # absolute, drive letter, '..', backslash, empty or too deep, bad name
    "link_refused",  # a symlink, hard link, device, FIFO or socket entry
    "duplicate_name",  # the same path twice (also ignoring case or Unicode form)
    "unsupported_layout",  # not the structure this source type reads
    "upload_incomplete",  # the request ended before the declared body arrived
    "upload_timeout",
    "storage_full",  # the import area cannot hold it (or the organization's quota)
    "upload_abandoned",  # the request that received it died
    "upload_failed",  # the server could not finish receiving it (try again)
]
REJECT_CODES: Final[frozenset[str]] = frozenset(RejectCode.__args__)  # type: ignore[attr-defined]


class ImportRejected(Exception):
    """An upload refused for a fixed reason. Nothing of it stays available."""

    def __init__(self, code: RejectCode) -> None:
        super().__init__(code)
        self.code: RejectCode = code


@dataclass(frozen=True)
class ImportLimits:
    max_upload_bytes: int = 100 * MIB
    max_expanded_bytes: int = 512 * MIB
    max_entries: int = 20_000
    max_file_bytes: int = 32 * MIB
    # Per entry, checked while streaming (declared sizes are not trusted), once an entry
    # has produced more than ``ratio_floor_bytes`` (a small, very repetitive text file is
    # not a bomb).
    max_ratio: int = 100
    ratio_floor_bytes: int = 1 * MIB
    max_depth: int = 16
    max_component_bytes: int = 255
    max_path_bytes: int = 1024
    # All of an organization's retained imports (received and expanded bytes).
    organization_quota_bytes: int = 2 * GIB
    # Free space the import area must keep after reserving one upload's worst case.
    reserve_free_bytes: int = 256 * MIB

    def __post_init__(self) -> None:
        if not (1 * MIB <= self.max_upload_bytes <= 2 * GIB):
            raise ValueError("max_upload_bytes must be between 1 MiB and 2 GiB")
        if not (self.max_upload_bytes <= self.max_expanded_bytes <= 8 * GIB):
            raise ValueError("max_expanded_bytes must be >= the upload limit and <= 8 GiB")
        if not (1 <= self.max_entries <= 200_000):
            raise ValueError("max_entries must be between 1 and 200,000")
        if not (64 * 1024 <= self.max_file_bytes <= self.max_expanded_bytes):
            raise ValueError("max_file_bytes must be between 64 KiB and the expanded limit")
        if not (2 <= self.max_ratio <= 1000):
            raise ValueError("max_ratio must be between 2 and 1000")
        if self.organization_quota_bytes < self.max_upload_bytes + self.max_expanded_bytes:
            raise ValueError("the organization quota must hold at least one import")

    @property
    def worst_case_bytes(self) -> int:
        """Disk one upload may need at its peak (the archive and its expanded tree)."""
        return self.max_upload_bytes + self.max_expanded_bytes
