"""Deterministic lint for the synthetic dataset and the golden set.

``load_dataset`` reads everything; ``lint`` returns every problem found (empty when the
dataset is valid). Nothing here needs a database or a model. Checks, by area:

* configuration: manifests, source configs and admin grants validate and reference
  existing departments, accounts, groups, members, channels and records;
* raw records: formats parse; stable IDs are unique per organization; timestamps are
  time-zone aware, ordered and not after the dataset's "now"; ticket numbers grow with
  time; Slack threads are consistent; every record has at least one reader;
* canaries: unique, each exactly once across all raw sources and exactly where declared,
  in a record some persona cannot read, and no undeclared canary anywhere;
* ground truth: storyline dates and order, potential conflicts without (and
  supersession with) explicit metadata, staleness, authority, injection markers,
  unmapped records, quiet periods, cross-organization collisions, distractor hygiene,
  and that the committed bulk records match the generator;
* golden set: schema, coverage minimums, personas, scopes, record references, and
  expectations that agree with the reference policy (relevant evidence readable by the
  persona, prohibited evidence not, egress levels, ambiguity choices, hidden accounts,
  D-8 metadata cases, trap phrases, same-question groups);
* golden provenance and splits (P13.2): label sources and the label freezes (Phase 3
  labels unchanged, held-out and sealed labels frozen; ``provenance``), every item in a
  split with no information need straddling two splits and no near-duplicate questions
  on different sides (``splits``), every item in the directory of its split (sealed
  items only in ``golden-sealed``, items without a split only in ``golden-staging``),
  owner-review records on held-out and sealed items, declared
  evaluation classes that the labels really exercise (and a minimum per class),
  exhaustive reviews that cover every candidate the persona can read, and no
  contradictory or impossible constraints (evidence both wanted and prohibited, facts
  both required and forbidden, statuses and reasons that disagree, impossible scopes,
  egress expectations that lose evidence between configurations).
"""

import itertools
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from lattice.dataset import provenance, splits
from lattice.dataset.access import ExpectedAccess, OrgWorld, Unmapped
from lattice.dataset.distractors import BULK_FILE, render_bulk_tickets
from lattice.dataset.manifests import (
    AS_OF,
    CANARY,
    CATEGORY_MINIMUMS,
    CLASS_MINIMUM,
    CONFLICT_AND_SUPERSESSION_MINIMUM,
    KEY_REF,
    AdminGrantsFile,
    CanariesFile,
    Canary,
    EvalClass,
    Expected,
    GoldenItem,
    GroundTruth,
    SourcesFile,
    load_yaml,
)
from lattice.dataset.sources import (
    SOURCE_LOADERS,
    ChannelInfo,
    FrontMatter,
    Part,
    Record,
    SourceSet,
    Ticket,
)
from lattice.seed import Manifest

ORGANIZATIONS = ("halden", "veridian")
RAW_DIRS = ("documents", "tickets", "slack")
_AUTHORITY_RANK = {"conversational": 0, "working": 1, "system_of_record": 2}


@dataclass(frozen=True)
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class Dataset:
    root: Path
    golden_dir: Path
    # phase-13-followups F2: sealed items and items still being authored (no split) live
    # in sibling directories that only the dataset tools load; every evaluation run,
    # baseline and golden test loads ``golden_dir`` alone.
    sealed_dir: Path | None = None
    staging_dir: Path | None = None
    worlds: dict[str, OrgWorld] = field(default_factory=dict)
    ground_truth: dict[str, GroundTruth] = field(default_factory=dict)
    canaries: list[Canary] = field(default_factory=list)
    goldens: list[tuple[Path, GoldenItem]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    def problem(self, where: object, message: str) -> None:
        self.problems.append(Problem(str(where), message))

    def resolve(self, org: str, ref: str) -> tuple[Record, Part | None] | str:
        """A record (and part) from ``[org/]kind:id[#part]``, or why it cannot be found."""
        match = KEY_REF.fullmatch(ref)
        if match is None:
            return f"malformed record reference {ref!r}"
        world = self.worlds.get(match["org"] or org)
        if world is None:
            return f"unknown organization in {ref!r}"
        record = world.raw.records.get(match["ref"])
        if record is None:
            return f"no record {ref!r}"
        if match["part"] is None:
            return record, None
        part = record.part(match["part"])
        if part is None:
            return f"record {match['ref']} has no part #{match['part']}"
        return record, part


def error_text(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    )


def sealed_dir_for(golden_dir: Path) -> Path:
    return golden_dir.with_name(f"{golden_dir.name}-sealed")


def staging_dir_for(golden_dir: Path) -> Path:
    return golden_dir.with_name(f"{golden_dir.name}-staging")


def load_dataset(
    root: Path,
    golden_dir: Path,
    *,
    sealed_dir: Path | None = None,
    staging_dir: Path | None = None,
) -> Dataset:
    """The dataset and the golden items of ``golden_dir`` -- plus, only when named, the
    sealed and staging directories (``load_all`` names both)."""
    data = Dataset(root=root, golden_dir=golden_dir, sealed_dir=sealed_dir, staging_dir=staging_dir)
    for slug in ORGANIZATIONS:
        folder = root / slug
        try:
            manifest = Manifest.model_validate(load_yaml(folder / "org.yaml"))
            sources = SourcesFile.model_validate(load_yaml(folder / "sources.yaml"))
            grants_path = folder / "admin-grants.yaml"
            grants = (
                AdminGrantsFile.model_validate(load_yaml(grants_path))
                if grants_path.exists()
                else AdminGrantsFile()
            )
            truth = GroundTruth.model_validate(load_yaml(folder / "ground-truth.yaml"))
        except ValidationError as exc:
            data.problem(folder, f"invalid manifest: {error_text(exc)}")
            continue
        except FileNotFoundError as exc:
            data.problem(folder, f"missing {Path(str(exc.filename)).name}")
            continue
        if manifest.organization.slug != slug or truth.organization != slug:
            data.problem(folder, "organization slug does not match its folder")
        raw = SourceSet(organization=slug)
        for source in sources.sources:
            directory = folder / source.path
            if not directory.is_dir():
                data.problem(directory, f"source {source.key}: directory missing")
                continue
            SOURCE_LOADERS[source.connector_type](directory, raw, source.key)
        for where, message in raw.problems:
            data.problem(where, message)
        world = OrgWorld(
            slug=slug,
            manifest=manifest,
            sources={s.key: s for s in sources.sources},
            raw=raw,
            admin_grants=grants,
        )
        for message in world.problems:
            data.problem(folder, message)
        data.worlds[slug] = world
        data.ground_truth[slug] = truth
    try:
        data.canaries = CanariesFile.model_validate(load_yaml(root / "canaries.yaml")).canaries
    except (ValidationError, FileNotFoundError) as exc:
        detail = error_text(exc) if isinstance(exc, ValidationError) else "missing"
        data.problem(root / "canaries.yaml", detail)
    adapter = TypeAdapter(list[GoldenItem])
    golden_dirs = [d for d in (golden_dir, sealed_dir, staging_dir) if d is not None]
    for path in [p for d in golden_dirs for p in sorted(d.glob("*.yaml"))]:
        try:
            for item in adapter.validate_python(load_yaml(path)):
                data.goldens.append((path, item))
        except ValidationError as exc:
            data.problem(path, error_text(exc))
    return data


def load_all(root: Path, golden_dir: Path) -> Dataset:
    """Every golden item, sealed and staged ones included: for the dataset tools only."""
    return load_dataset(
        root,
        golden_dir,
        sealed_dir=sealed_dir_for(golden_dir),
        staging_dir=staging_dir_for(golden_dir),
    )


def lint(data: Dataset) -> list[Problem]:
    for slug, world in data.worlds.items():
        _check_configuration(data, world)
        _check_records(data, world)
        _check_ground_truth(data, world, data.ground_truth[slug])
    _check_canaries(data)
    _check_goldens(data)
    return data.problems


# --- configuration -----------------------------------------------------------------------


def _check_configuration(data: Dataset, world: OrgWorld) -> None:
    where = data.root / world.slug
    for source in world.sources.values():
        for key in source.default_departments:
            if key not in world.department_keys:
                data.problem(where, f"source {source.key}: unknown department {key}")
        for ref, account in source.account_refs.items():
            if account not in world.account_keys:
                data.problem(where, f"source {source.key}: ticketing org {ref} -> unknown account")
        for channel_id, channel in source.channels.items():
            if channel_id not in world.raw.slack_channels:
                data.problem(where, f"source {source.key}: unknown channel {channel_id}")
            if channel.account and channel.account not in world.account_keys:
                data.problem(where, f"source {source.key}: channel {channel_id} unknown account")
    emails = {m.email for m in world.manifest.members}
    for grant in world.admin_grants.grants:
        found = data.resolve(world.slug, grant.document)
        if isinstance(found, str):
            data.problem(where / "admin-grants.yaml", found)
        if grant.member not in emails:
            data.problem(where / "admin-grants.yaml", f"unknown member {grant.member}")


# --- raw records -------------------------------------------------------------------------


def _check_records(data: Dataset, world: OrgWorld) -> None:
    for record in world.raw.records.values():
        where = f"{world.slug}/{record.key}"
        stamps = [record.created_at, record.updated_at, *(p.occurred_at for p in record.parts)]
        if max(stamps) > AS_OF:
            data.problem(where, f"dated after the dataset's now ({AS_OF.date()})")
        if record.created_at > record.updated_at:
            data.problem(where, "created after updated")
        origin = record.origin
        if isinstance(origin, FrontMatter):
            _check_front_matter(data, world, record, origin)
        expected = world.expected(record)
        if isinstance(expected, ExpectedAccess) and not world.readers(record):
            data.problem(where, "no persona can read this record")
    _check_tickets(data, world)
    _check_slack(data, world)


def _check_front_matter(data: Dataset, world: OrgWorld, record: Record, fm: FrontMatter) -> None:
    where = f"{world.slug}/{record.key}"
    if fm.account and fm.account not in world.account_keys:
        data.problem(where, f"unknown account {fm.account}")
    for key in fm.departments:
        if key not in world.department_keys:
            data.problem(where, f"unknown department {key}")
    for grant in fm.grants:
        kind, _, key = grant.partition(":")
        known = world.admin_group_keys if kind == "group" else world.department_keys
        if key not in known:
            data.problem(where, f"grant to unknown {kind} {key}")
    if record.supersedes:
        target = world.raw.records.get(record.supersedes)
        if target is None:
            data.problem(where, f"supersedes unknown {record.supersedes}")
        elif target.created_at >= record.created_at:
            data.problem(where, f"supersedes {record.supersedes}, which is not older")


def _check_tickets(data: Dataset, world: OrgWorld) -> None:
    comment_ids: Counter[int] = Counter()
    tickets: list[Ticket] = []
    for path, ticket in world.raw.tickets:
        where = f"{path.name}:{ticket.id}"
        tickets.append(ticket)
        comment_ids.update(c.id for c in ticket.comments)
        times = [c.created_at for c in ticket.comments]
        if times != sorted(times):
            data.problem(where, "comments are not in chronological order")
        if times[0] != ticket.created_at:
            data.problem(where, "first comment is not at the ticket's creation time")
        if times[-1] != ticket.updated_at:
            data.problem(where, "updated_at is not the last comment's time")
    for comment_id, count in comment_ids.items():
        if count > 1:
            data.problem(f"{world.slug}/tickets", f"duplicate comment id {comment_id}")
    ordered = sorted(tickets, key=lambda t: t.number)
    for before, after in itertools.pairwise(ordered):
        if after.created_at < before.created_at:
            data.problem(
                f"{world.slug}/tickets",
                f"{after.id} was created before {before.id}: ticket numbers must grow with time",
            )


def _check_slack(data: Dataset, world: OrgWorld) -> None:
    users = world.raw.slack_users
    for info in world.raw.slack_channels.values():
        for member in [info.channel.creator, *info.channel.members]:
            if member not in users:
                data.problem(f"{world.slug}/slack", f"#{info.channel.name}: unknown user {member}")


# --- canaries ----------------------------------------------------------------------------


def _raw_files(data: Dataset) -> list[Path]:
    return [
        path
        for slug in ORGANIZATIONS
        for sub in RAW_DIRS
        for path in sorted((data.root / slug / sub).rglob("*"))
        if path.is_file()
    ]


def _check_canaries(data: Dataset) -> None:
    where = data.root / "canaries.yaml"
    for label, values in (
        ("id", [c.id for c in data.canaries]),
        ("string", [c.string for c in data.canaries]),
    ):
        for value, count in Counter(values).items():
            if count > 1:
                data.problem(where, f"duplicate canary {label} {value}")
    occurrences: Counter[str] = Counter()
    for path in _raw_files(data):
        occurrences.update(CANARY.findall(path.read_text(encoding="utf-8")))
    declared = {c.string for c in data.canaries}
    for string in occurrences:
        if string not in declared:
            data.problem(where, f"undeclared canary {string} in the raw sources")
    for canary in data.canaries:
        label = f"canary {canary.id}"
        if occurrences[canary.string] != 1:
            data.problem(where, f"{label} occurs {occurrences[canary.string]} times (expected 1)")
        if canary.organization not in data.worlds:
            data.problem(where, f"{label}: unknown organization")
            continue
        found = data.resolve(canary.organization, canary.record)
        if isinstance(found, str):
            data.problem(where, f"{label}: {found}")
            continue
        record, part = found
        if part is None:
            data.problem(where, f"{label}: the record reference must name the part")
        elif canary.string not in part.text:
            data.problem(where, f"{label}: not in {canary.record}")
        world = data.worlds[canary.organization]
        everyone = world.readers(record) == set(world.personas)
        if canary.guards == "access" and everyone:
            data.problem(where, f"{label}: every persona can read {record.key}; nothing to protect")
        if canary.guards == "isolation" and not everyone:
            data.problem(where, f"{label}: an isolation canary must be readable org-wide")


# --- ground truth ------------------------------------------------------------------------


def _stamps(record: Record, part: Part | None) -> set[str]:
    if part is not None:
        return {part.occurred_at.date().isoformat()}
    values = {record.created_at, record.updated_at, *(p.occurred_at for p in record.parts)}
    return {v.date().isoformat() for v in values}


def _supersession_links(world: OrgWorld) -> set[tuple[str, str]]:
    """(newer, older) pairs, transitively."""
    direct = {(r.key, r.supersedes) for r in world.raw.records.values() if r.supersedes}
    closure = set(direct)
    changed = True
    while changed:
        changed = False
        for a, b in list(closure):
            for c, d in direct:
                if b == c and (a, d) not in closure:
                    closure.add((a, d))
                    changed = True
    return closure


def _linked(links: set[tuple[str, str]], a: str, b: str) -> bool:
    return (a, b) in links or (b, a) in links


def _base(ref: str) -> str:
    match = KEY_REF.fullmatch(ref)
    return match["ref"] if match else ref


def _authority(world: OrgWorld, record: Record) -> str:
    return record.authority or world.sources[record.source].authority


def _check_ground_truth(data: Dataset, world: OrgWorld, truth: GroundTruth) -> None:
    where = f"{world.slug}/ground-truth.yaml"
    slug = world.slug

    def get(ref: str) -> tuple[Record, Part | None] | None:
        found = data.resolve(slug, ref)
        if isinstance(found, str):
            data.problem(where, found)
            return None
        return found

    for story in truth.storylines:
        previous = None
        for event in story.events:
            found = get(event.record)
            if found and event.date.isoformat() not in _stamps(*found):
                data.problem(where, f"{story.id}: {event.record} has nothing dated {event.date}")
            if previous and event.date < previous:
                data.problem(where, f"{story.id}: events out of order at {event.record}")
            previous = event.date

    links = _supersession_links(world)
    declared_supersession: set[tuple[str, str]] = set()
    for case in truth.conflicts:
        keys = [_base(r) for r in case.records]
        for ref in case.records:
            get(ref)
        pairs = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1 :]]
        if case.kind == "potential_conflict" and any(_linked(links, a, b) for a, b in pairs):
            data.problem(where, f"{case.id}: a supersession link exists; not a potential conflict")
        if case.kind == "superseded":
            linked = [(a, b) for a, b in pairs if _linked(links, a, b)]
            if not linked:
                data.problem(where, f"{case.id}: no explicit supersession metadata links these")
            declared_supersession |= {tuple(sorted(p)) for p in linked}  # type: ignore[misc]
    direct = {
        tuple(sorted((r.key, r.supersedes))) for r in world.raw.records.values() if r.supersedes
    }
    for pair in direct - declared_supersession:
        data.problem(where, f"supersession {pair} is not declared as a superseded case")

    for stale in truth.stale:
        found = get(stale.record)
        if not found:
            continue
        for newer in stale.newer:
            other = get(newer)
            if other and max(_stamps(*other)) <= max(_stamps(*found)):
                data.problem(where, f"{stale.id}: {newer} is not newer than {stale.record}")

    for ranked in truth.authority:
        newer_found = get(ranked.newer_lower_authority)
        older_found = get(ranked.older_higher_authority)
        if not (newer_found and older_found):
            continue
        if max(_stamps(*newer_found)) <= max(_stamps(*older_found)):
            data.problem(where, f"{ranked.id}: the lower-authority record is not newer")
        if (
            _AUTHORITY_RANK[_authority(world, newer_found[0])]
            >= _AUTHORITY_RANK[_authority(world, older_found[0])]
        ):
            data.problem(where, f"{ranked.id}: authorities are not ordered as declared")

    for injection in truth.injections:
        found = get(injection.record)
        if found:
            text = found[1].text if found[1] else found[0].text
            if injection.marker not in text:
                data.problem(where, f"{injection.id}: marker not found in {injection.record}")

    unmapped = {
        key for key, r in world.raw.records.items() if isinstance(world.expected(r), Unmapped)
    }
    declared_unmapped = {u.record for u in truth.unmapped}
    for key in unmapped ^ declared_unmapped:
        data.problem(where, f"{key}: unmapped records and the declared list differ")

    for quiet in truth.quiet_periods:
        if quiet.account not in world.account_keys:
            data.problem(where, f"quiet period for unknown account {quiet.account}")
            continue
        dates = [
            d
            for r in world.raw.records.values()
            if world.account_of(r) == quiet.account
            for d in _stamps(r, None)
        ]
        if not dates or max(dates) != quiet.last_evidence_on.isoformat():
            data.problem(
                where,
                f"{quiet.account}: last evidence is {max(dates, default='none')}, "
                f"declared {quiet.last_evidence_on}",
            )

    for collision in truth.cross_org_collisions:
        get(collision.record)
        others = [
            s for s, w in data.worlds.items() if s != slug and collision.record in w.raw.records
        ]
        if not others:
            data.problem(where, f"{collision.record} does not exist in another organization")

    terms = [t.lower() for t in truth.distractor_forbidden_terms]
    bulk = data.root / slug / "tickets" / BULK_FILE
    if bulk.exists():
        expected = render_bulk_tickets(slug, data.root)
        if bulk.read_text(encoding="utf-8") != expected:
            data.problem(bulk, "out of date: regenerate with `lattice dataset generate`")
        for line in expected.splitlines():
            text = line.lower()
            for term in terms:
                if re.search(rf"\b{re.escape(term)}\b", text):
                    ticket_id = json.loads(line)["id"]
                    data.problem(bulk, f"{ticket_id} contains storyline term {term!r}")


# --- golden set --------------------------------------------------------------------------


def _check_goldens(data: Dataset) -> None:
    ids = Counter(item.id for _, item in data.goldens)
    for item_id, count in ids.items():
        if count > 1:
            data.problem(data.golden_dir, f"duplicate golden id {item_id}")
    categories: Counter[str] = Counter(item.category for _, item in data.goldens)
    if len(data.goldens) < 50:
        data.problem(data.golden_dir, f"only {len(data.goldens)} golden items (minimum 50)")
    for category, minimum in CATEGORY_MINIMUMS.items():
        if categories[category] < minimum:
            data.problem(
                data.golden_dir,
                f"category {category}: {categories[category]} items (minimum {minimum})",
            )
    combined = categories["potential_conflicts"] + categories["supersession"]
    if combined < CONFLICT_AND_SUPERSESSION_MINIMUM:
        data.problem(data.golden_dir, "potential conflicts + supersession below minimum")

    golden_items = [item for _, item in data.goldens]
    _check_placement(data)
    ambiguous_units = {
        item.id
        for unit in splits.units(golden_items)
        if any(e.status == "ambiguous" for i in unit for _, e in i.expectations())
        for item in unit
    }
    groups: dict[str, list[GoldenItem]] = defaultdict(list)
    for path, item in data.goldens:
        _check_item(data, path, item)
        _check_classes(data, path, item, ambiguous_units)
        if item.same_question_group:
            groups[item.same_question_group].append(item)
    for name, items in groups.items():
        where = f"{data.golden_dir}: group {name}"
        if len(items) < 2:
            data.problem(where, "a same-question group needs at least two items")
        if len({i.question for i in items}) != 1:
            data.problem(where, "questions in a same-question group must be identical")
        if len({(i.organization, i.persona) for i in items}) != len(items):
            data.problem(where, "each item of a group must be a different persona")
        shapes = {
            (cfg, e.status, tuple(sorted(e.relevant)), tuple(sorted(e.not_used_for_answer)))
            for i in items
            for cfg, e in i.expectations()
            if cfg == "default"
        }
        if len(shapes) < 2:
            data.problem(where, "expectations do not differ between personas")
    _check_provenance_and_splits(data, golden_items)


def _check_placement(data: Dataset) -> None:
    """Each item lives in the directory of its split (phase-13-followups §7.4)."""
    for path, item in data.goldens:
        where = f"{path}: {item.id}"
        if data.staging_dir is not None and path.parent == data.staging_dir:
            if item.split is not None:
                data.problem(where, "staged items get their split from `splits --propose`")
        elif data.sealed_dir is not None and path.parent == data.sealed_dir:
            if item.split != "sealed":
                data.problem(where, "only sealed items belong in the sealed directory")
        elif item.split == "sealed":
            data.problem(where, "sealed items belong in the sealed directory")


def _check_provenance_and_splits(data: Dataset, items: list[GoldenItem]) -> None:
    try:
        freeze = provenance.load_freeze(data.golden_dir)
    except (ValueError, KeyError) as exc:
        data.problem(data.golden_dir / provenance.FREEZE_FILE, f"unreadable: {exc}")
        freeze = None
    else:
        problems = provenance.check(items, freeze, sealed_loaded=data.sealed_dir is not None)
        for where, message in problems:
            data.problem(f"{data.golden_dir}: {where}", message)
    staged = {
        item.id
        for path, item in data.goldens
        if data.staging_dir and path.parent == data.staging_dir
    }
    for problem in splits.check(items, unassigned_ok=staged):
        data.problem(f"{data.golden_dir}: {problem.where}", problem.message)
    for item in items:
        where = f"{data.golden_dir}: {item.id}"
        if item.split in ("held_out", "sealed") and item.owner_review is None:
            name = "held-out" if item.split == "held_out" else "sealed"
            data.problem(where, f"{name} items carry an owner_review record (D-41)")
    counts: Counter[str] = Counter(c for item in items for c in item.classes)
    for name in EvalClass.__args__:  # type: ignore[attr-defined]
        if counts[name] < CLASS_MINIMUM:
            data.problem(
                data.golden_dir, f"class {name}: {counts[name]} items (minimum {CLASS_MINIMUM})"
            )


_IDENTIFIER_LIKE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,15}-[0-9]+|\bticket\s+[0-9]{3,}\b", re.I)
_MISLEADING_TAGS = {
    "injection_subtle",
    "injection_obvious",
    "foreign_citation",
    "misleading",
    "newer_lower_authority",
    "exfiltration",
}


def supported_classes(data: Dataset, item: GoldenItem, ambiguous_units: set[str]) -> set[str]:
    """The evaluation classes an item's labels really exercise (a declared class must be
    one of them; ``paraphrase`` and ``keyword_vs_natural`` also need human judgment)."""
    world = data.worlds[item.organization]
    expectations = [e for _, e in item.expectations()]
    statuses = {e.status for e in expectations}
    records = splits.target_records(item)
    accounts: set[str] = set()
    for e in expectations:
        accounts |= set(e.hidden_accounts)
        for key in (*e.relevant, *e.not_used_for_answer, *e.acceptable):
            found = data.resolve(item.organization, key)
            if not isinstance(found, str):
                owner = data.worlds[found[0].organization]
                if (account := owner.account_of(found[0])) is not None:
                    accounts.add(f"{found[0].organization}/{account}")
    same_org_prohibited = any(
        (match := KEY_REF.fullmatch(key)) is not None
        and (match["org"] or item.organization) == item.organization
        for e in expectations
        for key in e.must_not_retrieve
    )
    scope = item.scope
    out: set[str] = set()
    if item.paraphrase_group:
        out |= {"paraphrase", "keyword_vs_natural"}
    if _IDENTIFIER_LIKE.search(item.question):
        out.add("identifier_variant")
    if "ambiguous" in statuses or item.id in ambiguous_units:
        out.add("ambiguous_account")
    if any(e.hidden_accounts for e in expectations):
        out.add("hidden_account_collision")
    if len(records) >= 3:
        out.add("multi_document")
    if len(accounts) >= 2:
        out.add("multi_account_comparison")
    if any(e.conflict.kind == "potential_conflict" for e in expectations):
        out.add("contradictory_evidence")
    if (
        any(e.conflict.kind == "superseded" or e.freshness is not None for e in expectations)
        or scope.since
        or scope.until
        or item.category in ("stale", "supersession")
    ):
        out.add("temporal_supersession")
    if statuses & {"insufficient_evidence", "partial"}:
        out.add("insufficient_evidence")
    if any(e.not_used_for_answer for e in expectations):
        out.add("egress_withheld")
    if same_org_prohibited:
        out.add("inaccessible_strong_match")
    if scope.account or scope.source_types or scope.since or scope.until:
        out.add("scoped_retrieval")
    if item.category == "prompt_injection" or _MISLEADING_TAGS & set(item.tags):
        out.add("injection_misleading")
    if world.slug == "veridian":
        out.add("veridian_cross_tenant")
    return out


def _check_classes(data: Dataset, path: Path, item: GoldenItem, ambiguous_units: set[str]) -> None:
    if item.organization not in data.worlds:
        return
    where = f"{path.name}: {item.id}"
    supported = supported_classes(data, item, ambiguous_units)
    for name in item.classes:
        if name not in supported:
            data.problem(where, f"class {name} is not exercised by the item's labels")
    if len(set(item.classes)) != len(item.classes):
        data.problem(where, "classes are listed more than once")


def _check_item(data: Dataset, path: Path, item: GoldenItem) -> None:
    where = f"{path.name}: {item.id}"
    world = data.worlds.get(item.organization)
    if world is None:
        data.problem(where, f"unknown organization {item.organization}")
        return
    if item.persona not in world.personas:
        data.problem(where, f"unknown persona {item.persona} in {item.organization}")
        return
    persona = item.persona
    if item.scope.account and not world.sees_account(persona, item.scope.account):
        data.problem(where, f"scope account {item.scope.account} is not visible to {persona}")
    for phrase in item.trap_phrases:
        if phrase.lower() not in item.question.lower():
            data.problem(where, f"trap phrase {phrase!r} is not in the question")
    scope = item.scope
    if scope.since and scope.until and scope.since > scope.until:
        data.problem(where, "scope since is after until")
    if any(d is not None and d > AS_OF.date() for d in (scope.since, scope.until)):
        data.problem(where, "scope dates after the dataset's now")
    for config, expected in item.expectations():
        _check_expectation(data, world, where, item, config, expected)
        _check_constraints(data, where, item, config, expected)
    if item.expected_by_egress is not None:
        _check_egress_pair(data, world, where, item)
    if item.exhaustive_review is not None:
        _check_exhaustive(data, world, where, item)


_REASONS = {
    "no_relevant_evidence": {"insufficient_evidence"},
    "claims_unverified": {"insufficient_evidence"},
    "egress_policy": {"evidence_only"},
    "action_request": {"unsupported_request"},
}


def _check_constraints(
    data: Dataset, where: str, item: GoldenItem, config: str, e: Expected
) -> None:
    """Contradictory or impossible label constraints (P13.2)."""
    where = f"{where} [{config}]"
    home = item.organization

    def qualified(ref: str, *, record: bool) -> str:
        match = KEY_REF.fullmatch(ref)
        if match is None:
            return ref
        part = "" if record or match["part"] is None else f"#{match['part']}"
        return f"{match['org'] or home}/{match['ref']}{part}"

    prohibited = {qualified(k, record=False) for k in e.must_not_retrieve}
    for label, refs in (
        ("relevant", e.relevant),
        ("acceptable", e.acceptable),
        ("not_used_for_answer", e.not_used_for_answer),
    ):
        for ref in refs:
            if prohibited & {qualified(ref, record=False), qualified(ref, record=True)}:
                data.problem(where, f"{label} {ref} is also must_not_retrieve")
    for ref in e.relevant:
        if ref in e.not_used_for_answer:
            data.problem(where, f"{ref} is both relevant and not_used_for_answer")
    for fact in e.required_facts:
        if fact in e.forbidden_claims:
            data.problem(where, f"the fact {fact!r} is both required and forbidden")
    if e.status_reason is not None and e.status not in _REASONS[e.status_reason]:
        data.problem(where, f"status_reason {e.status_reason} does not fit status {e.status}")
    undrafted = ("insufficient_evidence", "evidence_only", "unsupported_request", "ambiguous")
    if e.status in undrafted and e.required_facts:
        data.problem(where, f"{e.status} items are not drafted from evidence: no required facts")
    if e.clarification and len(set(e.clarification.choices)) != len(e.clarification.choices):
        data.problem(where, "clarification choices repeat")
    if e.clarification and set(e.clarification.choices) & set(e.hidden_accounts):
        data.problem(where, "an account is both a clarification choice and hidden")
    if item.scope.account and item.scope.account in e.hidden_accounts:
        data.problem(where, "the scope account is also hidden")
    newest = e.freshness.newest_evidence_on if e.freshness else None
    if newest is not None and newest > AS_OF.date():
        data.problem(where, "newest evidence is dated after the dataset's now")


def _check_egress_pair(data: Dataset, world: OrgWorld, where: str, item: GoldenItem) -> None:
    """Evidence withheld under the default policy must be accounted for under the
    confidential opt-in: confidential evidence becomes usable (relevant or acceptable),
    restricted evidence is still withheld (D-3, D-15)."""
    assert item.expected_by_egress is not None  # noqa: S101 - caller checks
    default = item.expected_by_egress["default"]
    opt_in = item.expected_by_egress["opt_in_confidential"]
    usable = {_base(k) for k in (*opt_in.relevant, *opt_in.acceptable)}
    withheld = {_base(k) for k in opt_in.not_used_for_answer}
    for ref, record in _records(data, world, where, default.not_used_for_answer):
        level = world.level(record)
        if level == "restricted" and _base(ref) not in withheld:
            data.problem(where, f"restricted {ref} must stay not_used_for_answer under opt-in")
        if level == "confidential" and _base(ref) not in usable:
            data.problem(where, f"confidential {ref} is lost under opt-in: relevant or acceptable")


_KIND_OF_SOURCE = {
    "doc": {"doc"},
    "ticket": {"ticket"},
    "chat": {"chat_thread", "chat_channel_day"},
}


def exhaustive_candidates(data: Dataset, item: GoldenItem) -> set[str]:
    """Every part the persona can read, inside the item's scope, whose title or text
    contains one of the review's terms (whole words, case-insensitive)."""
    assert item.exhaustive_review is not None  # noqa: S101 - caller checks
    world = data.worlds[item.organization]
    patterns = [
        re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", re.IGNORECASE)
        for t in item.exhaustive_review.terms
    ]
    scope = item.scope
    kinds = {k for t in scope.source_types for k in _KIND_OF_SOURCE[t]}
    out = set()
    for record in world.raw.records.values():
        if not world.can_read(item.persona, record):
            continue
        if scope.account and world.account_of(record) != scope.account:
            continue
        if kinds and record.kind not in kinds:
            continue
        for part in record.parts:
            day = part.occurred_at.date()
            if (scope.since and day < scope.since) or (scope.until and day > scope.until):
                continue
            text = f"{record.title}\n{part.text}"
            if item.exhaustive_review.scope_all or any(p.search(text) for p in patterns):
                out.add(f"{record.key}#{part.anchor}")
    return out


def _expand(data: Dataset, org: str, refs: list[str]) -> set[str]:
    """Part keys covered by ``refs`` (a record key covers every part)."""
    out = set()
    for ref in refs:
        found = data.resolve(org, ref)
        if isinstance(found, str):
            continue
        record, part = found
        parts = [part] if part is not None else list(record.parts)
        out |= {f"{record.key}#{p.anchor}" for p in parts}
    return out


def _check_exhaustive(data: Dataset, world: OrgWorld, where: str, item: GoldenItem) -> None:
    review = item.exhaustive_review
    assert review is not None  # noqa: S101 - caller checks
    scope = item.scope
    if review.scope_all and not (scope.account or scope.source_types or scope.since or scope.until):
        data.problem(where, "scope_all needs a scope")
        return
    if item.expected_by_egress is not None:
        data.problem(where, "exhaustive labels need a single expectation")
        return
    e = item.expected
    assert e is not None  # noqa: S101 - one of the two is set
    found = exhaustive_candidates(data, item)
    for key in review.extra:
        resolved = data.resolve(item.organization, key)
        if isinstance(resolved, str) or resolved[1] is None:
            data.problem(where, f"exhaustive extra {key} is not a part key")
        elif not world.can_read(item.persona, resolved[0]):
            data.problem(where, f"exhaustive extra {key} is not readable by {item.persona}")
    expected_candidates = found | set(review.extra)
    listed = set(review.candidates)
    for key in sorted(expected_candidates - listed):
        data.problem(where, f"exhaustive review misses candidate {key}")
    for key in sorted(listed - expected_candidates):
        data.problem(where, f"exhaustive review lists {key}, which no term or extra yields")
    wanted = _expand(data, item.organization, [*e.relevant, *e.not_used_for_answer])
    accepted = _expand(data, item.organization, e.acceptable)
    for key in sorted(wanted | accepted):
        if key not in listed:
            data.problem(where, f"labelled part {key} is not among the reviewed candidates")
    for key, verdict in sorted(review.candidates.items()):
        if verdict == "relevant" and key not in wanted:
            data.problem(where, f"reviewed relevant {key} is not labelled relevant")
        if verdict == "acceptable" and key not in accepted:
            data.problem(where, f"reviewed acceptable {key} is not labelled acceptable")
        if verdict == "unrelated" and key in wanted | accepted:
            data.problem(where, f"reviewed unrelated {key} is labelled")
        if key in wanted and verdict != "relevant":
            data.problem(where, f"labelled relevant {key} is reviewed as {verdict}")


def _records(
    data: Dataset, world: OrgWorld, where: str, refs: list[str]
) -> list[tuple[str, Record]]:
    out = []
    for ref in refs:
        found = data.resolve(world.slug, ref)
        if isinstance(found, str):
            data.problem(where, found)
        else:
            out.append((ref, found[0]))
    return out


def _check_expectation(
    data: Dataset, world: OrgWorld, where: str, item: GoldenItem, config: str, e: Expected
) -> None:
    persona = item.persona
    where = f"{where} [{config}]"
    for label, refs in (
        ("relevant", e.relevant),
        ("acceptable", e.acceptable),
        ("not_used_for_answer", e.not_used_for_answer),
        ("account_metadata_hidden", e.account_metadata_hidden),
    ):
        for ref, record in _records(data, world, where, refs):
            if not world.can_read(persona, record):
                data.problem(where, f"{label} {ref} is not readable by {persona}")
    for ref, record in _records(data, world, where, e.relevant):
        if world.above_egress(record, config):
            data.problem(where, f"relevant {ref} is above the {config} egress level")
    for ref, record in _records(data, world, where, e.not_used_for_answer):
        if not world.above_egress(record, config):
            data.problem(where, f"not_used_for_answer {ref} is allowed by the {config} policy")
    prohibited = _records(data, world, where, e.must_not_retrieve)
    for ref, record in prohibited:
        if world.can_read(persona, record):
            data.problem(where, f"must_not_retrieve {ref} is readable by {persona}")
    for phrase in item.trap_phrases:
        if not any(phrase.lower() in r.text.lower() for _, r in prohibited):
            data.problem(where, f"trap phrase {phrase!r} is in no must_not_retrieve record")
    if "security" in item.tags and not prohibited:
        data.problem(where, "security items need must_not_retrieve evidence")

    status = e.status
    if status in ("insufficient_evidence", "unsupported_request") and (
        e.relevant or e.not_used_for_answer
    ):
        data.problem(where, f"{status} items have no relevant evidence")
    if status == "evidence_only" and (e.relevant or not e.not_used_for_answer):
        data.problem(where, "evidence_only needs not_used_for_answer evidence and no relevant")
    if status in ("answered", "partial") and not e.relevant:
        data.problem(where, f"{status} needs relevant evidence")
    if (status == "ambiguous") != (e.clarification is not None):
        data.problem(where, "ambiguous items (and only they) carry clarification choices")
    if e.clarification:
        for account in e.clarification.choices:
            if not world.sees_account(persona, account):
                data.problem(where, f"clarification choice {account} is not visible to {persona}")

    links = _supersession_links(world)
    conflict_keys = [_base(r) for r, _ in _records(data, world, where, e.conflict.records)]
    pairs = [(a, b) for i, a in enumerate(conflict_keys) for b in conflict_keys[i + 1 :]]
    if e.conflict.kind != "none" and len(conflict_keys) < 2:
        data.problem(where, "a conflict expectation needs at least two records")
    if e.conflict.kind == "potential_conflict" and any(_linked(links, a, b) for a, b in pairs):
        data.problem(where, "potential_conflict records are linked by supersession metadata")
    if e.conflict.kind == "superseded" and not any(_linked(links, a, b) for a, b in pairs):
        data.problem(where, "superseded expectation without supersession metadata")
    evidence = {_base(r) for r in [*e.relevant, *e.acceptable, *e.not_used_for_answer]}
    for key in conflict_keys:
        if key not in evidence:
            data.problem(where, f"conflict record {key} is not in the item's evidence")

    for account in e.hidden_accounts:
        if account not in world.account_keys:
            data.problem(where, f"hidden account {account} does not exist")
        elif world.sees_account(persona, account):
            data.problem(where, f"hidden account {account} is visible to {persona}")
    for ref, record in _records(data, world, where, e.account_metadata_hidden):
        if world.account_of(record) is None or world.shown_account(persona, record) is not None:
            data.problem(where, f"{ref}: its account is not hidden from {persona} (D-8)")
    canaries = {c.string: c for c in data.canaries}
    for text in e.must_not_contain:
        for string in CANARY.findall(text):
            canary = canaries.get(string)
            if canary is None:
                data.problem(where, f"undeclared canary {string}")
                continue
            found = data.resolve(canary.organization, canary.record)
            if not isinstance(found, str) and world.can_read(persona, found[0]):
                data.problem(where, f"canary {canary.id} is readable by {persona}")


def summary(data: Dataset) -> dict[str, Any]:
    kinds: Counter[str] = Counter()
    for slug, world in data.worlds.items():
        for record in world.raw.records.values():
            kinds[f"{slug}.{record.kind}"] += 1
    messages = {
        slug: sum(len(r.parts) for r in w.raw.records.values() if isinstance(r.origin, ChannelInfo))
        for slug, w in data.worlds.items()
    }
    return {
        "records": dict(sorted(kinds.items())),
        "slack_messages": messages,
        "canaries": len(data.canaries),
        "golden_items": len(data.goldens),
        "golden_splits": dict(
            sorted(Counter(i.split or "unassigned" for _, i in data.goldens).items())
        ),
        "golden_categories": dict(sorted(Counter(i.category for _, i in data.goldens).items())),
    }
