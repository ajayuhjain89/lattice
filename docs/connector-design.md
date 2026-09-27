# Lattice — Connector Design

Status: Foundation draft (Phase 0). The three export formats now exist as fixtures
(`data/synthetic/<org>/documents|tickets|slack`, Phase 3); the details below marked
"as built" are what those fixtures use. Connectors themselves arrive in Phase 4.
Related: `ingestion-design.md`, `data-model.md` §3–5.

---

## 1. Purpose and scope

A connector translates one external system into typed `SourceRecord`s. It does
**not** touch the database, compute embeddings, or make authorization decisions. It
reports what the source says (including access hints); the ingestion pipeline decides
what Lattice does with it.

This keeps the untrusted-input surface in one testable place and makes a new
connector a small, self-contained unit.

## 2. Contract

```python
class Connector(Protocol):
    connector_type: ClassVar[ConnectorType]          # document_repository | ticketing | chat_export
    config_model: ClassVar[type[BaseModel]]          # validated source config

    def __init__(self, config: BaseModel) -> None: ...

    async def check(self) -> SourceHealth:
        """Can the source be read right now? Used by Sources page and before sync."""

    def list_records(self, checkpoint: Checkpoint | None) -> AsyncIterator[SourceRecord | RecordError]:
        """Yield every record (full snapshot) or those changed since checkpoint.
        Must never raise for a single bad record: yield RecordError and continue."""

    def list_access_groups(self) -> AsyncIterator[ExternalGroup]:
        """Groups whose membership governs access (e.g. private channels). May be empty."""

    capabilities: ClassVar[ConnectorCapabilities]
    # snapshot: bool      -> every sync sees all live records (enables deletion sweep)
    # incremental: bool   -> supports checkpoints
    # acl_export: bool    -> provides per-record access hints / groups
```

```python
class SourceRecord(BaseModel):
    external_id: str                      # stable, normalized (see §4)
    kind: DocumentKind                    # doc | ticket | chat_thread | chat_channel_day
    title: str
    display_ref: str | None               # "HAL-4821"
    uri: HttpsUrl | None
    author: str | None
    created_at: datetime                  # tz-aware, from source
    updated_at: datetime
    account_ref: str | None               # external account reference, mapped by ingestion
    access: AccessHints                   # see §5
    parts: list[RecordPart]               # ordered content with per-part timestamps/authors
    supersedes_external_id: str | None    # when the source explicitly marks replacement
    extra: dict[str, str | int | bool]    # small, validated per kind (ticket status, channel name)

class RecordPart(BaseModel):
    heading: str | None                   # doc section heading, "Comment 4", message author/time
    author: str | None
    occurred_at: datetime | None
    text: str                             # plain text or markdown (no HTML)

class RecordError(BaseModel):
    external_id: str | None
    stage: Literal["read", "parse", "validate"]
    code: str                             # e.g. "malformed_json", "missing_timestamp"
    message: str                          # safe summary, never record content
```

`SourceHealth`, `ExternalGroup(external_ref, name, member_emails | member_external_ids)`
and `Checkpoint` (opaque string) complete the contract.

That is the entire framework: one Protocol, a handful of Pydantic models, and a
registry mapping `connector_type` → class. No plugin loader, no base-class hierarchy.

## 3. First three connectors (local exports)

All three read from local directories in the slice, so the synthetic dataset can be
versioned in the repo and ingestion is fully reproducible. Their formats mirror real
exports so that switching to API-backed readers later changes only the reader.

### 3.1 `document_repository` — Markdown with front matter

Mirrors a Drive/Notion export plus a sharing manifest.

```
data/synthetic/halden/documents/
  acme/2026-06-12-acme-kickoff-call-notes.md
  product/decisions/2026-09-18-scim-ga-moved-to-december.md
  policies/enterprise-refund-policy-v2.md
```

Front matter: `id`, `title`, `author`, `created`, `updated`, `account` (optional),
`department`, `sensitivity`, `authority` (optional), `uri` (optional),
`supersedes` (optional), `grants` (optional list of group/department keys).
Parts: split at H1/H2 headings. Capabilities: snapshot ✔, incremental ✔ (mtime),
acl_export ✔ (front matter).

As built (Phase 3 fixtures): `departments` is a list of department keys (empty or
absent = no restriction); `grants_only: true` expresses `AccessHints.grants_only`
(implied by `sensitivity: restricted`); each grant is written `group:<key>` (an
admin-defined access group, found by its stable key, D-9) or `department:<key>`;
timestamps are ISO 8601 with a zone. Text before the first H1/H2 heading is the first
part. Per-person grants are not expressible here (§5); a person-level share is an
admin grant made in Lattice (`admin-grants.yaml` in the dataset, applied after
ingestion).

### 3.2 `ticketing` — Zendesk-style JSON Lines

```
data/synthetic/halden/tickets/tickets.jsonl
data/synthetic/halden/tickets/tickets-bulk.jsonl
```

The connector reads every `*.jsonl` file of the directory (pages of one export).
Each line: `id`, `subject`, `organization_id` (customer org in the ticketing tool),
`status`, `priority`, `created_at`, `updated_at`, `tags`, `requester`, `assignee`,
`comments[{id, author, author_role, public, created_at, body}]`. As built the fixtures
also carry Zendesk's `type`, `via` and `group`; `requester`/`assignee`/`author` are
`{name, email}`; `author_role` is `end-user` or `agent`.
Parts: a header part (subject, status, priority, requester) + one part per comment.
Internal (non-public) comments are included; they are internal-level content.
`account_ref` = ticketing organization ID → mapped through `account_external_refs`.
Source is `account_scoped = true`: unmapped tickets are not indexed.
Capabilities: snapshot ✔, incremental ✔ (`updated_at`), acl_export ✗ (source defaults
apply: internal, Support department not restricted).

### 3.3 `chat_export` — Slack export format

The real Slack export layout: `channels.json`, `groups.json` (private channels, with
`members`), `users.json`, and `<channel>/<YYYY-MM-DD>.json` message files.

- Threads (messages with replies) → one `chat_thread` record per thread root.
- Non-threaded messages → one `chat_channel_day` record per channel per day.
- `external_id`: `<channel_id>:<thread_ts>` or `<channel_id>:day:<date>`.
- Account mapping by channel via source config (e.g. `C0ACME01 → acme-corp`, or the
  naming rule `acct-<key>`); channels not mapped have no account.
- Public channels: source default sensitivity (internal).
- Private channels: `ExternalGroup` per private channel; records get a `group` grant
  and `access_mode = grants_only` so **only channel members** can read them —
  mirroring Slack's own semantics. Sensitivity comes from per-channel config
  (default: the source default, `internal`); e.g. `#exec-escalations` is configured
  `confidential`.
- Users are resolved from `users.json` for display names; message user IDs map to
  Lattice memberships by email for group membership.
- As built (fixtures): folders are named by channel name; threads use `thread_ts`,
  `reply_count`, `reply_users` and `replies`; replies are stored in the day file of the
  day they were posted; bot posts use `subtype: bot_message`; a Slack Connect channel
  includes external users from another `team_id`. Channel settings (account,
  sensitivity) live in the source configuration (`sources.yaml` in the dataset).
Capabilities: snapshot ✔, incremental ✗ (exports are full snapshots), acl_export ✔.

## 4. External identifier normalization

- `external_id` must be stable across syncs and unique within the source:
  documents → front-matter `id` (not path, so renames aren't delete+create);
  tickets → ticket ID; Slack → channel ID + ts.
- Normalized: trimmed, NFC, case preserved, ≤ 256 chars, no control characters.
- `display_ref` is the human form (`HAL-4821`) and is also indexed for exact lookup.

## 5. Access hints

```python
class AccessHints(BaseModel):
    sensitivity: Sensitivity | None          # None -> source default
    grants_only: bool = False                # True -> only explicit grants may read
    department_keys: list[str] | None        # None -> source default
    group_refs: list[str] = []               # external group refs (private channels)
    department_grant_keys: list[str] = []
```

Hints are **untrusted**. Ingestion maps them through the source's configuration:
unknown department keys fail the record (not silently dropped); sensitivity can only
be one of the enum values; groups must be declared by `list_access_groups`. A
connector cannot express "grant user X" by email in the slice; groups are the unit.

## 6. Failure reporting

- Per-record problems → `RecordError` (sync continues).
- Source-level problems (directory missing, unreadable, auth failure for future API
  connectors) → `SourceUnavailable` exception → sync run `failed`, no deletion sweep.
- Connectors never log record content.

## 7. Adding a future connector (checklist)

1. Config model + `check()`.
2. Map records to `SourceRecord` with stable `external_id` and per-part timestamps.
3. Declare capabilities honestly (no snapshot ⇒ deletion must come from a deletion
   feed or tombstones).
4. Map the source's ACL model to sensitivity/departments/groups; document fail-closed
   behaviour.
5. Fixture export + contract tests (shared test kit runs the same assertions against
   every connector: stable IDs, tz-aware timestamps, no HTML in text, errors don't
   abort iteration).

Likely next connectors and their ACL mapping: call transcripts (Gong: account-scoped,
participants), CRM notes (account-scoped, owner-based), GitHub issues
(repo visibility → groups), policies/release notes (document_repository with
`authority=system_of_record`).
