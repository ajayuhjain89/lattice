"""Deterministic bulk distractor tickets (``tickets/tickets-bulk.jsonl``).

Retrieval is only meaningful with ordinary, unrelated records around the storylines.
These are generated from a small set of reviewed templates of everyday support work --
never storyline topics (``distractor_forbidden_terms`` in each ground truth, checked by
the lint) -- with a fixed seed, and committed. The lint regenerates them and fails if
the committed file differs, so they are never edited by hand.

Ticket numbers are placed between the hand-authored tickets of the same organization in
time order, so numbers keep growing with creation time across the whole export, and
never collide with an authored ticket. Generated tickets are all solved or closed, so
questions about *open* tickets depend only on authored records.
"""

import itertools
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

BULK_FILE = "tickets-bulk.jsonl"
AUTHORED_FILE = "tickets.jsonl"
DATA = Path(__file__).resolve().parents[4] / "data" / "synthetic"


@dataclass(frozen=True)
class Template:
    subjects: tuple[str, ...]
    type: str
    tags: tuple[str, ...]
    body: str
    reply: str
    follow_up: str
    close: str
    group: str = "Tier 1"


@dataclass(frozen=True)
class Customer:
    org_id: int
    people: tuple[tuple[str, str], ...]  # (name, email)
    start: str
    end: str
    weight: int


@dataclass(frozen=True)
class OrgPlan:
    seed: int
    count: int
    first: tuple[str, int]  # (date, ticket number) before the first authored ticket
    last: tuple[str, int]
    agents: tuple[tuple[str, str], ...]
    customers: tuple[Customer, ...]
    templates: tuple[Template, ...]
    names: tuple[str, ...]


_HALDEN_TEMPLATES = (
    Template(
        ("CSV import shows strange characters", "Accented names broken after CSV import"),
        "problem",
        ("csv", "import"),
        "Our CSV import turns names like {word} into garbled text. The file comes from Excel.",
        "Hi {first} - Excel sometimes saves CSV as Windows-1252. Could you re-export as "
        "'CSV UTF-8' or pick the encoding in the import step's advanced options?",
        "Choosing UTF-8 in the import step fixed it.",
        "Great, closing this one.",
    ),
    Template(
        ("Scheduled flow runs an hour late", "Flow schedule ignores our time zone"),
        "problem",
        ("scheduler", "flows"),
        "Our {flow} flow is scheduled for 06:00 but runs at 07:00.",
        "Hi {first} - the schedule is set in UTC. Since 2026.08 you can switch the "
        "schedule to your workspace's local time zone in the trigger settings.",
        "Switched to local time, it ran at 06:00 today.",
        "Thanks for confirming.",
    ),
    Template(
        ("Colleague can't see a flow I shared", "Shared flow not visible to teammate"),
        "question",
        ("permissions", "sharing"),
        "I shared the {flow} flow with a colleague but it doesn't show up for them.",
        "Hi {first} - flows shared with a person need the 'Viewer' role at least; 'Runner' "
        "only lets them trigger it from a link. You can change the role in the Share menu.",
        "Changed it to Viewer, they can see it now.",
        "Glad that sorted it.",
    ),
    Template(
        ("API pagination cursor expired", "Getting 'cursor expired' when listing runs"),
        "problem",
        ("api", "pagination"),
        "Our nightly job lists flow runs through the API and fails with 'cursor expired' "
        "after about {n} pages.",
        "Hi {first} - cursors expire after 10 minutes. If the job pauses between pages, "
        "request smaller pages or restart from the last run ID with the 'after' filter.",
        "We reduced the pause between pages and it completes now.",
        "Closing, thanks.",
        "Tier 2",
    ),
    Template(
        ("Webhook signature verification failing", "HMAC check fails on Halden webhooks"),
        "problem",
        ("webhooks", "security"),
        "Our endpoint rejects Halden webhooks because the HMAC signature doesn't match.",
        "Hi {first} - the signature is computed over the raw request body. Frameworks that "
        "parse and re-serialize JSON first will produce a different digest. Verify against "
        "the raw bytes.",
        "That was it - our middleware parsed the body first.",
        "Closing this out.",
        "Tier 2",
    ),
    Template(
        ("Google Sheets connection keeps disconnecting", "Sheets connector asks to reconnect"),
        "problem",
        ("connector", "google-sheets"),
        "The Google Sheets connection used by our {flow} flow asks us to reconnect every few days.",
        "Hi {first} - this happens when the Google account that authorized the connection "
        "changes its password or loses access to the sheet. Reconnecting with a service "
        "account avoids it.",
        "We moved it to a service account, stable since.",
        "Great - closing.",
    ),
    Template(
        ("Copy of last invoice", "Please resend invoice"),
        "task",
        ("billing", "invoice"),
        "Could you resend our latest invoice to accounts payable? Our copy went missing.",
        "Hi {first} - I've resent it to the billing contact on file. You can also download "
        "invoices from Admin > Billing.",
        "Received, thank you.",
        "Closing.",
        "Billing",
    ),
    Template(
        ("Snowflake step is slow", "Snowflake query step takes minutes"),
        "problem",
        ("connector", "snowflake"),
        "The Snowflake query step in our {flow} flow went from seconds to several minutes.",
        "Hi {first} - the step now runs on your warehouse's default size. Setting a "
        "dedicated warehouse on the connection brought similar queries back to seconds for "
        "other customers.",
        "A dedicated XS warehouse fixed it.",
        "Thanks, closing.",
    ),
    Template(
        ("Custom field missing in Salesforce mapping", "Can't map a Salesforce custom field"),
        "question",
        ("connector", "salesforce"),
        "A custom field we added in Salesforce yesterday doesn't appear in the field mapping.",
        "Hi {first} - field lists are cached for 24 hours. Use 'Refresh schema' on the "
        "Salesforce connection to load new fields immediately.",
        "Refresh schema worked.",
        "Closing, thanks.",
    ),
    Template(
        ("HTTP step times out after 30 seconds", "HTTP request step timeout"),
        "question",
        ("flows", "http"),
        "Our HTTP request step calls an internal report service that takes about {n} "
        "seconds, and the step times out.",
        "Hi {first} - the default timeout is 30 seconds and can be raised to 120 in the "
        "step's advanced settings. For longer jobs, use the async pattern with a callback.",
        "Raising it to 90 seconds is enough for us.",
        "Closing this one.",
    ),
    Template(
        ("Notification emails land in spam", "Flow emails going to junk"),
        "problem",
        ("email", "notifications"),
        "Emails sent by the notification step go to our users' junk folders.",
        "Hi {first} - adding Halden's sending domain to your SPF record and allow-listing "
        "notifications@halden.example usually fixes this. Instructions attached.",
        "Our mail team allow-listed it; emails arrive now.",
        "Great, closing.",
    ),
    Template(
        ("Who changed our flow?", "Need to see who edited a flow"),
        "question",
        ("flows", "versioning"),
        "Someone changed the {flow} flow last week and it broke. Can we see who?",
        "Hi {first} - open the flow and use Versions: each version shows the editor and a "
        "diff, and you can restore a previous version.",
        "Found it and restored the previous version.",
        "Glad to help - closing.",
    ),
    Template(
        ("Password reset for a non-SSO user", "Service user can't reset password"),
        "task",
        ("accounts", "password"),
        "Our integration service user isn't behind SSO and needs a password reset.",
        "Hi {first} - I've sent a reset link to the service user's email. Consider an API "
        "token instead of a password for integrations.",
        "Reset done, and we'll switch to a token.",
        "Closing.",
    ),
    Template(
        ("Rename our workspace", "Workspace name change request"),
        "task",
        ("admin", "workspace"),
        "Please rename our workspace to reflect our new brand name.",
        "Hi {first} - workspace admins can rename it in Admin > General. The workspace URL "
        "stays the same so nothing breaks.",
        "Done, thanks.",
        "Closing.",
    ),
    Template(
        ("429 errors from the Halden API", "Hitting API rate limits"),
        "problem",
        ("api", "rate-limit"),
        "Our sync script gets 429 responses from the Halden API during the nightly run.",
        "Hi {first} - the API allows 600 requests per minute per workspace. The response "
        "includes Retry-After; honouring it (or batching run lookups) avoids the errors.",
        "We added backoff on Retry-After and the errors stopped.",
        "Closing, thanks.",
        "Tier 2",
    ),
    Template(
        ("How long are run logs kept?", "Run history retention question"),
        "question",
        ("retention", "logs"),
        "Our auditors want to know how long flow run logs are retained.",
        "Hi {first} - run history is kept for 400 days on Enterprise and 90 days on "
        "Business. You can export it to S3 for longer retention.",
        "Thanks, that answers it.",
        "Closing.",
    ),
    Template(
        ("Duplicate contacts in HubSpot", "HubSpot sync creating duplicates"),
        "problem",
        ("connector", "hubspot"),
        "The HubSpot sync in our {flow} flow creates duplicate contacts when emails differ "
        "in case.",
        "Hi {first} - set the match key to 'email (case-insensitive)' in the HubSpot upsert "
        "step. Existing duplicates can be merged in HubSpot.",
        "Switched the match key, no new duplicates.",
        "Closing this one.",
    ),
    Template(
        ("Excel attachment not parsed", "Parse Excel step fails"),
        "problem",
        ("flows", "excel"),
        "The 'Parse spreadsheet' step fails on the Excel files our supplier emails us.",
        "Hi {first} - the files are password-protected .xlsx. The step can't open protected "
        "files; ask the supplier to send them unprotected or as CSV.",
        "Supplier now sends CSV.",
        "Closing.",
    ),
    Template(
        ("Slack step says channel not found", "Slack notification step failing"),
        "problem",
        ("connector", "slack"),
        "Our Slack notification step fails with 'channel_not_found' after we renamed a channel.",
        "Hi {first} - the step stores the channel ID, but private channels need the Halden "
        "app to be invited. Invite the app to the channel and reselect it in the step.",
        "Invited the app, messages post again.",
        "Closing, thanks.",
    ),
    Template(
        ("JSONPath expression help", "Extracting a nested value"),
        "question",
        ("flows", "jsonpath"),
        "How do we pull the first line item's SKU out of an order payload?",
        "Hi {first} - use $.order.line_items[0].sku in the 'Extract value' step. The "
        "expression tester in the step shows the result against a sample payload.",
        "Works, thanks!",
        "Closing.",
    ),
)

_VERIDIAN_TEMPLATES = (
    Template(
        ("Dashboard filter doesn't save", "Saved filters reset"),
        "problem",
        ("dashboards", "filters"),
        "Filters on the {flow} dashboard reset every time we reopen it.",
        "Hi {first} - filters are saved per view. Use 'Save view' after setting them, or "
        "set defaults in the dashboard settings.",
        "Save view did it.",
        "Closing.",
    ),
    Template(
        ("Chart totals off after time zone change", "Wrong daily totals"),
        "problem",
        ("dashboards", "timezone"),
        "Daily totals on the {flow} chart changed after we switched the workspace time zone.",
        "Hi {first} - days are now bucketed in the new time zone, so totals shift at the "
        "boundaries. Historical data is unchanged.",
        "Makes sense, thanks.",
        "Closing.",
    ),
    Template(
        ("Scheduled PDF missing pages", "PDF export cuts off"),
        "problem",
        ("export", "pdf"),
        "The scheduled PDF of the {flow} dashboard is missing the last pages.",
        "Hi {first} - long dashboards need 'Fit to multiple pages' enabled in the schedule.",
        "Enabled it, full PDF now.",
        "Closing.",
    ),
    Template(
        ("Invite email not received", "User didn't get the invite"),
        "problem",
        ("accounts", "invites"),
        "A new colleague never got their Veridian invite email.",
        "Hi {first} - I've resent it. If it doesn't arrive, allow-list no-reply@veridian.example.",
        "Arrived this time.",
        "Closing.",
    ),
    Template(
        ("Embedded dashboard blocked", "Iframe embed shows blank"),
        "question",
        ("embedding",),
        "Our intranet shows a blank frame where the embedded dashboard should be.",
        "Hi {first} - add your intranet's domain to the allowed embed origins in Settings > "
        "Embedding.",
        "Added it, the dashboard shows now.",
        "Closing.",
    ),
    Template(
        ("Query timeout on large dataset", "Report query times out"),
        "problem",
        ("queries", "performance"),
        "The {flow} report times out when we select the full year.",
        "Hi {first} - enable the aggregate table for that dataset; queries over a year then "
        "read pre-computed daily rollups.",
        "Much faster now.",
        "Closing.",
    ),
)

_WORDS = ("Müller", "Ødegård", "Jiménez", "Łukasz", "François", "Çelik")
_FLOWS = (
    "supplier invoice routing",
    "inventory reconciliation",
    "order sync",
    "weekly KPI export",
    "carrier booking",
    "lead enrichment",
)
_DASHBOARDS = ("plant OEE", "readmissions", "monthly revenue", "campaign performance")

_PLANS: dict[str, OrgPlan] = {
    "halden": OrgPlan(
        seed=20260927,
        count=120,
        first=("2025-01-02T00:00:00+00:00", 3100),
        last=("2026-09-27T23:59:00+00:00", 4990),
        agents=(
            ("Ben Adeyemi", "ben.adeyemi@halden.example"),
            ("Tomas Silva", "tomas.silva@halden.example"),
            ("Aisha Karim", "aisha.karim@halden.example"),
            ("Leo Nakamura", "leo.nakamura@halden.example"),
            ("Priya Natarajan", "priya.natarajan@halden.example"),
        ),
        customers=(
            Customer(
                360001001,
                (
                    ("Derek Holt", "derek.holt@acme.example"),
                    ("Wen Zhao", "wen.zhao@acme.example"),
                    ("Luca Ferri", "luca.ferri@acme.example"),
                ),
                "2025-01-06",
                "2026-09-10",
                5,
            ),
            Customer(
                360001002,
                (
                    ("Grete Hansen", "grete.hansen@northpeak.example"),
                    ("Jonas Berg", "jonas.berg@northpeak.example"),
                ),
                "2025-01-06",
                "2026-03-05",
                3,
            ),
            Customer(
                360001003,
                (
                    ("Karen Liu", "karen.liu@brightwaterhealth.example"),
                    ("Ravi Menon", "ravi.menon@brightwaterhealth.example"),
                ),
                "2025-06-23",
                "2026-09-10",
                4,
            ),
            Customer(
                360001004,
                (
                    ("Martin Osei", "martin.osei@kestrelbank.example"),
                    ("Aaron Fox", "aaron.fox@kestrelbank.example"),
                ),
                "2025-03-17",
                "2026-09-10",
                4,
            ),
            Customer(
                360001005,
                (
                    ("Paul Nguyen", "paul.nguyen@acmehealth.example"),
                    ("Dana Brooks", "dana.brooks@acmehealth.example"),
                ),
                "2025-03-03",
                "2026-09-10",
                3,
            ),
            Customer(
                360001006,
                (
                    ("Tessa Moore", "tessa.moore@larkspur.example"),
                    ("Kim Alvarez", "kim.alvarez@larkspur.example"),
                ),
                "2025-01-06",
                "2026-09-10",
                3,
            ),
            Customer(
                360001007,
                (
                    ("Rohan Iyer", "rohan.iyer@tidewell.example"),
                    ("Nell Harper", "nell.harper@tidewell.example"),
                ),
                "2025-01-06",
                "2026-09-10",
                3,
            ),
        ),
        templates=_HALDEN_TEMPLATES,
        names=_FLOWS,
    ),
    "veridian": OrgPlan(
        seed=20260928,
        count=30,
        first=("2025-10-01T00:00:00+00:00", 4100),
        last=("2026-09-27T23:59:00+00:00", 4990),
        agents=(
            ("Felix Wagner", "felix.wagner@veridian.example"),
            ("Sara Lindgren", "sara.lindgren@veridian.example"),
        ),
        customers=(
            Customer(
                5100201,
                (
                    ("Derek Holt", "derek.holt@acme.example"),
                    ("Priscilla Vance", "priscilla.vance@acme.example"),
                ),
                "2025-10-06",
                "2026-09-10",
                3,
            ),
            Customer(
                5100202,
                (("Jenna Wu", "jenna.wu@brightwaterhealth.example"),),
                "2025-10-06",
                "2026-09-10",
                2,
            ),
            Customer(
                5100203,
                (("Omar Farah", "omar.farah@quarrylane.example"),),
                "2025-10-06",
                "2026-09-10",
                2,
            ),
        ),
        templates=_VERIDIAN_TEMPLATES,
        names=_DASHBOARDS,
    ),
}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _authored(root: Path, slug: str) -> list[tuple[datetime, int]]:
    anchors = []
    path = root / slug / "tickets" / AUTHORED_FILE
    for line in path.read_text(encoding="utf-8").splitlines():
        ticket = json.loads(line)
        created = datetime.fromisoformat(ticket["created_at"].replace("Z", "+00:00"))
        anchors.append((created, int(ticket["id"].split("-")[1])))
    return sorted(anchors)


def render_bulk_tickets(slug: str, root: Path = DATA) -> str:
    """The committed content of ``<root>/<slug>/tickets/tickets-bulk.jsonl``."""
    plan = _PLANS[slug]
    rng = random.Random(plan.seed)  # noqa: S311 - fixture generation, not security
    weighted = [c for c in plan.customers for _ in range(c.weight)]
    drafts: list[tuple[datetime, Customer, Template]] = []
    for _ in range(plan.count):
        customer = rng.choice(weighted)
        start = datetime.fromisoformat(customer.start + "T08:00:00+00:00")
        end = datetime.fromisoformat(customer.end + "T08:00:00+00:00")
        offset = rng.randrange(int((end - start).total_seconds() // 60))
        created = start + timedelta(minutes=offset)
        drafts.append((created, customer, rng.choice(plan.templates)))
    drafts.sort(key=lambda d: d[0])

    # Place numbers between the authored anchors, in time order.
    anchors = [
        (datetime.fromisoformat(plan.first[0]), plan.first[1]),
        *_authored(root, slug),
        (datetime.fromisoformat(plan.last[0]), plan.last[1]),
    ]
    numbered: list[tuple[int, datetime, Customer, Template]] = []
    for (lo_at, lo), (hi_at, hi) in itertools.pairwise(anchors):
        window = [d for d in drafts if lo_at < d[0] < hi_at]
        if len(window) >= hi - lo:
            window = window[: max(hi - lo - 1, 0)]  # no room: drop the extras
        for i, (created, customer, template) in enumerate(window, start=1):
            numbered.append(
                (lo + round(i * (hi - lo) / (len(window) + 1)), created, customer, template)
            )

    lines = []
    for number, created, customer, template in numbered:
        name, email = rng.choice(customer.people)
        agent_name, agent_email = rng.choice(plan.agents)
        fill = {
            "first": name.split()[0],
            "word": rng.choice(_WORDS),
            "flow": rng.choice(plan.names),
            "n": rng.choice((12, 20, 45, 60, 75)),
        }
        times = [created]
        for gap in (rng.randrange(20, 240), rng.randrange(120, 2880), rng.randrange(10, 600)):
            times.append(times[-1] + timedelta(minutes=gap))
        texts = [
            (name, email, "end-user", template.body),
            (agent_name, agent_email, "agent", template.reply),
            (name, email, "end-user", template.follow_up),
            (agent_name, agent_email, "agent", template.close),
        ]
        comments = [
            {
                "id": number * 100 + i,
                "author": {"name": who, "email": address},
                "author_role": role,
                "public": True,
                "created_at": _iso(at),
                "body": text.format(**fill),
            }
            for i, ((who, address, role, text), at) in enumerate(zip(texts, times, strict=True), 1)
        ]
        ticket = {
            "id": f"HAL-{number}",
            "subject": rng.choice(template.subjects),
            "organization_id": customer.org_id,
            "status": rng.choice(("solved", "closed", "closed")),
            "priority": rng.choice(("low", "normal", "normal", "normal", "high")),
            "type": template.type,
            "via": rng.choice(("email", "web", "web")),
            "group": template.group,
            "created_at": _iso(created),
            "updated_at": comments[-1]["created_at"],
            "tags": list(template.tags),
            "requester": {"name": name, "email": email},
            "assignee": {"name": agent_name, "email": agent_email},
            "comments": comments,
        }
        lines.append(json.dumps(ticket, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def write_bulk_tickets(root: Path = DATA) -> list[Path]:
    written = []
    for slug in _PLANS:
        path = root / slug / "tickets" / BULK_FILE
        path.write_text(render_bulk_tickets(slug, root), encoding="utf-8")
        written.append(path)
    return written
