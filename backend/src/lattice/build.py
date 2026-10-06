"""Build identification for deployment traceability (Phase 11, D-27).

The image build bakes ``LATTICE_BUILD_REVISION`` (the git commit) and
``LATTICE_BUILD_VERSION`` (a release tag, when there is one) into the environment.
They are reported only where operators look -- the ``lattice_build_info`` metric on
the internal metrics listener and one ``startup`` log line per process -- and never on
a public endpoint (health endpoints report states only, Phase 8). Unset or malformed
values read ``unknown``: the values come from the build, not from users, but they are
still reduced to a short, safe alphabet before they become a metric label.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Final

from lattice import metrics
from lattice.db.schema import SCHEMA_REVISION
from lattice.log import log_event

UNKNOWN: Final = "unknown"
_SAFE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


@dataclass(frozen=True)
class BuildInfo:
    revision: str
    version: str
    schema_revision: str


def _clean(value: str | None) -> str:
    return value if value is not None and _SAFE.fullmatch(value) else UNKNOWN


def build_info() -> BuildInfo:
    return BuildInfo(
        revision=_clean(os.environ.get("LATTICE_BUILD_REVISION")),
        version=_clean(os.environ.get("LATTICE_BUILD_VERSION")),
        schema_revision=SCHEMA_REVISION,
    )


def report_startup(process: str, logger: logging.Logger) -> BuildInfo:
    """Set ``lattice_build_info`` and log one ``startup`` event for ``process``."""
    info = build_info()
    metrics.BUILD_INFO.labels(
        revision=info.revision, version=info.version, schema_revision=info.schema_revision
    ).set(1)
    log_event(
        logger,
        logging.INFO,
        "startup",
        process=process,
        revision=info.revision,
        version=info.version,
        schema_revision=info.schema_revision,
    )
    return info
