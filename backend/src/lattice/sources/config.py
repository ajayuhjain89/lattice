"""Lattice-side source configuration (``sources.yaml``): how an organization's sources
are set up in Lattice -- defaults for access metadata, the ticketing organization ->
account mapping, per-channel account and sensitivity settings.

This is administrator configuration, not source data: connectors never see it. The
ingestion access mapping applies it to the untrusted hints a connector reports
(docs/ingestion-design.md §6). The synthetic dataset's lint validates the same files
with these models, so the lint and ingestion cannot disagree about their meaning.

Configuration never carries secrets; future API-backed connectors take credentials from
the environment or a secret store.
"""

import re
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ConnectorType = Literal["document_repository", "ticketing", "chat_export"]
Authority = Literal["system_of_record", "working", "conversational"]
DefaultLevel = Literal["internal", "confidential"]

_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChannelConfig(_Strict):
    account: str | None = None
    sensitivity: DefaultLevel | None = None


class SourceConfig(_Strict):
    key: str
    connector_type: ConnectorType
    name: str = Field(min_length=1)
    # Location of the export, relative to the configuration file.
    path: str
    default_sensitivity: DefaultLevel
    default_departments: list[str] = Field(default_factory=list)
    # If true, every record must map to an account or it is not indexed (fail closed).
    account_scoped: bool = False
    authority: Authority
    # ticketing: the ticketing tool's organization ID -> account key.
    account_refs: dict[int, str] = Field(default_factory=dict)
    # chat_export: channel ID -> settings.
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("must be lowercase letters, digits and hyphens (max 63)")
        return value

    @model_validator(mode="after")
    def _settings_fit_the_connector(self) -> Self:
        if self.account_refs and self.connector_type != "ticketing":
            raise ValueError("account_refs apply to ticketing sources only")
        if self.channels and self.connector_type != "chat_export":
            raise ValueError("channels apply to chat_export sources only")
        return self

    def account_keys(self) -> set[str]:
        """Every account key this configuration names."""
        keys = set(self.account_refs.values())
        keys |= {c.account for c in self.channels.values() if c.account}
        return keys


class SourcesFile(_Strict):
    sources: list[SourceConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [s.key for s in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source key")
        return self

    def get(self, key: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.key == key), None)


def load_sources_file(path: Path) -> SourcesFile:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SourcesFile.model_validate(raw)
