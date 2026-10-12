# Lattice — Connector Design

Status: **Implemented (Phase 4)** — `backend/src/lattice/connectors/` (contract,
registry, three connectors) with the shared contract test kit
`backend/tests/ingestion/test_connector_contract.py`. The export formats are the
Phase 3 fixtures (`data/synthetic/<org>/documents|tickets|slack`). §2 shows the contract
as built; notes marked "as built" record where the connectors differ from the draft.
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

As built (`lattice.connectors.contract`; the draft's async iterators, checkpoints and
per-connector config registry are simplified to what local exports need):

```python
class Connector(Protocol):
    capabilities: ConnectorCapabilities   # snapshot, incremental, acl_export
    def check(self) -> None: ...          # raise SourceUnavailable(code) if unreadable
    def access_groups(self) -> list[ExternalGroup]: ...   # may be empty
    def records(self) -> Iterator[SourceRecord | RecordError]:
        """Every live record (a full snapshot), in a deterministic order. Never raises
        for a single bad record: yields RecordError and continues."""

build_connector(connector_type, path) -> Connector      # lattice.connectors.registry
```

```python
class SourceRecord(BaseModel):          # frozen, extra="forbid"; validates itself
    external_id: str                    # normalized (§4), unique within the source
    kind: DocumentKind                  # doc | ticket | chat_thread | chat_channel_day
    title: str
    display_ref: str | None             # "HAL-4821"
    uri: str | None                     # https only
    author: str | None
    created_at: datetime | None         # zone-aware
    updated_at: datetime                # zone-aware
    account_ref: str | None             # account key / ticketing org ID / channel ID
    access: AccessHints                 # untrusted (§5)
    parts: tuple[RecordPart, ...]       # ≥ 1; anchors unique within the record
    supersedes_external_id: str | None
    authority: Authority | None         # None -> the source default
    extra: dict[str, str | int | bool]  # ticket status, channel name, file path

class RecordPart(BaseModel):
    anchor: str                         # stable position: section "3", comment "c4", message ts
    heading: str | None
    author: str | None
    occurred_at: datetime | None
    text: str                           # plain text or Markdown; control chars and HTML rejected

class RecordError(BaseModel):
    external_id: str | None             # when known
    location: str | None                # "tickets.jsonl:12" -- operators only (a filename)
    stage: Literal["read", "parse", "validate"]
    code: str                           # lowercase code, e.g. "malformed_json"
    params: dict[str, str | int]        # short identifiers only (field name, line number)

class ExternalGroup(BaseModel):
    external_ref: str                   # e.g. a private channel ID
    name: str                           # "#acct-acme"
    member_emails: tuple[str, ...]      # lowercased; mapped to memberships by ingestion
```

Differences from the draft: `RecordError` has **no free-text `message`** — its
parameters are validated as short identifiers, so a connector cannot put record content
into a failure; `check()` raises instead of returning a health object; iteration is
synchronous (local exports); `incremental`/checkpoints are not built (no connector
supports them yet). Connector configuration holds only what is needed to read the
export (a path); Lattice-side settings (defaults, account mapping, channel settings)
live in `sources.yaml` and never reach a connector.

That is the entire framework: one Protocol, a handful of Pydantic models, and a
registry mapping `connector_type` → connector. No plugin loader, no base-class
hierarchy. Connectors import only the standard library, Pydantic, PyYAML and the
contract (a test enforces it): no database, provider, ingestion or authorization code.

## 3. First three connectors (local exports)

All three read from local directories in the slice, so the synthetic dataset can be
versioned in the repo and ingestion is fully reproducible. Their formats mirror real
exports so that switching to API-backed readers later changes only the reader.

**What they are (stated for customers, D-33, 2026-10-06).** All three are
**export-based sources**, not live integrations: *Documents (Markdown export)* is
Lattice's Markdown interchange format; *Tickets (JSON Lines export)* is Lattice's ticket
interchange format shaped like a Zendesk export — not a Zendesk integration; *Slack
workspace export* is Slack's own export format — not a live Slack integration. Phase 12
adds no live connector (GitHub Issues is the first future candidate). *Current:* exports
are read from a path an operator's `lattice sync` recorded. *Phase 12 target (not built):*
administrators upload exports through the import store (phase-12-plan C8) or pick an
operator-registered location; the connectors are unchanged.

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
Parts: split at H1/H2 headings (not inside fenced code). Capabilities: snapshot ✔,
incremental ✗ (as built: the export is read whole), acl_export ✔ (front matter).

As built (Phase 3 fixtures): `departments` is a list of department keys (empty or
absent = no restriction); `grants_only: true` expresses `AccessHints.grants_only`
(implied by `sensitivity: restricted`); each grant is written `group:<key>` (an
admin-defined access group, found by its stable key, D-9) or `department:<key>`;
timestamps are ISO 8601 with a zone. Text before the first H1/H2 heading is the first
part. Per-person grants are not expressible here (§5); a person-level share is an
admin grant made in Lattice (`admin-grants.yaml` in the dataset, applied after
ingestion).

As built (connector): `external_id` is the front-matter `id` (a rename is not a
delete + create); two files with the same `id` are both rejected
(`duplicate_external_id`); a file without front matter, with malformed YAML or invalid
fields, or with HTML in its text yields a `RecordError` and the next file is read.

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
`account_ref` = ticketing organization ID (canonical decimal text) → mapped **exactly** by
the source's `account_external_refs` rows of kind `ticketing_organization` (P12.3; the
operator's `sources.yaml` `account_refs` are written there by `lattice sync`, audited as
`operator:sync`; administrators map, remap and unmap in Access → Accounts). Source is
`account_scoped = true`: unmapped tickets
are not indexed (`access_unmappable:account`, e.g. HAL-4951 in the dataset).
`display_ref` = the ticket ID. People appear by name only; emails are never copied into
content. A ticket ID that appears twice is rejected in every occurrence.
Capabilities: snapshot ✔, incremental ✗ (as built), acl_export ✗ (source defaults
apply: internal, no department restriction).

### 3.3 `chat_export` — Slack export format

The real Slack export layout: `channels.json`, `groups.json` (private channels, with
`members`), `users.json`, and `<channel>/<YYYY-MM-DD>.json` message files.

- Threads (messages with replies) → one `chat_thread` record per thread root.
- Non-threaded messages → one `chat_channel_day` record per channel per day.
- `external_id`: `<channel_id>:<thread_ts>` or `<channel_id>:day:<date>`.
- Account mapping by channel ID through the source's `account_external_refs` rows of kind
  `chat_channel` (P12.3; a `sources.yaml` `channels: {C0ACCTKES01: {account:
  kestrel-bank}}` entry is written there by `lattice sync`); channels not mapped have no
  account. No naming rule — an explicit mapping only, by ID. Channel *names* never appear
  in the mapping workflow (D-18).
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
- As built (connector): any message a reply points to is a thread root, even without
  `reply_count`, so a root never appears in both its thread and its day record; a reply
  to a missing root is `orphan_reply`; `<@U123>` becomes `@Real Name` and `&amp;`,
  `&lt;`, `&gt;` are unescaped (text that then looks like HTML is rejected); a folder
  that names no known channel is `unknown_channel`. Missing or malformed
  `users.json`/`channels.json`/`groups.json` makes the source unavailable: without them
  no message can be attributed and no private channel's membership is known.
Capabilities: snapshot ✔, incremental ✗ (exports are full snapshots), acl_export ✔.

## 4. External identifier normalization

- `external_id` must be stable across syncs and unique within the source:
  documents → front-matter `id` (not path, so renames aren't delete+create);
  tickets → ticket ID; Slack → channel ID + ts.
- Normalized: trimmed, NFC, case preserved, ≤ 256 chars, no control characters.
- `display_ref` is the human form (`HAL-4821`) and is also indexed for exact lookup.

## 5. Access hints

```python
class AccessHints(BaseModel):                  # as built
    sensitivity: Level | None = None           # None -> channel setting / source default
    grants_only: bool = False                  # True -> only explicit grants may read
    department_keys: tuple[str, ...] | None = None   # None -> the source default
    group_refs: tuple[str, ...] = ()           # groups declared by access_groups() (private channels)
    group_grant_keys: tuple[str, ...] = ()     # admin-defined Lattice groups, by stable key (D-9)
    department_grant_keys: tuple[str, ...] = ()
```

Hints are **untrusted**. Ingestion maps them through the source's configuration and
fails the record when it cannot (`ingestion-design.md` §6): unknown department or group
keys fail it (never silently dropped); groups must be declared by `access_groups()`. A
connector cannot express "grant user X": groups are the unit, and person-level shares
are admin grants made in Lattice (D-12).

## 6. Failure reporting

- Per-record problems → `RecordError` (sync continues). A record that fails before
  its `external_id` is known (a corrupt line) blocks the deletion sweep for that run.
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
5. Fixture export + contract tests: add the connector to `CONNECTORS` and its
   malformed fixtures to `MALFORMED` in `tests/ingestion/test_connector_contract.py`
   (the shared kit asserts normalized unique IDs, zone-aware timestamps, plain text,
   deterministic order, honest capabilities, record keys equal to the dataset's, and
   that errors carry no content and never abort iteration).

Likely next connectors and their ACL mapping: call transcripts (Gong: account-scoped,
participants), CRM notes (account-scoped, owner-based), GitHub issues
(repo visibility → groups), policies/release notes (document_repository with
`authority=system_of_record`).
