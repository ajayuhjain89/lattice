/**
 * Deterministic API fixtures for component and browser tests. Every object is typed by the
 * generated OpenAPI types, so a contract change breaks these fixtures at compile time.
 * Content is synthetic (Halden, the design customer) and imports nothing from the app.
 */
import type { components } from "../api/schema.gen";

type S = components["schemas"];
type Evidence = S["EvidenceItem"];
type Detail = S["EvidenceDetail"];

export const ORG = { id: "0b7d8a4e-6a3f-4b53-9d0e-2f7c5b1d9a01", slug: "halden", name: "Halden" };

export const ME_MAYA: S["MeResponse"] = {
  user: { id: "8f3a2c10-1d4e-4c7b-9a55-0e6f2b7c8d11", email: "maya.okafor@halden.example", display_name: "Maya Okafor" },
  organization: ORG,
  membership: { id: "5c1e9b72-3a8d-4f60-b2c4-7d9e0a1f2b22", role_label: "Account Manager" },
  capabilities: [],
};

export const ME_LENA: S["MeResponse"] = {
  user: { id: "2d7e4f81-9b3c-4a26-8e15-6c0d3f9a4b33", email: "lena.brandt@halden.example", display_name: "Lena Brandt" },
  organization: ORG,
  membership: { id: "7a9c3e15-2f6b-4d80-a1e7-9b4c2d6f8e44", role_label: "IT Administrator" },
  capabilities: ["admin"],
};

// --- evidence -----------------------------------------------------------------------------

const DOC = {
  kickoff: "a1f0c3e2-1111-4a1a-8b1b-000000000001",
  ticket: "a1f0c3e2-2222-4a1a-8b1b-000000000002",
  decision: "a1f0c3e2-3333-4a1a-8b1b-000000000003",
  escalation: "a1f0c3e2-4444-4a1a-8b1b-000000000004",
  slack: "a1f0c3e2-5555-4a1a-8b1b-000000000005",
  roadmap: "a1f0c3e2-6666-4a1a-8b1b-000000000006",
  refundV1: "a1f0c3e2-7777-4a1a-8b1b-000000000007",
  refundV2: "a1f0c3e2-8888-4a1a-8b1b-000000000008",
};

export const EV = {
  kickoff: "e0000000-0000-4000-8000-000000000001",
  ticket: "e0000000-0000-4000-8000-000000000002",
  decision: "e0000000-0000-4000-8000-000000000003",
  escalation: "e0000000-0000-4000-8000-000000000004",
  slack: "e0000000-0000-4000-8000-000000000005",
  roadmap: "e0000000-0000-4000-8000-000000000006",
  refundV1: "e0000000-0000-4000-8000-000000000007",
  refundV2: "e0000000-0000-4000-8000-000000000008",
  missing: "e0000000-0000-4000-8000-00000000dead",
};

const ACME = { key: "acme-corp", name: "Acme Corp" };

function item(partial: Partial<Evidence> & Pick<Evidence, "evidence_id" | "document_id" | "title" | "text">, rank: number): Evidence {
  return {
    rank,
    source_type: "doc",
    document_kind: "doc",
    source_name: "Halden Drive",
    display_ref: null,
    uri: null,
    heading_path: [],
    author: null,
    occurred_at: null,
    document_updated_at: "2026-09-18T09:00:00Z",
    version: 1,
    authority: "working",
    classification: "internal",
    account: null,
    supersedes_document_id: null,
    superseded_by_document_ids: [],
    used_for_answer: true,
    not_used_reason: null,
    ...partial,
  };
}

const KICKOFF_TEXT =
  "Acme kickoff — call notes, 12 June 2026.\n\nAttendees: Maya Okafor (Halden), Priya Natarajan (Halden), Dana Wu (Acme IT).\n\nAcme asked about automated user provisioning. We said we're targeting SCIM provisioning in Q3, pending the identity team's capacity. Acme will pilot with 40 users first.";
const TICKET_TEXT =
  "HAL-4821 · SCIM provisioning ETA\n\nCustomer: Acme Corp. Priority: normal.\n\nUpdate from Support, 14 Aug 2026: engineering now expects SCIM to be generally available in October. We shared this date with Acme's IT lead.";
const DECISION_TEXT =
  "Decision record DR-112 · Identity roadmap\n\nContext: SCIM work was re-scoped after the August security review.\n\nDecision: SCIM GA moves to December. Acme gets a beta in November, limited to their pilot group.\n\nStatus: approved by Product and Engineering, 18 Sep 2026.";
const ESCALATION_TEXT =
  "Acme — executive escalation notes (Confidential)\n\nAcme's CIO raised the SCIM delay in the 20 Sep QBR. We committed to a written plan by 30 Sep, including the November beta scope.";
const SLACK_TEXT =
  "#acme-account · 3 Sep 2026\n\npriya: acme asked again about scim in their weekly — still pointing them at the ticket\nmaya: thanks, I'll raise it with product before the QBR";
const ROADMAP_TEXT =
  "Identity roadmap (H2)\n\nThemes: SSO hardening, SCIM provisioning, audit log export. Dates are indicative and owned by Product.";

export const ITEMS = {
  kickoff: item(
    {
      evidence_id: EV.kickoff,
      document_id: DOC.kickoff,
      title: "Acme kickoff — call notes",
      text: KICKOFF_TEXT,
      author: "Maya Okafor",
      occurred_at: "2026-06-12T15:00:00Z",
      account: ACME,
      heading_path: ["Call notes"],
      uri: "https://drive.halden.example/d/kickoff-acme",
    },
    1,
  ),
  ticket: item(
    {
      evidence_id: EV.ticket,
      document_id: DOC.ticket,
      title: "SCIM provisioning ETA",
      display_ref: "HAL-4821",
      source_type: "ticket",
      document_kind: "ticket",
      source_name: "Halden Support",
      text: TICKET_TEXT,
      author: "Priya Natarajan",
      occurred_at: "2026-08-14T10:20:00Z",
      account: ACME,
    },
    2,
  ),
  decision: item(
    {
      evidence_id: EV.decision,
      document_id: DOC.decision,
      title: "DR-112 · Identity roadmap decision",
      display_ref: "DR-112",
      text: DECISION_TEXT,
      author: "Tom Lindqvist",
      occurred_at: "2026-09-18T08:00:00Z",
      authority: "system_of_record",
    },
    3,
  ),
  escalation: item(
    {
      evidence_id: EV.escalation,
      document_id: DOC.escalation,
      title: "Acme — executive escalation notes",
      text: ESCALATION_TEXT,
      occurred_at: "2026-09-20T17:00:00Z",
      classification: "confidential",
      account: ACME,
      used_for_answer: false,
      not_used_reason: "egress_policy",
    },
    4,
  ),
  slack: item(
    {
      evidence_id: EV.slack,
      document_id: DOC.slack,
      title: "#acme-account — 3 Sep 2026",
      source_type: "chat",
      document_kind: "chat_channel_day",
      source_name: "Halden Slack",
      text: SLACK_TEXT,
      occurred_at: "2026-09-03T12:00:00Z",
      authority: "conversational",
    },
    5,
  ),
  roadmap: item(
    {
      evidence_id: EV.roadmap,
      document_id: DOC.roadmap,
      title: "Identity roadmap (H2)",
      text: ROADMAP_TEXT,
      occurred_at: "2026-07-01T09:00:00Z",
      used_for_answer: false,
      not_used_reason: "relevance_floor",
    },
    6,
  ),
} satisfies Record<string, Evidence>;

function cite(e: Evidence, quote: string): S["Citation"] {
  const at = e.text.indexOf(quote);
  if (at < 0) throw new Error(`fixture quote not in ${e.title}: ${quote}`);
  return { evidence_id: e.evidence_id, document_id: e.document_id, quote, quote_start: at, quote_end: at + quote.length };
}

const I = ITEMS;

const CLAIM_KICKOFF: S["Claim"] = {
  text: "At the June kickoff, Halden told Acme it was targeting SCIM provisioning in Q3.",
  role: "supporting",
  citations: [cite(I.kickoff, "we're targeting SCIM provisioning in Q3")],
};
const CLAIM_TICKET: S["Claim"] = {
  text: "On 14 Aug, Support told Acme that SCIM was expected to be generally available in October.",
  role: "supporting",
  citations: [cite(I.ticket, "engineering now expects SCIM to be generally available in October")],
};
const CLAIM_DECISION: S["Claim"] = {
  text: "A decision record dated 18 Sep moves SCIM general availability to December, with a November beta for Acme's pilot group.",
  role: "direct_answer",
  citations: [
    cite(I.decision, "SCIM GA moves to December"),
    cite(I.decision, "Acme gets a beta in November, limited to their pilot group"),
  ],
};

const FRESH: S["Freshness"] = {
  newest_cited_at: "2026-09-18T08:00:00Z",
  newest_evidence_at: "2026-09-20T17:00:00Z",
  is_stale: false,
  threshold_days: null,
  note: null,
};

const BASE: Omit<S["Answer"], "status" | "status_reason" | "message" | "trace_id"> = {
  claims: [],
  conflicts: [],
  unknowns: [],
  clarification: null,
  evidence: [],
  freshness: { newest_cited_at: null, newest_evidence_at: null, is_stale: false, threshold_days: null, note: null },
  notices: [],
  question_sensitivity: "internal",
};

export const TRACE = {
  answered: "70000000-0000-4000-8000-000000000001",
  partial: "70000000-0000-4000-8000-000000000002",
  insufficient: "70000000-0000-4000-8000-000000000003",
  unverified: "70000000-0000-4000-8000-000000000004",
  evidenceOnly: "70000000-0000-4000-8000-000000000005",
  unavailable: "70000000-0000-4000-8000-000000000006",
  ambiguous: "70000000-0000-4000-8000-000000000007",
  unsupported: "70000000-0000-4000-8000-000000000008",
  failed: "70000000-0000-4000-8000-000000000009",
  superseded: "70000000-0000-4000-8000-00000000000a",
  hidden: "70000000-0000-4000-8000-00000000000b",
  other: "70000000-0000-4000-8000-00000000000c",
  dangling: "70000000-0000-4000-8000-00000000000d",
};

/** answered: lead + supporting claims, a potential conflict, an egress-excluded source. */
export const ANSWERED: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.answered,
  status: "answered",
  status_reason: null,
  message: null,
  claims: [CLAIM_DECISION, CLAIM_KICKOFF, CLAIM_TICKET],
  conflicts: [
    {
      kind: "potential_conflict",
      topic: "SCIM general availability date",
      summary:
        'These sources may disagree. The most recent is the document "DR-112 · Identity roadmap decision" (18 Sep 2026). Lattice can\'t confirm which is current.',
      positions: [
        {
          claim: CLAIM_TICKET,
          document_id: DOC.ticket,
          title: I.ticket.title,
          source_type: "ticket",
          authority: "working",
          occurred_at: "2026-08-14T10:20:00Z",
          version: 1,
        },
        {
          claim: { ...CLAIM_DECISION, role: "supporting", text: "SCIM general availability moves to December." , citations: [cite(I.decision, "SCIM GA moves to December")] },
          document_id: DOC.decision,
          title: I.decision.title,
          source_type: "doc",
          authority: "system_of_record",
          occurred_at: "2026-09-18T08:00:00Z",
          version: 1,
        },
      ],
      supersession: null,
    },
  ],
  unknowns: ["Whether Acme has been told about the December date."],
  evidence: [I.decision, I.kickoff, I.ticket, I.escalation, I.slack, I.roadmap],
  freshness: FRESH,
  notices: [
    {
      code: "egress_excluded",
      count: 1,
      message:
        "1 source you can access wasn't sent to the AI model because of your organization's AI data policy, including the most recent evidence. Review them below.",
    },
  ],
};

export const PARTIAL: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.partial,
  status: "partial",
  status_reason: "claims_removed",
  message: "Some statements couldn't be verified against sources and were removed.",
  claims: [CLAIM_DECISION],
  unknowns: ["Who owns the written plan promised for 30 Sep.", "Whether the pilot group size changed."],
  evidence: [I.decision, I.ticket, I.slack],
  freshness: { ...FRESH, newest_evidence_at: "2026-09-18T08:00:00Z" },
  notices: [{ code: "claims_removed", count: 2, message: "Some statements couldn't be verified against sources and were removed." }],
};

export const INSUFFICIENT: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.insufficient,
  status: "insufficient_evidence",
  status_reason: "no_relevant_evidence",
  message: "I couldn't find evidence you have access to that answers this.",
};

export const UNVERIFIED: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.unverified,
  status: "insufficient_evidence",
  status_reason: "claims_unverified",
  message: "Lattice couldn't verify an answer against the sources it found, so none is shown. Review the evidence below.",
  evidence: [I.ticket, I.slack],
  freshness: { ...BASE.freshness, newest_evidence_at: "2026-09-03T12:00:00Z" },
};

const EXCLUDED = (e: Evidence): Evidence => ({ ...e, used_for_answer: false, not_used_reason: "egress_policy" });

export const EVIDENCE_ONLY: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.evidenceOnly,
  status: "evidence_only",
  status_reason: "egress_policy",
  message:
    "Lattice found sources you can access, but your organization's AI data policy doesn't allow sending them to the AI model, so no summary was drafted. Review the sources below.",
  evidence: [I.escalation, EXCLUDED({ ...I.decision, classification: "confidential" })],
  freshness: { ...BASE.freshness, newest_evidence_at: "2026-09-20T17:00:00Z" },
  notices: [
    {
      code: "egress_excluded",
      count: 2,
      message: "2 sources you can access weren't sent to the AI model because of your organization's AI data policy. Review them below.",
    },
  ],
  question_sensitivity: "confidential",
};

export const UNAVAILABLE: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.unavailable,
  status: "evidence_only",
  status_reason: "generation_unavailable",
  message: "No AI model is available to draft an answer right now. Review the sources below.",
  // As the API sends it: items are marked egress_policy although no provider exists.
  evidence: [EXCLUDED(I.ticket), EXCLUDED(I.kickoff)],
  freshness: { ...BASE.freshness, newest_evidence_at: "2026-08-14T10:20:00Z" },
};

export const AMBIGUOUS: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.ambiguous,
  status: "ambiguous",
  status_reason: "multiple_accounts",
  message: "Which account do you mean?",
  clarification: {
    question: "Which account do you mean?",
    choices: [
      { key: "acme-corp", name: "Acme Corp" },
      { key: "acme-health", name: "Acme Health" },
    ],
  },
  evidence: [I.kickoff, I.ticket],
};

export const UNSUPPORTED: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.unsupported,
  status: "unsupported_request",
  status_reason: "action_request",
  message: "Lattice answers questions from your sources. It can't send, change or do anything on your behalf, so nothing was done.",
  evidence: [I.ticket],
};

export const FAILED: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.failed,
  status: "failed",
  status_reason: "provider_unavailable",
  message: "The AI model couldn't be reached, so no answer was drafted. Review the sources below.",
  evidence: [{ ...I.ticket, used_for_answer: true }, I.decision],
  freshness: { ...BASE.freshness, newest_evidence_at: "2026-09-18T08:00:00Z" },
};

// Supersession: refund policy v2 replaces v1 (source metadata).
const V1_TEXT = "Refund policy v1\n\nCustomers may request a refund within 30 days of purchase. Refunds are issued to the original payment method.";
const V2_TEXT = "Refund policy v2\n\nCustomers may request a refund within 14 days of purchase. Annual plans are refunded pro rata after that window.";
export const REFUND_V1 = item(
  {
    evidence_id: EV.refundV1,
    document_id: DOC.refundV1,
    title: "Refund policy v1",
    text: V1_TEXT,
    occurred_at: "2025-11-02T09:00:00Z",
    authority: "system_of_record",
    superseded_by_document_ids: [DOC.refundV2],
  },
  2,
);
export const REFUND_V2 = item(
  {
    evidence_id: EV.refundV2,
    document_id: DOC.refundV2,
    title: "Refund policy v2",
    text: V2_TEXT,
    occurred_at: "2026-03-03T09:00:00Z",
    authority: "system_of_record",
    supersedes_document_id: DOC.refundV1,
    version: 2,
  },
  1,
);

export const SUPERSEDED: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.superseded,
  status: "answered",
  status_reason: null,
  message: null,
  claims: [
    {
      text: "Customers may request a refund within 14 days of purchase.",
      role: "direct_answer",
      citations: [cite(REFUND_V2, "Customers may request a refund within 14 days of purchase.")],
    },
    {
      text: "Annual plans are refunded pro rata after that window.",
      role: "supporting",
      citations: [cite(REFUND_V2, "Annual plans are refunded pro rata after that window.")],
    },
  ],
  conflicts: [
    {
      kind: "superseded",
      topic: null,
      summary: '"Refund policy v2" replaces "Refund policy v1", as recorded by its source. The replacement is dated 3 Mar 2026.',
      positions: [],
      supersession: { superseded_document_id: DOC.refundV1, superseding_document_id: DOC.refundV2, since: "2026-03-03T09:00:00Z" },
    },
  ],
  evidence: [REFUND_V2, { ...REFUND_V1, used_for_answer: true }],
  freshness: {
    newest_cited_at: "2026-03-03T09:00:00Z",
    newest_evidence_at: "2026-03-03T09:00:00Z",
    is_stale: true,
    threshold_days: 180,
    note: "The most recent evidence available to you is from 3 Mar 2026.",
  },
  notices: [{ code: "stale_evidence", message: "The most recent evidence available to you is from 3 Mar 2026." }],
};

/** A claim whose citation points at evidence absent from the response (UI invariant). */
export const DANGLING: S["Answer"] = {
  ...BASE,
  trace_id: TRACE.dangling,
  status: "answered",
  status_reason: null,
  message: null,
  claims: [
    CLAIM_DECISION,
    {
      text: "This claim cites evidence that is not in the response.",
      role: "supporting",
      citations: [{ evidence_id: EV.missing, document_id: DOC.roadmap, quote: "x", quote_start: 0, quote_end: 1 }],
    },
  ],
  evidence: [I.decision],
  freshness: FRESH,
};

// --- D4: provenance shapes ------------------------------------------------------------------

/**
 * Many-to-many: one claim rests on two exhibits, and one exhibit backs two claims -- the
 * case the provenance lattice exists for.
 */
export const MANY_TO_MANY: S["Answer"] = {
  ...BASE,
  trace_id: "70000000-0000-4000-8000-0000000000e1",
  status: "answered",
  status_reason: null,
  message: null,
  claims: [
    {
      text: "SCIM general availability moves to December, with a November beta for Acme's pilot group.",
      role: "direct_answer",
      citations: [cite(I.decision, "SCIM GA moves to December"), cite(I.decision, "Acme gets a beta in November, limited to their pilot group")],
    },
    {
      text: "In June, Halden told Acme it was targeting SCIM provisioning in Q3, and Acme planned a 40-user pilot.",
      role: "supporting",
      citations: [cite(I.kickoff, "we're targeting SCIM provisioning in Q3"), cite(I.kickoff, "Acme will pilot with 40 users first")],
    },
    {
      text: "Support told Acme in August that SCIM was expected in October, and Acme raised it again in September.",
      role: "supporting",
      citations: [cite(I.ticket, "engineering now expects SCIM to be generally available in October"), cite(I.slack, "acme asked again about scim in their weekly")],
    },
    {
      text: "The November beta is limited to Acme's pilot group of 40 users.",
      role: "supporting",
      citations: [cite(I.decision, "Acme gets a beta in November, limited to their pilot group"), cite(I.kickoff, "Acme will pilot with 40 users first")],
    },
  ],
  evidence: [I.decision, I.kickoff, I.ticket, I.slack, I.roadmap],
  freshness: FRESH,
};

const PILOT_CHAT = item(
  {
    evidence_id: "e0000000-0000-4000-8000-0000000000c1",
    document_id: "a1f0c3e2-9999-4a1a-8b1b-0000000000c1",
    title: "#acme-pilot — 12 Jun 2026",
    source_type: "chat",
    document_kind: "chat_channel_day",
    source_name: "Halden Slack",
    text: "#acme-pilot · 12 Jun 2026\n\ndana: we'll start the pilot with 25 seats, not 40",
    occurred_at: "2026-06-12T18:30:00Z",
    authority: "conversational",
  },
  7,
);
const PILOT_GUIDE = item(
  {
    evidence_id: "e0000000-0000-4000-8000-0000000000c2",
    document_id: "a1f0c3e2-9999-4a1a-8b1b-0000000000c2",
    title: "Customer pilot guide",
    text: "Customer pilot guide\n\nPilots cover up to 50 users and last six weeks.",
    occurred_at: null,
    authority: "system_of_record",
  },
  8,
);
const SLA_OLD = item(
  {
    evidence_id: "e0000000-0000-4000-8000-0000000000c3",
    document_id: "a1f0c3e2-9999-4a1a-8b1b-0000000000c3",
    title: "Support policy 2023",
    text: "Support policy 2023\n\nSupport responds to priority tickets within 2 business days.",
    occurred_at: "2023-02-01T09:00:00Z",
    authority: "system_of_record",
  },
  9,
);
const SLA_NEW = item(
  {
    evidence_id: "e0000000-0000-4000-8000-0000000000c4",
    document_id: "a1f0c3e2-9999-4a1a-8b1b-0000000000c4",
    title: "Acme renewal order form",
    source_name: "Halden Drive",
    text: "Acme renewal order form\n\nSupport responds to priority tickets within 1 business day.",
    occurred_at: "2026-09-01T09:00:00Z",
    account: ACME,
  },
  10,
);

const position = (e: Evidence, quote: string, text: string): S["ConflictPosition"] => ({
  claim: { text, role: "supporting", citations: [cite(e, quote)] },
  document_id: e.document_id,
  title: e.title,
  source_type: e.source_type,
  authority: e.authority,
  occurred_at: e.occurred_at,
  version: e.version,
});

/** Potential conflicts with three positions (same day, undated) and with dates years apart. */
export const CONFLICTS: S["Answer"] = {
  ...BASE,
  trace_id: "70000000-0000-4000-8000-0000000000e2",
  status: "answered",
  status_reason: null,
  message: null,
  claims: [
    { text: "Acme's pilot was planned around 40 users.", role: "direct_answer", citations: [cite(I.kickoff, "Acme will pilot with 40 users first")] },
    { text: "Acme's renewal promises a response to priority tickets within 1 business day.", role: "supporting", citations: [cite(SLA_NEW, "Support responds to priority tickets within 1 business day.")] },
  ],
  conflicts: [
    {
      kind: "potential_conflict",
      topic: "Pilot group size",
      summary: "These sources may disagree. Lattice can't confirm which is current.",
      positions: [
        position(I.kickoff, "Acme will pilot with 40 users first", "Acme will pilot with 40 users."),
        position(PILOT_CHAT, "we'll start the pilot with 25 seats", "The pilot starts with 25 seats."),
        position(PILOT_GUIDE, "Pilots cover up to 50 users", "Pilots cover up to 50 users."),
      ],
      supersession: null,
    },
    {
      kind: "potential_conflict",
      topic: "Priority ticket response time",
      summary: 'These sources may disagree. The most recent is the document "Acme renewal order form" (1 Sep 2026). Lattice can\'t confirm which is current.',
      positions: [
        position(SLA_OLD, "Support responds to priority tickets within 2 business days.", "Priority tickets get a response within 2 business days."),
        position(SLA_NEW, "Support responds to priority tickets within 1 business day.", "Priority tickets get a response within 1 business day."),
      ],
      supersession: null,
    },
  ],
  evidence: [I.kickoff, SLA_NEW, PILOT_CHAT, PILOT_GUIDE, SLA_OLD],
  freshness: { ...FRESH, newest_cited_at: "2026-09-01T09:00:00Z", newest_evidence_at: "2026-09-01T09:00:00Z" },
};

const LONG_TITLE =
  "Acme Corp — SCIM provisioning rollout plan for the production tenant, the EU sandbox and the November pilot group, including the identity team's revised capacity estimate after the August security review";

function generated(i: number, quote: string, extra: Partial<Evidence> = {}): Evidence {
  const n = String(i).padStart(2, "0");
  const text = `${i === 1 ? LONG_TITLE : `Planning note ${n}`}\n\n${"Background on the rollout and its dependencies. ".repeat(6)}${quote} ${"Further detail on owners, risks and dates follows here. ".repeat(5)}`;
  return item(
    {
      evidence_id: `e0000000-0000-4000-8000-0000000001${n}`,
      document_id: `a1f0c3e2-9999-4a1a-8b1b-0000000001${n}`,
      title: i === 1 ? LONG_TITLE : `Planning note ${n} · SCIM rollout`,
      display_ref: i % 3 === 0 ? `HAL-48${n}` : null,
      source_type: i % 3 === 0 ? "ticket" : "doc",
      document_kind: i % 3 === 0 ? "ticket" : "doc",
      source_name: i % 3 === 0 ? "Halden Support (Zendesk)" : "Halden Drive",
      text,
      author: i % 2 ? "Priya Natarajan" : "Tom Lindqvist",
      occurred_at: `2026-0${(i % 9) + 1}-1${i % 10}T09:00:00Z`,
      account: i % 2 ? ACME : null,
      ...extra,
    },
    i,
  );
}

const QUOTES = [
  "the identity team estimates eleven engineering weeks for SCIM, including directory sync, group push and deprovisioning, before general availability can be declared for any customer tenant",
  "group push ships first",
  "deprovisioning ships second",
  "the EU sandbox follows the production tenant by two weeks",
  "Acme's pilot group is the first external cohort",
  "rollback is manual during the beta",
  "audit events are recorded for every provisioning change",
  "support is staffed for the beta from 9:00 to 17:00 UK time",
  "the beta ends when GA is declared",
  "pricing is unchanged by SCIM",
  "the November date depends on the security review sign-off",
  "no customer data leaves the EU region during the pilot",
];
const MANY = QUOTES.map((q, i) => generated(i + 1, q, i >= 10 ? { used_for_answer: false, not_used_reason: i === 10 ? "egress_policy" : "relevance_floor", classification: i === 10 ? "confidential" : "internal" } : {}));

/** A long answer with many exhibits, a very long title and a very long quote. */
export const MANY_EXHIBITS: S["Answer"] = {
  ...BASE,
  trace_id: "70000000-0000-4000-8000-0000000000e3",
  status: "answered",
  status_reason: null,
  message: null,
  claims: [
    {
      text: "The identity team estimates eleven engineering weeks before SCIM can be generally available for any customer tenant, covering directory sync, group push and deprovisioning.",
      role: "direct_answer",
      citations: [cite(MANY[0]!, QUOTES[0]!)],
    },
    ...QUOTES.slice(1, 10).map((q, i): S["Claim"] => ({
      text: `${q.charAt(0).toUpperCase()}${q.slice(1)}.`,
      role: "supporting",
      citations: [cite(MANY[i + 1]!, q)],
    })),
  ],
  unknowns: ["Whether the EU sandbox needs a separate security review.", "Who approves the end of the beta."],
  evidence: MANY,
  freshness: { ...FRESH, newest_cited_at: "2026-09-10T09:00:00Z", newest_evidence_at: "2026-09-10T09:00:00Z" },
  notices: [
    { code: "egress_excluded", count: 1, message: "1 source you can access wasn't sent to the AI model because of your organization's AI data policy. Review them below." },
  ],
};

/** 429 from POST /v1/ask, as the API sends it. */
export const RATE_LIMITED = {
  status: 429,
  body: { error: { code: "rate_limited", message: "Too many requests. Try again later.", request_id: "req-429-fixture" } },
  headers: { "retry-after": "30" },
};

// --- evidence details (GET /v1/evidence/{id}) -----------------------------------------------

function detail(e: Evidence, charStart = 0): Detail {
  const { rank: _rank, used_for_answer: _used, not_used_reason: _reason, ...rest } = e;
  return { ...rest, char_start: charStart, char_end: charStart + e.text.length };
}

export const DETAILS: Record<string, Detail> = Object.fromEntries(
  [I.kickoff, I.ticket, I.decision, I.escalation, I.slack, I.roadmap, REFUND_V1, REFUND_V2, PILOT_CHAT, PILOT_GUIDE, SLA_OLD, SLA_NEW, ...MANY].map((e, i) => [
    e.evidence_id,
    detail(e, i * 311),
  ]),
);

// --- traces --------------------------------------------------------------------------------

const OPERATIONAL = {
  membership_id: ME_MAYA.membership.id,
  created_at: "2026-09-29T09:41:07Z",
  provider: "ollama",
  model: "qwen3.5:4b",
  deployment: "local",
  prompt_version: "answer-prompt-1",
  policy_version: "authz-2",
  retrieval_config_version: "retrieval-6",
  generation_calls: 1,
  timings_ms: { policy: 3.1, generation: 3890.4, total: 4212.7, retrieval_total: 301.2 },
  usage: { input_tokens: 2140, output_tokens: 388 },
};

const { evidence: _evidence, trace_id: _traceId, ...STORED } = ANSWERED;

export const TRACE_ASKER: S["AskerTrace"] = {
  ...OPERATIONAL,
  view: "asker",
  trace_id: TRACE.answered,
  status: "answered",
  status_reason: null,
  error_code: null,
  question: "When will SCIM ship for Acme, and what did we tell them?",
  question_sensitivity: "internal",
  scope: {},
  answer: STORED as unknown as Record<string, unknown>,
  answer_hidden: false,
  evidence_ids: [EV.decision, EV.kickoff, EV.ticket, EV.escalation, EV.slack, EV.roadmap],
  decision_reason: null,
  validation: {
    generation_calls: 1,
    regenerated: false,
    schema_failures: 0,
    claims_proposed: 4,
    claims_valid: 3,
    claims_rejected: 1,
    rejection_codes: { quote_not_found: 1 },
    conflicts_proposed: 1,
    conflicts_discarded: 0,
    model_status: "answered",
  },
};

export const TRACE_HIDDEN: S["AskerTrace"] = {
  ...TRACE_ASKER,
  trace_id: TRACE.hidden,
  answer: null,
  answer_hidden: true,
  evidence_ids: [EV.decision],
};

export const TRACE_ADMIN: S["AdminTrace"] = {
  ...OPERATIONAL,
  view: "operational",
  trace_id: TRACE.other,
  membership_id: "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b55",
  status: "evidence_only",
  status_reason: "egress_policy",
  error_code: null,
  provider: null,
  model: null,
  deployment: null,
  prompt_version: null,
  generation_calls: 0,
  timings_ms: { policy: 2.4, total: 288.0, retrieval_total: 281.9 },
  usage: { input_tokens: 0, output_tokens: 0 },
};

// --- providers ------------------------------------------------------------------------------

type ProviderFixture = Omit<S["ProviderOut"], "allowed_up_to" | "blocked_by">;
const LEVEL_ORDER = ["internal", "confidential", "restricted"] as const;
type FixtureLevel = (typeof LEVEL_ORDER)[number];

/**
 * Test data only: the server's reading of a fixture's lattice (backend
 * providers/lattice.py), so fixtures stay consistent. The page never computes a cell.
 */
function reading(egress: S["EgressSettingsOut"], p: ProviderFixture): { level: FixtureLevel | null; reason: string } {
  const rank = (l: string | null | undefined) => LEVEL_ORDER.indexOf(l as FixtureLevel);
  if (p.purpose === "generation" && !p.enabled) return { level: null, reason: "provider_disabled" };
  if (p.purpose === "generation" && !p.explicitly_approved) return { level: null, reason: "provider_unapproved" };
  if (!["local", "organization_controlled", "external_api"].includes(p.deployment)) return { level: null, reason: "deployment_unknown" };
  if (p.deployment === "organization_controlled" && !p.explicitly_approved) return { level: null, reason: "provider_unapproved" };
  if (!p.training_opt_out && egress.data_origin !== "synthetic") return { level: null, reason: "provider_data_use" };
  const ceiling = p.deployment === "local" ? "restricted" : p.deployment === "external_api" ? egress.external_egress_level : egress.organization_controlled_level;
  if (ceiling == null) return { level: null, reason: "deployment_not_allowed" };
  const level = LEVEL_ORDER[Math.min(rank(ceiling), rank(p.approved_for_level))]!;
  return { level, reason: "permitted" };
}

export function providerSettings(
  egress: S["EgressSettingsOut"],
  records: ProviderFixture[],
  extra: Partial<Omit<S["ProviderSettings"], "egress" | "providers" | "lattice">> = {},
): S["ProviderSettings"] {
  const providers = records.map((p) => {
    const r = reading(egress, p);
    return { ...p, allowed_up_to: r.level, blocked_by: r.level ? null : r.reason };
  });
  const lattice = providers.flatMap((p) =>
    LEVEL_ORDER.map((level) => {
      const r = reading(egress, p);
      const permitted = r.level !== null && LEVEL_ORDER.indexOf(level) <= LEVEL_ORDER.indexOf(r.level);
      let reason = r.level === null ? r.reason : "permitted";
      if (r.level !== null && !permitted) {
        const approval = LEVEL_ORDER.indexOf(p.approved_for_level as FixtureLevel);
        reason = approval < LEVEL_ORDER.indexOf(level) && LEVEL_ORDER.indexOf(r.level) === approval ? "above_approval" : "above_ceiling";
      }
      return { provider_id: p.id, level, permitted, reason };
    }),
  );
  return {
    egress,
    providers,
    lattice,
    targets: [],
    supported: [
      { provider: "gemini", trust_class: "external_api", requires_credential: true, models: ["gemini-3.5-flash-lite"], needs_target: false },
      { provider: "ollama", trust_class: "organization_controlled", requires_credential: false, models: [], needs_target: true },
    ],
    step_up: { available: true, reason: "available", assurance_required: null, max_age_seconds: 300 },
    confirmation: "Halden Logistics",
    status: { state: "complete", reason: "generation_ready", message: "A generation provider is enabled and allowed for the level the organization's questions default to." },
    ...extra,
  };
}

export const PROVIDER_ID = {
  ollama: "9a000000-0000-4000-8000-000000000001",
  gemini: "9a000000-0000-4000-8000-000000000002",
  mxbai: "9a000000-0000-4000-8000-000000000003",
} as const;

const NO_CREDENTIAL: S["CredentialOut"] = { state: "none", id: null, source: null, managed_by: null, resolvable: false, data_use: null, hint: null, set_at: null };

export const PROVIDER_OLLAMA: ProviderFixture = {
  id: PROVIDER_ID.ollama,
  purpose: "generation",
  provider: "ollama",
  model: "qwen3.5:4b",
  deployment: "local",
  approved_for_level: "confidential",
  explicitly_approved: true,
  enabled: true,
  endpoint_configured: true,
  retention_mode: "none",
  training_opt_out: true,
  credential: NO_CREDENTIAL,
  managed_by: "organization",
  destination: { kind: "deployment_local", origin: "http://127.0.0.1:11434", access: "deployment_local" },
  requires_credential: false,
  last_test: { at: "2026-10-16T09:12:00Z", result: "valid", current: true },
};

export const PROVIDER_GEMINI: ProviderFixture = {
  id: PROVIDER_ID.gemini,
  purpose: "generation",
  provider: "gemini",
  model: "gemini-3.5-flash-lite",
  deployment: "external_api",
  approved_for_level: "internal",
  explicitly_approved: true,
  enabled: false,
  endpoint_configured: false,
  retention_mode: "unpaid_product_improvement",
  training_opt_out: false,
  credential: { state: "active", id: "c1000000-0000-4000-8000-000000000001", source: "env", managed_by: "operator", resolvable: false, data_use: "unpaid", hint: null, set_at: "2026-10-01T08:00:00Z" },
  managed_by: "organization",
  destination: { kind: "vendor_api", origin: "https://generativelanguage.googleapis.com", access: "public" },
  requires_credential: true,
  last_test: null,
};

export const PROVIDER_MXBAI: ProviderFixture = {
  id: PROVIDER_ID.mxbai,
  purpose: "embedding",
  provider: "local",
  model: "mxbai-embed-large-v1",
  deployment: "local",
  approved_for_level: "restricted",
  explicitly_approved: true,
  enabled: true,
  endpoint_configured: false,
  retention_mode: "none",
  training_opt_out: true,
  credential: NO_CREDENTIAL,
  managed_by: "deployment",
  destination: { kind: "deployment_local", origin: null, access: "deployment_local" },
  requires_credential: false,
  last_test: null,
};

export const EGRESS_DEFAULT: S["EgressSettingsOut"] = {
  external_egress_level: "internal",
  organization_controlled_level: null,
  default_query_sensitivity: "internal",
  data_origin: "synthetic",
  version: 7,
};

export const PROVIDERS: S["ProviderSettings"] = providerSettings(EGRESS_DEFAULT, [PROVIDER_OLLAMA, PROVIDER_GEMINI, PROVIDER_MXBAI]);

/** A fresh organization: nothing recorded, evidence only. */
export const PROVIDERS_EMPTY: S["ProviderSettings"] = providerSettings({ ...EGRESS_DEFAULT, version: 0 }, [], {
  status: { state: "optional", reason: "evidence_only", message: "No generation provider is enabled: answers show their evidence without a written answer." },
});

/** Gemini configured in the product: a key entered here, disabled and unapproved. */
export const PROVIDER_GEMINI_NEW: ProviderFixture = {
  ...PROVIDER_GEMINI,
  explicitly_approved: false,
  enabled: false,
  training_opt_out: true,
  retention_mode: "abuse_monitoring_limited",
  credential: { state: "active", id: "c1000000-0000-4000-8000-000000000002", source: "vault", managed_by: "organization", resolvable: true, data_use: "paid", hint: "WXYZ", set_at: "2026-10-17T08:00:00Z" },
  last_test: { at: "2026-10-17T08:01:00Z", result: "valid", current: true },
};

export const PROVIDERS_GEMINI_NEW: S["ProviderSettings"] = providerSettings(EGRESS_DEFAULT, [PROVIDER_GEMINI_NEW], {
  status: { state: "optional", reason: "not_enabled", message: "A generation provider is configured but not enabled: answers show their evidence without a written answer." },
});

export const PREVIEW_APPROVE_GEMINI: S["ChangePreview"] = {
  policy_version: 7,
  escalation: true,
  newly_allowed: [
    { provider_id: PROVIDER_ID.gemini, provider: "gemini", model: "gemini-3.5-flash-lite", purpose: "generation", trust_class: "external_api", destination: "https://generativelanguage.googleapis.com", level: "internal", before: false, after: true },
  ],
  no_longer_allowed: [],
  default_query_sensitivity: { before: "internal", after: "internal" },
  remaining: ["authorization_first", "question_floor:internal", "organization_controlled_not_allowed", "never_sent:confidential", "never_sent:restricted", "credentials_never_widen"],
  affected_provider_ids: [PROVIDER_ID.gemini],
};

export const PREVIEW_LOWER_EXTERNAL: S["ChangePreview"] = {
  policy_version: 7,
  escalation: false,
  newly_allowed: [],
  no_longer_allowed: [],
  default_query_sensitivity: { before: "internal", after: "internal" },
  remaining: ["authorization_first", "question_floor:internal", "external_not_allowed", "organization_controlled_not_allowed", "never_sent:confidential", "never_sent:restricted", "credentials_never_widen"],
  affected_provider_ids: [],
};

export const STEP_UP_GRANTED: S["StepUpOut"] = {
  id: "5e000000-0000-4000-8000-000000000001",
  outcome: "granted",
  created_at: "2026-10-17T09:00:00Z",
  change: { kind: "provider", provider_id: PROVIDER_ID.gemini, approval: "internal" },
  reason: "Security review 42 approved Gemini for internal data",
  policy_version: 7,
  current_version: 7,
  grant: { id: "6e000000-0000-4000-8000-000000000001", expires_at: "2026-10-17T09:05:30Z", auth_time: "2026-10-17T09:00:30Z", assurance: null, consumed: false },
  preview: PREVIEW_APPROVE_GEMINI,
};

export const STEP_UP_NOT_FRESH: S["StepUpOut"] = { ...STEP_UP_GRANTED, outcome: "not_fresh", grant: null, preview: null };

/** Every trust class and approval the egress lattice must draw, eight providers wide. */
export const PROVIDERS_MANY: S["ProviderSettings"] = providerSettings({ ...EGRESS_DEFAULT, external_egress_level: "internal", organization_controlled_level: "confidential" }, [
  PROVIDER_OLLAMA,
  PROVIDER_GEMINI,
  PROVIDER_MXBAI,
  { ...PROVIDER_OLLAMA, id: "9a000000-0000-4000-8000-000000000004", provider: "vllm-private", model: "llama-4-scout-17b-instruct", deployment: "organization_controlled", enabled: false, destination: { kind: "registered_target", origin: "https://llm.halden.internal:8443", access: "operator_approved" } },
  { ...PROVIDER_OLLAMA, id: "9a000000-0000-4000-8000-000000000005", provider: "vllm-staging", model: "qwen3-32b", deployment: "organization_controlled", approved_for_level: "restricted", explicitly_approved: false, enabled: false, destination: { kind: "registered_target", origin: "https://staging.halden.internal", access: "not_permitted" } },
  { ...PROVIDER_GEMINI, id: "9a000000-0000-4000-8000-000000000006", provider: "anthropic", model: "claude-sonnet-5-5", enabled: false, training_opt_out: true, retention_mode: "zero", credential: { ...PROVIDER_GEMINI.credential, resolvable: true, data_use: "paid" } },
  { ...PROVIDER_GEMINI, id: "9a000000-0000-4000-8000-000000000007", provider: "legacy-gateway", model: "unknown-model", deployment: "private", enabled: false, training_opt_out: true },
  { ...PROVIDER_MXBAI, id: "9a000000-0000-4000-8000-000000000008", provider: "voyage", model: "voyage-3-large", deployment: "external_api", approved_for_level: "internal", destination: { kind: "vendor_api", origin: null, access: "public" } },
]);

export const NOT_FOUND = {
  error: { code: "not_found", message: "Not found.", request_id: "req-404-fixture" },
} as const;

// --- sources (Phase 8) -------------------------------------------------------------------------

export const SOURCE = {
  drive: "5d2c1b0a-1111-4e3f-9a8b-7c6d5e4f3a01",
  support: "5d2c1b0a-2222-4e3f-9a8b-7c6d5e4f3a02",
  slack: "5d2c1b0a-3333-4e3f-9a8b-7c6d5e4f3a03",
};

export const SOURCES_MEMBER: S["MemberSourcesResponse"] = {
  sources: [
    {
      id: SOURCE.drive,
      name: "Halden Drive",
      connector_type: "document_repository",
      readable_documents: 41,
      last_synced_at: "2026-09-28T07:12:40Z",
      last_succeeded_at: "2026-09-28T07:12:40Z",
      last_run_status: "succeeded",
    },
    {
      id: SOURCE.support,
      name: "Halden Support (Zendesk)",
      connector_type: "ticketing",
      readable_documents: 12,
      last_synced_at: "2026-09-28T07:12:44Z",
      last_succeeded_at: "2026-09-21T06:00:02Z",
      last_run_status: "partial",
    },
  ],
};

const RUN_COUNTS = {
  records_seen: 0,
  created: 0,
  updated: 0,
  access_updated: 0,
  unchanged: 0,
  deleted: 0,
  failed: 0,
  chunks_embedded: 0,
  embeddings_reused: 0,
  chunks_policy_excluded: 0,
  embedding_failures: 0,
};

export const SOURCES_ADMIN: S["AdminSourcesResponse"] = {
  sources: [
    {
      id: SOURCE.drive,
      key: "drive",
      managed_by: "operator",
      version: 1,
      import_status: null,
      rejected_upload_code: null,
      next_action: "wait",
      name: "Halden Drive",
      connector_type: "document_repository",
      status: "active",
      account_scoped: false,
      default_sensitivity: "internal",
      authority: "working",
      documents: 58,
      last_synced_at: "2026-09-28T07:12:40Z",
      last_succeeded_at: "2026-09-28T07:12:40Z",
      acl_synced_at: "2026-09-28T07:12:40Z",
      last_run: {
        id: "6e1f0a9b-1111-4c2d-8e3f-0a1b2c3d4e01",
        mode: "full",
        status: "succeeded",
        started_at: "2026-09-28T07:12:38Z",
        finished_at: "2026-09-28T07:12:40Z",
        counts: { ...RUN_COUNTS, records_seen: 58, unchanged: 58 },
        deletion_guard_tripped: false,
        error_code: null,
      },
      failure_codes: [],
      job: {
        id: "7f2a1b0c-1111-4d3e-9f4a-1b2c3d4e5f01",
        source_id: SOURCE.drive,
        mode: "full",
        status: "running",
        attempts: 1,
        error_code: null,
        created_at: "2026-09-29T09:00:00Z",
        started_at: "2026-09-29T09:00:01Z",
        finished_at: null,
        sync_run_id: null,
      },
    },
    {
      id: SOURCE.support,
      key: "support",
      managed_by: "operator",
      version: 1,
      import_status: null,
      rejected_upload_code: null,
      next_action: "map_references",
      name: "Halden Support (Zendesk)",
      connector_type: "ticketing",
      status: "active",
      account_scoped: true,
      default_sensitivity: "internal",
      authority: "working",
      documents: 214,
      last_synced_at: "2026-09-28T07:12:44Z",
      last_succeeded_at: "2026-09-21T06:00:02Z",
      acl_synced_at: "2026-09-28T07:12:44Z",
      last_run: {
        id: "6e1f0a9b-2222-4c2d-8e3f-0a1b2c3d4e02",
        mode: "full",
        status: "partial",
        started_at: "2026-09-28T07:12:41Z",
        finished_at: "2026-09-28T07:12:44Z",
        counts: { ...RUN_COUNTS, records_seen: 215, unchanged: 214, failed: 1 },
        deletion_guard_tripped: false,
        error_code: null,
      },
      failure_codes: [{ stage: "map_access", code: "access_unmappable:account", count: 1 }],
      job: null,
    },
    {
      id: SOURCE.slack,
      key: "slack",
      managed_by: "operator",
      version: 1,
      import_status: null,
      rejected_upload_code: null,
      next_action: "start_sync",
      name: "Halden Slack",
      connector_type: "chat_export",
      status: "active",
      account_scoped: false,
      default_sensitivity: "internal",
      authority: "conversational",
      documents: 96,
      last_synced_at: null,
      last_succeeded_at: null,
      acl_synced_at: null,
      last_run: null,
      failure_codes: [],
      job: null,
    },
  ],
};

export const FAILURES: S["FailuresResponse"] = {
  failures: [
    {
      stage: "map_access",
      code: "access_unmappable:account",
      params: { external_account_ref: "360009913" },
      record_kind: "opaque",
      record: "rec_3f9a0c1d2e4b5a67",
      created_at: "2026-09-28T07:12:43Z",
    },
  ],
};

/** A source whose last run failed as a whole, and whose last requested sync failed too. */
export const SOURCE_FAILED: S["AdminSourceOut"] = {
  ...SOURCES_ADMIN.sources[0]!,
  id: "5d2c1b0a-4444-4e3f-9a8b-7c6d5e4f3a04",
  key: "wiki",
  name: "Halden Wiki",
  documents: 37,
  next_action: "retry_sync",
  last_synced_at: "2026-09-29T06:00:09Z",
  last_succeeded_at: "2026-09-12T06:00:04Z",
  acl_synced_at: "2026-09-12T06:00:04Z",
  last_run: {
    id: "6e1f0a9b-4444-4c2d-8e3f-0a1b2c3d4e04",
    mode: "full",
    status: "failed",
    started_at: "2026-09-29T06:00:01Z",
    finished_at: "2026-09-29T06:00:09Z",
    counts: { ...RUN_COUNTS },
    deletion_guard_tripped: false,
    error_code: "source_unavailable:timeout",
  },
  failure_codes: [],
  job: {
    id: "7f2a1b0c-4444-4d3e-9f4a-1b2c3d4e5f04",
    source_id: "5d2c1b0a-4444-4e3f-9a8b-7c6d5e4f3a04",
    mode: "full",
    status: "failed",
    attempts: 3,
    error_code: "source_unavailable:timeout",
    created_at: "2026-09-29T05:59:58Z",
    started_at: "2026-09-29T06:00:01Z",
    finished_at: "2026-09-29T06:00:09Z",
    sync_run_id: "6e1f0a9b-4444-4c2d-8e3f-0a1b2c3d4e04",
  },
};

/** "Now" for tests that read ages and day headings (the fixtures' week). */
export const FIXTURE_NOW = "2026-09-29T10:00:00Z";

// --- export sources operated in the product (Phase 12, P12.6) ---------------------------------

export const ORG_SOURCE = {
  escalations: "5d2c1b0a-6666-4e3f-9a8b-7c6d5e4f3a06",
  handbook: "5d2c1b0a-7777-4e3f-9a8b-7c6d5e4f3a07",
};

const UPLOAD_LIMITS: S["UploadLimits"] = {
  available: true,
  formats: ["zip", "jsonl"],
  max_upload_bytes: 100 * 1024 * 1024,
  max_expanded_bytes: 512 * 1024 * 1024,
  max_file_bytes: 32 * 1024 * 1024,
  max_entries: 20_000,
};

/** A ready export of tickets whose check found two references, one still without an account. */
export const IMPORT_ESCALATIONS: S["ImportOut"] = {
  id: "8a1b2c3d-6666-4e5f-8a9b-0c1d2e3f4a06",
  status: "ready",
  format: "zip",
  current: true,
  size_bytes: 1_843_200,
  sha256: "9f2c4e6a8b0d1f3e5a7c9e1b3d5f7a9c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f",
  entries: 3,
  ignored_entries: 1,
  expanded_bytes: 7_340_032,
  rejected_code: null,
  created_at: "2026-09-29T09:20:00Z",
  received_at: "2026-09-29T09:20:04Z",
  ready_at: "2026-09-29T09:20:05Z",
  superseded_at: null,
  check: {
    checked_at: "2026-09-29T09:20:09Z",
    error_code: null,
    records_seen: 42,
    would_create: 39,
    would_update: 0,
    would_update_access: 0,
    unchanged: 0,
    invalid_records: 1,
    would_quarantine: 2,
    would_delete: 0,
    deletion_guard: null,
    failure_codes: [
      { code: "access_unmappable:account", count: 2 },
      { code: "malformed_json", count: 1 },
    ],
    unresolved_references: [
      { external_ref: "360009913", records: 2, state: "unmapped" },
      { external_ref: "360001002", records: 5, state: "mapped" },
    ],
    unresolved_total: 2,
  },
};

/** An organization-managed ticketing source with a checked export, never synced. */
export const SOURCE_RECORD_ESCALATIONS: S["SourceDetail"] = {
  id: ORG_SOURCE.escalations,
  key: "escalations",
  connector_type: "ticketing",
  managed_by: "organization",
  status: "active",
  version: 3,
  settings: {
    name: "Halden Escalations",
    default_sensitivity: "internal",
    default_departments: [{ key: "support", name: "Support" }],
    authority: "working",
    account_scoped: true,
    channel_sensitivities: {},
  },
  documents: 0,
  created_at: "2026-09-29T09:15:00Z",
  last_synced_at: null,
  last_succeeded_at: null,
  current_import: IMPORT_ESCALATIONS,
  latest_upload: null,
  job: null,
  next_action: "map_references",
  can_sync: true,
  sync_blocked_by: null,
  upload: UPLOAD_LIMITS,
};

/** A Markdown documents source just added: no export yet. */
export const SOURCE_RECORD_NEW: S["SourceDetail"] = {
  id: ORG_SOURCE.handbook,
  key: "handbook",
  connector_type: "document_repository",
  managed_by: "organization",
  status: "active",
  version: 1,
  settings: {
    name: "Halden Handbook",
    default_sensitivity: "internal",
    default_departments: [],
    authority: "working",
    account_scoped: false,
    channel_sensitivities: {},
  },
  documents: 0,
  created_at: "2026-09-29T09:40:00Z",
  last_synced_at: null,
  last_succeeded_at: null,
  current_import: null,
  latest_upload: null,
  job: null,
  next_action: "upload_import",
  can_sync: false,
  sync_blocked_by: "import_required",
  upload: { ...UPLOAD_LIMITS, formats: ["zip"] },
};

/** The operator-managed Drive source's record (Phase 4 workflow: `lattice sync`). */
export const SOURCE_RECORD_DRIVE: S["SourceDetail"] = {
  id: SOURCE.drive,
  key: "drive",
  connector_type: "document_repository",
  managed_by: "operator",
  status: "active",
  version: 1,
  settings: {
    name: "Halden Drive",
    default_sensitivity: "internal",
    default_departments: [],
    authority: "working",
    account_scoped: false,
    channel_sensitivities: {},
  },
  documents: 58,
  created_at: "2026-09-01T08:00:00Z",
  last_synced_at: "2026-09-28T07:12:40Z",
  last_succeeded_at: "2026-09-28T07:12:40Z",
  current_import: null,
  latest_upload: null,
  job: null,
  next_action: "none",
  can_sync: true,
  sync_blocked_by: null,
  upload: { ...UPLOAD_LIMITS, formats: ["zip"] },
};

/** The register's rows for the two organization-managed sources. */
export const SOURCES_ADMIN_ORG: S["AdminSourceOut"][] = [
  {
    ...SOURCES_ADMIN.sources[1]!,
    id: ORG_SOURCE.escalations,
    key: "escalations",
    name: "Halden Escalations",
    managed_by: "organization",
    version: 3,
    import_status: "ready",
    next_action: "map_references",
    documents: 0,
    last_synced_at: null,
    last_succeeded_at: null,
    acl_synced_at: null,
    last_run: null,
    failure_codes: [],
    job: null,
  },
  {
    ...SOURCES_ADMIN.sources[0]!,
    id: ORG_SOURCE.handbook,
    key: "handbook",
    name: "Halden Handbook",
    managed_by: "organization",
    version: 1,
    import_status: null,
    next_action: "upload_import",
    documents: 0,
    last_synced_at: null,
    last_succeeded_at: null,
    acl_synced_at: null,
    last_run: null,
    failure_codes: [],
    job: null,
  },
];

export const UPLOAD_ACCEPTED: S["UploadResult"] = {
  import: { ...IMPORT_ESCALATIONS, id: "8a1b2c3d-7777-4e5f-8a9b-0c1d2e3f4a07", check: null, entries: 12, ignored_entries: 2 },
  check_queued: true,
};

export const UPLOAD_REFUSED_LAYOUT = {
  error: {
    code: "validation_failed",
    message: "The upload was refused: The archive does not have the structure this source type reads.",
    request_id: "req-upload-fixture",
    details: [{ field: "import", issue: "unsupported_layout" }],
  },
} as const;

export const DISCONNECT_PREVIEW_ESCALATIONS: S["DisconnectPreview"] = {
  documents: 39,
  chunks: 117,
  account_mappings: 1,
  imports: 1,
  queued_jobs: 0,
  sync_running: false,
  operator_managed: false,
};

export const SOURCE_PREVIEW_WIDENS: S["SourceChangePreview"] = {
  changes: [{ field: "default_sensitivity", before: "confidential", after: "internal", effect: "widens" }],
  widens: true,
  takes_effect: "next_sync",
};

// --- activity (Phase 8) --------------------------------------------------------------------

export const ACTIVITY_OWN: S["OwnActivityResponse"] = {
  items: [
    {
      trace_id: TRACE.answered,
      created_at: "2026-09-29T08:41:07Z",
      status: "answered",
      status_reason: null,
      question: "When will SCIM ship for Acme, and what did we tell them?",
    },
    {
      trace_id: TRACE.hidden,
      created_at: "2026-09-28T16:02:55Z",
      status: "partial",
      status_reason: "claims_removed",
      question: "What did Kestrel agree to in the renewal?",
    },
  ],
};

export const ACTIVITY_ADMIN: S["OperationalActivityResponse"] = {
  items: [
    {
      trace_id: TRACE.other,
      created_at: "2026-09-29T08:44:12Z",
      membership_id: "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b55",
      member_name: "Grace Whitfield",
      status: "evidence_only",
      status_reason: "egress_policy",
      error_code: null,
      provider: null,
      model: null,
      duration_ms: 288.0,
      generation_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
    },
    {
      trace_id: TRACE.answered,
      created_at: "2026-09-29T08:41:07Z",
      membership_id: ME_MAYA.membership.id,
      member_name: "Maya Okafor",
      status: "answered",
      status_reason: null,
      error_code: null,
      provider: "gemini",
      model: "gemini-3.5-flash-lite",
      duration_ms: 4213.4,
      generation_calls: 2,
      input_tokens: 2940,
      output_tokens: 311,
    },
  ],
};

// --- accounts and access management (Phase 9) --------------------------------------------------

export const ACCOUNTS_MAYA: S["MemberAccounts"] = {
  account_scope: "assigned",
  accounts: [
    { key: "acme-corp", name: "Acme Corp", archived: false },
    { key: "northpeak-logistics", name: "Northpeak Logistics", archived: false },
  ],
};

export const ID = {
  maya: ME_MAYA.membership.id,
  lena: ME_LENA.membership.id,
  grace: "9e8d7c6b-5a49-4382-9170-6f5e4d3c2b55",
  acme: "ac000000-0000-4000-8000-000000000001",
  northpeak: "ac000000-0000-4000-8000-000000000002",
  kestrel: "ac000000-0000-4000-8000-000000000003",
  sales: "de000000-0000-4000-8000-000000000001",
  leadership: "de000000-0000-4000-8000-000000000002",
  exec: "60000000-0000-4000-8000-000000000001",
  channel: "60000000-0000-4000-8000-000000000002",
  grant: "9a000000-0000-4000-8000-000000000001",
  doc: "d0c00000-0000-4000-8000-000000000001",
};

const SALES = { id: ID.sales, name: "Sales" };

export const MEMBERS_PAGE: S["MembersPage"] = {
  items: [
    {
      id: ID.grace,
      name: "Grace Whitfield",
      email: "grace.whitfield@halden.example",
      role_label: "VP Customer Success",
      status: "active",
      is_admin: false,
      clearance: "confidential",
      account_scope: "all",
      assigned_accounts: 0,
      departments: [{ id: ID.leadership, name: "Leadership" }],
      managed_by: null,
    },
    {
      id: ID.lena,
      name: "Lena Brandt",
      email: "lena.brandt@halden.example",
      role_label: "IT Administrator",
      status: "active",
      is_admin: true,
      clearance: "internal",
      account_scope: "assigned",
      assigned_accounts: 0,
      departments: [],
      managed_by: null,
    },
    {
      id: ID.maya,
      name: "Maya Okafor",
      email: "maya.okafor@halden.example",
      role_label: "Account Manager",
      status: "active",
      is_admin: false,
      clearance: "internal",
      account_scope: "assigned",
      assigned_accounts: 2,
      departments: [SALES],
      managed_by: null,
    },
  ],
  next_after: null,
};

export const DIRECTORY: S["Directory"] = {
  accounts: [
    { id: ID.acme, key: "acme-corp", name: "Acme Corp", status: "active", archived: false },
    { id: ID.kestrel, key: "kestrel-bank", name: "Kestrel Bank", status: "active", archived: false },
    { id: ID.northpeak, key: "northpeak-logistics", name: "Northpeak Logistics", status: "active", archived: false },
  ],
  departments: [
    { id: ID.leadership, key: "leadership", name: "Leadership" },
    { id: ID.sales, key: "sales", name: "Sales" },
  ],
  groups: [{ id: ID.exec, key: "exec-staff", name: "Exec staff", members: 2 }],
};

const HANDBOOK: S["DocumentRef"] = {
  id: ID.doc,
  title: "Employee handbook",
  display_ref: null,
  source_name: "Halden Drive",
  connector_type: "document_repository",
  classification: "internal",
};

const OPEN = { available: true, blocked_by: null } as const;
const blocked = (reason: S["LifecycleAction"]["blocked_by"]): S["LifecycleAction"] => ({ available: false, blocked_by: reason });

/** A member the organization manages (added by hand, or seeded): suspendable here. */
export const LIFECYCLE_ACTIVE: S["MemberLifecycle"] = {
  sign_in_identifier: "00u8maya-okafor",
  identity_managed_by: "organization",
  suspend: OPEN,
  reactivate: null,
  reinstate: null,
  grant_administrator: OPEN,
  revoke_administrator: null,
};

export const MEMBER_MAYA: S["MemberAccess"] = {
  member: MEMBERS_PAGE.items[2]!,
  lifecycle: LIFECYCLE_ACTIVE,
  version: "2026-09-29T08:00:00.123456Z",
  is_self: false,
  accounts: [
    { id: ID.acme, name: "Acme Corp" },
    { id: ID.northpeak, name: "Northpeak Logistics" },
  ],
  departments: [SALES],
  groups: [
    {
      id: ID.exec,
      origin: "admin",
      name: "Exec staff",
      connector_type: null,
      members: [
        { id: ID.grace, name: "Grace Whitfield" },
        { id: ID.maya, name: "Maya Okafor" },
      ],
      editable: true,
    },
  ],
  has_hidden_groups: true,
  grants: [
    {
      id: ID.grant,
      origin: "admin",
      document: HANDBOOK,
      created_at: "2026-09-16T10:00:00Z",
      granted_by: "Lena Brandt",
      reason: "Onboarding cover",
      revocable: true,
    },
  ],
  has_other_grants: true,
};

/** A member provisioned by the identity provider (SCIM, Phase 10): it owns suspension. */
export const MEMBER_PAT_MANAGED: S["MemberAccess"] = {
  member: {
    id: "a1000000-0000-4000-8000-0000000000aa",
    name: "Pat Rivera",
    email: "pat.rivera@halden.example",
    role_label: null,
    status: "active",
    is_admin: false,
    clearance: "internal",
    account_scope: "assigned",
    assigned_accounts: 0,
    departments: [],
    managed_by: "identity_provider",
  },
  lifecycle: {
    sign_in_identifier: "pat.rivera@halden.example",
    identity_managed_by: "identity_provider",
    suspend: blocked("managed_by_identity_provider"),
    reactivate: null,
    reinstate: null,
    grant_administrator: OPEN,
    revoke_administrator: null,
  },
  version: "2026-10-02T09:00:00Z",
  is_self: false,
  accounts: [],
  departments: [],
  groups: [],
  has_hidden_groups: false,
  grants: [],
  has_other_grants: false,
};

export const MEMBER_LENA_SELF: S["MemberAccess"] = {
  member: MEMBERS_PAGE.items[1]!,
  lifecycle: {
    sign_in_identifier: "00u1lena-brandt",
    identity_managed_by: "organization",
    suspend: blocked("own_membership"),
    reactivate: null,
    reinstate: null,
    grant_administrator: null,
    revoke_administrator: blocked("own_membership"),
  },
  version: "2026-09-29T08:00:00Z",
  is_self: true,
  accounts: [],
  departments: [],
  groups: [],
  has_hidden_groups: false,
  grants: [],
  has_other_grants: false,
};

// --- members and administrators (Phase 12, P12.2) -----------------------------------------

export const IDENTITY_REQUIREMENT: S["IdentityRequirement"] = {
  available: true,
  subject_claim: "sub",
  issuer: "https://halden.okta.example",
};

export const IDENTITY_REQUIREMENT_EMAIL: S["IdentityRequirement"] = { ...IDENTITY_REQUIREMENT, subject_claim: "email" };

export const IDENTITY_NOT_CONNECTED: S["IdentityRequirement"] = { available: false, subject_claim: null, issuer: null };

/** Someone an administrator has just added: least access, managed by the organization. */
export const MEMBER_NEW: S["MemberAccess"] = {
  member: {
    id: "a1000000-0000-4000-8000-0000000000bb",
    name: "Sam Achebe",
    email: "sam.achebe@halden.example",
    role_label: null,
    status: "active",
    is_admin: false,
    clearance: "internal",
    account_scope: "assigned",
    assigned_accounts: 0,
    departments: [],
    managed_by: null,
  },
  lifecycle: { ...LIFECYCLE_ACTIVE, sign_in_identifier: "00u9sam-achebe" },
  version: "2026-10-09T09:00:00Z",
  is_self: false,
  accounts: [],
  departments: [],
  groups: [],
  has_hidden_groups: false,
  grants: [],
  has_other_grants: false,
};

/** Grace is an administrator (not the viewer); the capability can be revoked. */
export const MEMBER_GRACE_ADMIN: S["MemberAccess"] = {
  ...MEMBER_NEW,
  member: { ...MEMBERS_PAGE.items[0]!, is_admin: true },
  lifecycle: { ...LIFECYCLE_ACTIVE, sign_in_identifier: "00u3grace", grant_administrator: null, revoke_administrator: OPEN },
};

/** Removed by the identity provider; reinstating starts again from least access. */
export const MEMBER_RAE_REMOVED: S["MemberAccess"] = {
  ...MEMBER_NEW,
  member: {
    ...MEMBER_NEW.member,
    id: "a1000000-0000-4000-8000-0000000000cc",
    name: "Rae Lindqvist",
    email: "rae.lindqvist@halden.example",
    status: "removed",
    clearance: "confidential",
    account_scope: "all",
    managed_by: "identity_provider",
  },
  lifecycle: {
    sign_in_identifier: "00u4rae",
    identity_managed_by: "identity_provider",
    suspend: null,
    reactivate: null,
    reinstate: OPEN,
    grant_administrator: null,
    revoke_administrator: null,
  },
  departments: [SALES],
};

export const MEMBER_RAE_REINSTATED: S["MemberAccess"] = {
  ...MEMBER_RAE_REMOVED,
  member: { ...MEMBER_RAE_REMOVED.member, status: "active", clearance: "internal", account_scope: "assigned" },
  lifecycle: { ...MEMBER_RAE_REMOVED.lifecycle, reinstate: null, suspend: blocked("managed_by_identity_provider"), grant_administrator: OPEN },
  departments: [],
};

export const PREVIEW_REINSTATE: S["AccessPreview"] = {
  effects: [
    { kind: "reinstated", before: null, after: null, subject: null },
    { kind: "administrator", before: "true", after: "false", subject: null },
    { kind: "clearance", before: "confidential", after: "internal", subject: null },
    { kind: "account_scope", before: "all", after: "assigned", subject: null },
    { kind: "department_removed", before: null, after: null, subject: { id: ID.sales, name: "Sales" } },
  ],
  documents_gained: 3,
  documents_lost: 0,
};

/** The audit entry of a member added by hand: least access, and the binding itself. */
export const AUDIT_MEMBER_ADDED: S["AuditPage"] = {
  items: [
    {
      id: "a0d10000-0000-4000-8000-000000000009",
      created_at: "2026-10-09T09:00:00Z",
      action: "access.member_changed",
      actor: { kind: "member", name: "Lena Brandt", operator: null },
      reason: "Joined the support team",
      member: { id: "a1000000-0000-4000-8000-0000000000bb", name: "Sam Achebe" },
      document: null,
      effects: [
        { kind: "created", before: null, after: "active", subject: null },
        { kind: "clearance", before: null, after: "internal", subject: null },
        { kind: "account_scope", before: null, after: "assigned", subject: null },
      ],
      details: [
        { field: "sign_in_identifier", before: null, after: "00u9sam-achebe" },
        { field: "email", before: null, after: "sam.achebe@halden.example" },
      ],
    },
  ],
  next_before: null,
};

export const PREVIEW_ADD_KESTREL: S["AccessPreview"] = {
  effects: [{ kind: "account_added", before: null, after: null, subject: { id: ID.kestrel, name: "Kestrel Bank" } }],
  documents_gained: 1,
  documents_lost: 0,
};

export const PREVIEW_SUSPEND: S["AccessPreview"] = {
  effects: [{ kind: "suspended", before: null, after: null, subject: null }],
  documents_gained: 0,
  documents_lost: 2,
};

export const DOCUMENTS: S["DocumentsPage"] = { items: [HANDBOOK] };

export const EXPLANATION_DENIED: S["DocumentExplanation"] = {
  document: HANDBOOK,
  readable: false,
  path: "none",
  reasons: ["clearance_covers_sensitivity", "not_account_specific", "department_not_matched"],
  via: [],
};

export const AUDIT: S["AuditPage"] = {
  items: [
    {
      id: "a0d10000-0000-4000-8000-000000000002",
      created_at: "2026-10-02T05:31:14Z",
      action: "access.member_changed",
      actor: { kind: "member", name: "Lena Brandt", operator: null },
      reason: "Covering Kestrel during the renewal",
      member: { id: ID.maya, name: "Maya Okafor" },
      document: null,
      effects: [{ kind: "account_added", before: null, after: null, subject: { id: ID.kestrel, name: "Kestrel Bank" } }],
      details: [],
    },
    {
      id: "a0d10000-0000-4000-8000-000000000001",
      created_at: "2026-09-29T09:13:03Z",
      action: "access.grant_created",
      actor: { kind: "operator", name: null, operator: "admin-grants" },
      reason: "Onboarding cover",
      member: { id: ID.maya, name: "Maya Okafor" },
      document: HANDBOOK,
      effects: [],
      details: [],
    },
    {
      id: "a0d10000-0000-4000-8000-000000000004",
      created_at: "2026-09-28T09:02:00Z",
      action: "access.identity_changed",
      actor: { kind: "identity_provider", name: null, operator: null },
      reason: "Updated by the identity provider (SCIM)",
      member: { id: "a1000000-0000-4000-8000-0000000000aa", name: "Pat Rivera" },
      document: null,
      effects: [],
      details: [{ field: "name", before: "Pat Doe", after: "Pat Rivera" }],
    },
    {
      id: "a0d10000-0000-4000-8000-000000000003",
      created_at: "2026-09-28T09:00:00Z",
      action: "access.member_changed",
      actor: { kind: "identity_provider", name: null, operator: null },
      reason: "Provisioned by the identity provider (SCIM)",
      member: { id: "a1000000-0000-4000-8000-0000000000aa", name: "Pat Rivera" },
      document: null,
      effects: [],
      details: [],
    },
  ],
  next_before: null,
};

// --- stress fixtures for the operational surfaces (D5 responsive checks) -----------------------

const LONG_NAME = "Maximiliana Wilhelmina Featherstonehaugh-Ollerenshaw de la Cruz-Abernathy";

/** A source with a very long title and key, beside the usual three. */
export const SOURCES_ADMIN_LONG: S["AdminSourcesResponse"] = {
  sources: [
    ...SOURCES_ADMIN.sources,
    {
      ...SOURCES_ADMIN.sources[1]!,
      id: "5d2c1b0a-5555-4e3f-9a8b-7c6d5e4f3a05",
      key: "customer-success-escalations-and-renewals-archive-emea",
      name: "Customer Success — Escalations, Renewals and Executive Correspondence Archive (EMEA and APAC regions, 2019–2026)",
      job: null,
    },
    SOURCE_FAILED,
  ],
};

/** Forty members, some with very long names and e-mail addresses. */
export const MANY_MEMBERS: S["MembersPage"] = {
  items: Array.from({ length: 40 }, (_, i) => ({
    ...MEMBERS_PAGE.items[i % 3]!,
    id: `b${String(i).padStart(7, "0")}-0000-4000-8000-000000000000`,
    name: i % 7 === 0 ? `${LONG_NAME} ${i}` : `Member ${String(i + 1).padStart(2, "0")}`,
    email: i % 7 === 0 ? `maximiliana.featherstonehaugh-ollerenshaw.${i}@customer-success.halden.example` : `member${i + 1}@halden.example`,
    status: i % 9 === 4 ? ("suspended" as const) : ("active" as const),
    managed_by: i % 5 === 2 ? "identity_provider" : null,
  })),
  next_after: "b0000039-0000-4000-8000-000000000000",
};

/** A member with a long name and many accounts, departments and groups. */
export const MEMBER_WIDE: S["MemberAccess"] = {
  ...MEMBER_MAYA,
  member: { ...MEMBER_MAYA.member, id: "b0000000-0000-4000-8000-000000000000", name: LONG_NAME, email: "maximiliana.featherstonehaugh-ollerenshaw@customer-success.halden.example" },
  accounts: Array.from({ length: 14 }, (_, i) => ({ id: `ac000000-0000-4000-8000-0000000001${String(i).padStart(2, "0")}`, name: `Account ${i + 1} — Northern Europe Holdings` })),
  departments: [SALES, { id: ID.leadership, name: "Leadership" }],
};


/** A long audit log, paged by its cursor. */
export function auditPages(before: string | null, total = 60, size = 25): S["AuditPage"] {
  const all = Array.from({ length: total }, (_, i) => ({
    ...AUDIT.items[i % AUDIT.items.length]!,
    id: `a0d1${String(i).padStart(4, "0")}-0000-4000-8000-000000000000`,
    created_at: new Date(Date.parse("2026-09-29T09:00:00Z") - i * 3_600_000 * 5).toISOString(),
  }));
  const start = before ? all.findIndex((e) => e.id === before) + 1 : 0;
  const items = all.slice(start, start + size);
  return { items, next_before: start + size < all.length ? (items.at(-1)?.id ?? null) : null };
}

/** A change that both widens and narrows: clearance up, an account added, a department removed. */
export const PREVIEW_MIXED: S["AccessPreview"] = {
  effects: [
    { kind: "clearance", before: "internal", after: "confidential", subject: null },
    { kind: "account_added", before: null, after: null, subject: { id: ID.kestrel, name: "Kestrel Bank" } },
    { kind: "department_removed", before: null, after: null, subject: { id: ID.sales, name: "Sales" } },
  ],
  documents_gained: 7,
  documents_lost: 3,
};

// --- organization setup (Phase 12, P12.1) -------------------------------------------------

const SETUP_ORGANIZATION: S["SetupOrganization"] = {
  slug: "halden",
  name: "Halden",
  status: "active",
  data_origin: "synthetic",
  version: "2026-10-08T09:00:00.000000Z",
};

/** A freshly created organization: the operator's trust root, nothing else yet. */
export const SETUP_FRESH: S["SetupStatus"] = {
  organization: SETUP_ORGANIZATION,
  categories: [
    { key: "organization", state: "attention", reason: "single_administrator", message: "One active administrator. If they leave, only your Lattice operator can appoint another; a second administrator avoids that.", facts: { active_administrators: 1 }, action: null },
    { key: "identity", state: "required", reason: "not_verified", message: "The identity provider is connected but no sign-in through it has succeeded yet. A test sign-in on the Identity page verifies it.", facts: { connection: "active", last_success_at: null, last_failure_at: null }, action: { kind: "link", href: "/settings/identity", label: "Test sign-in" } },
    { key: "members", state: "required", reason: "only_administrators", message: "Only administrators are members so far. People join when your identity provider provisions them (SCIM) or when your Lattice operator adds them.", facts: { active_members: 1, active_administrators: 1 }, action: null },
    { key: "provisioning", state: "optional", reason: "not_configured", message: "Automatic provisioning (SCIM) is not set up. It is optional; create a provisioning token on the Identity page to use it.", facts: { live_tokens: 0, provisioned_members: 0, refused_recently: 0 }, action: { kind: "link", href: "/settings/identity", label: "Open Identity" } },
    { key: "ai_policy", state: "optional", reason: "evidence_only", message: "No generation provider is enabled: answers show their evidence without a written answer.", facts: { provider: null, model: null, default_question_sensitivity: "internal" }, action: { kind: "link", href: "/settings/providers", label: "Open AI providers" } },
    { key: "sources", state: "required", reason: "no_sources", message: "No source is connected yet.", facts: { connected_sources: 0, synced_sources: 0, failed_sources: 0, syncing_sources: 0 }, action: null },
    { key: "accounts", state: "optional", reason: "not_needed", message: "No source maps records to accounts, so no account mapping is needed.", facts: { account_sources: 0, unmapped_references: 0 }, action: null },
    { key: "first_answer", state: "blocked", reason: "sources_required", message: "The first question can be asked once a source has synced.", facts: { answered: false }, action: null },
  ],
};

/** An organization in use, with two items that need attention. */
export const SETUP_IN_USE: S["SetupStatus"] = {
  organization: SETUP_ORGANIZATION,
  categories: [
    { key: "organization", state: "complete", reason: "active", message: "The organization is active and has more than one administrator.", facts: { active_administrators: 2 }, action: null },
    { key: "identity", state: "complete", reason: "verified", message: "Sign-in through the identity provider works.", facts: { connection: "active", last_success_at: "2026-10-08T08:41:07Z", last_failure_at: null }, action: { kind: "link", href: "/settings/identity", label: "Review identity" } },
    { key: "members", state: "complete", reason: "members_active", message: "Members other than administrators are active.", facts: { active_members: 14, active_administrators: 2 }, action: { kind: "link", href: "/access", label: "Review access" } },
    { key: "provisioning", state: "complete", reason: "in_use", message: "The identity provider provisions members.", facts: { live_tokens: 1, provisioned_members: 12, refused_recently: 0 }, action: { kind: "link", href: "/settings/identity", label: "Open Identity" } },
    { key: "ai_policy", state: "attention", reason: "credential_failed", message: "The generation provider's credential is missing or cannot be used: answers fall back to evidence only.", facts: { provider: "gemini", model: "gemini-3.5-flash-lite", default_question_sensitivity: "internal" }, action: { kind: "link", href: "/settings/providers", label: "Open AI providers" } },
    { key: "sources", state: "complete", reason: "synced", message: "At least one source has synced.", facts: { connected_sources: 3, synced_sources: 3, failed_sources: 0, syncing_sources: 0 }, action: { kind: "link", href: "/sources", label: "Open Sources" } },
    { key: "accounts", state: "attention", reason: "unmapped_references", message: "Records reference accounts that are not mapped, and are not indexed until they are.", facts: { account_sources: 1, unmapped_references: 2 }, action: { kind: "link", href: "/sources", label: "See unmapped references" } },
    { key: "first_answer", state: "complete", reason: "answered", message: "Members have received answers.", facts: { answered: true }, action: null },
  ],
};

// --- accounts and external-reference mappings (Phase 12, P12.3) ---------------------------------

export const ACCOUNT_IDS = {
  acme: ID.acme,
  kestrel: ID.kestrel,
  orbital: "ac000000-0000-4000-8000-000000000008",
  fernhill: "ac000000-0000-4000-8000-000000000009",
};

const SUPPORT_REF: S["SourceRef"] = { id: SOURCE.support, name: "Halden Support (Zendesk)", connector_type: "ticketing" };
const SLACK_REF: S["SourceRef"] = { id: SOURCE.slack, name: "Halden Slack", connector_type: "chat_export" };

export const ACCOUNT_ACME: S["AccountDetail"] = {
  id: ACCOUNT_IDS.acme,
  key: "acme-corp",
  name: "Acme Corp",
  aliases: ["Acme", "ACME Corp"],
  status: "active",
  lifecycle: "active",
  archived_at: null,
  references: 3,
  assigned_members: 2,
  version: "2026-10-12T09:00:00.000000Z",
  created_at: "2026-09-27T08:00:00Z",
  mappings: [
    { source: SLACK_REF, ref_kind: "chat_channel", external_ref: "C0EXTACME01", created_at: "2026-09-27T08:00:00Z", updated_at: "2026-09-27T08:00:00Z" },
    { source: SLACK_REF, ref_kind: "chat_channel", external_ref: "G0ACCTACME1", created_at: "2026-09-27T08:00:00Z", updated_at: "2026-09-27T08:00:00Z" },
    { source: SUPPORT_REF, ref_kind: "ticketing_organization", external_ref: "360001001", created_at: "2026-09-27T08:00:00Z", updated_at: "2026-09-27T08:00:00Z" },
  ],
};

export const ACCOUNT_ORBITAL_ARCHIVED: S["AccountDetail"] = {
  id: ACCOUNT_IDS.orbital,
  key: "orbital-freight",
  name: "Orbital Freight",
  aliases: ["Orbital"],
  status: "prospect",
  lifecycle: "archived",
  archived_at: "2026-10-10T12:00:00Z",
  references: 1,
  assigned_members: 0,
  version: "2026-10-10T12:00:00.000000Z",
  created_at: "2026-09-27T08:00:00Z",
  mappings: [
    { source: SUPPORT_REF, ref_kind: "ticketing_organization", external_ref: "360001008", created_at: "2026-09-27T08:00:00Z", updated_at: "2026-09-27T08:00:00Z" },
  ],
};

export const ACCOUNT_FERNHILL: S["AccountDetail"] = {
  id: ACCOUNT_IDS.fernhill,
  key: "fernhill-studios",
  name: "Fernhill Studios",
  aliases: [],
  status: "active",
  lifecycle: "active",
  archived_at: null,
  references: 0,
  assigned_members: 0,
  version: "2026-10-12T10:00:00.000000Z",
  created_at: "2026-10-12T10:00:00Z",
  mappings: [],
};

const summary = ({ version: _v, created_at: _c, mappings: _m, ...rest }: S["AccountDetail"]): S["AccountSummary"] => rest;

export const ACCOUNTS_PAGE: S["AccountsPage"] = {
  items: [
    summary(ACCOUNT_ACME),
    summary({
      ...ACCOUNT_ACME,
      id: ACCOUNT_IDS.kestrel,
      key: "kestrel-bank",
      name: "Kestrel Bank",
      aliases: ["Kestrel"],
      references: 2,
      assigned_members: 1,
    }),
    summary(ACCOUNT_ORBITAL_ARCHIVED),
  ],
  next_after: null,
};

export const UNMAPPED: S["UnmappedReferences"] = {
  items: [
    {
      source: SUPPORT_REF,
      ref_kind: "ticketing_organization",
      external_ref: "360009913",
      records_waiting: 1,
      seen_at: "2026-10-12T08:30:00Z",
      state: "unmapped",
      account: null,
    },
  ],
};

export const PREVIEW_MAP_FERNHILL: S["MappingPreview"] = {
  operation: "map",
  source: SUPPORT_REF,
  ref_kind: "ticketing_organization",
  external_ref: "360009913",
  account_scoped: true,
  now: null,
  after: { id: ACCOUNT_IDS.fernhill, key: "fernhill-studios", name: "Fernhill Studios", lifecycle: "active" },
  effect: "indexed",
  records_waiting: 1,
  takes_effect: "next_sync",
};

export const PREVIEW_REMAP_ACME_KESTREL: S["MappingPreview"] = {
  operation: "remap",
  source: SUPPORT_REF,
  ref_kind: "ticketing_organization",
  external_ref: "360001001",
  account_scoped: true,
  now: { id: ACCOUNT_IDS.acme, key: "acme-corp", name: "Acme Corp", lifecycle: "active" },
  after: { id: ACCOUNT_IDS.kestrel, key: "kestrel-bank", name: "Kestrel Bank", lifecycle: "active" },
  effect: "moved",
  records_waiting: 0,
  takes_effect: "next_sync",
};

export const PREVIEW_UNMAP_CHANNEL: S["MappingPreview"] = {
  operation: "unmap",
  source: SLACK_REF,
  ref_kind: "chat_channel",
  external_ref: "C0EXTACME01",
  account_scoped: false,
  now: { id: ACCOUNT_IDS.acme, key: "acme-corp", name: "Acme Corp", lifecycle: "active" },
  after: null,
  effect: "quarantined",
  records_waiting: 0,
  takes_effect: "next_sync",
};

// --- identity administration (Phase 12, P12.4) ---------------------------------------------------

export const CONNECTION_ID = "6f1c2a40-5d8e-4b9a-9c3e-1a2b3c4d5e6f";
export const TOKEN_IDS = { live: "7a2d3b51-6e9f-4cab-8d4f-2b3c4d5e6f70", revoked: "8b3e4c62-7fa0-4dbc-9e50-3c4d5e6f7081" };

export const OIDC_CONNECTION: S["OidcConnectionOut"] = {
  id: CONNECTION_ID,
  status: "active",
  issuer: "https://login.halden.example",
  client_id: "lattice-halden",
  subject_claim: "sub",
  client_secret: { state: "encrypted", set_at: "2026-10-09T10:15:00Z" },
  issuer_access: "public",
  sign_in_url: "https://lattice.halden.example/sso/halden-login-key-0123456789",
  redirect_uri: "https://lattice.halden.example/api/v1/auth/oidc/halden-login-key-0123456789/callback",
  last_success_at: "2026-10-12T08:41:07Z",
  last_failure: null,
  last_test: { kind: "sign_in", result: "matched", at: "2026-10-12T08:41:07Z" },
  members_bound: 14,
  live_sessions: 6,
  version: "2026-10-12T08:00:00.123456Z",
};

export const SCIM_LIVE_TOKEN: S["ScimTokenOut"] = {
  id: TOKEN_IDS.live,
  hint: "Qx7f",
  created_at: "2026-10-09T10:20:00Z",
  revoked_at: null,
  last_used_at: "2026-10-12T09:03:00Z",
  current_connection: true,
};

export const SCIM_REVOKED_TOKEN: S["ScimTokenOut"] = {
  id: TOKEN_IDS.revoked,
  hint: null,
  created_at: "2026-10-01T09:00:00Z",
  revoked_at: "2026-10-09T10:21:00Z",
  last_used_at: "2026-10-09T10:00:00Z",
  current_connection: true,
};

export const IDENTITY_VERIFIED: S["IdentitySettings"] = {
  oidc: OIDC_CONNECTION,
  can_add_connection: false,
  sign_in_status: { state: "complete", reason: "verified", message: "Sign-in through the identity provider works." },
  scim: {
    base_url: "https://lattice.halden.example/api/scim/v2",
    identifying_claim: "sub",
    tokens: [SCIM_LIVE_TOKEN, SCIM_REVOKED_TOKEN],
    provisioned_members: 12,
    refused_recently: 0,
    status: { state: "complete", reason: "in_use", message: "The identity provider provisions members." },
  },
};

/** An organization whose connection a private, unapproved issuer blocks. */
export const IDENTITY_BLOCKED: S["IdentitySettings"] = {
  ...IDENTITY_VERIFIED,
  oidc: { ...OIDC_CONNECTION, issuer: "https://idp.corp.example:9443", issuer_access: "not_permitted", last_success_at: null, last_test: { kind: "check", result: "outbound_blocked", at: "2026-10-12T09:00:00Z" } },
  sign_in_status: {
    state: "blocked",
    reason: "outbound_blocked",
    message: "Lattice's outbound policy refuses the identity provider's address. A private or self-hosted provider must be approved for this organization by your Lattice operator.",
  },
  scim: { ...IDENTITY_VERIFIED.scim, tokens: [], provisioned_members: 0, status: { state: "optional", reason: "not_configured", message: "Automatic provisioning (SCIM) is not set up. It is optional; create a provisioning token on the Identity page to use it." } },
};

export const IDENTITY_DISABLED: S["IdentitySettings"] = {
  ...IDENTITY_VERIFIED,
  oidc: { ...OIDC_CONNECTION, status: "disabled", live_sessions: 0 },
  sign_in_status: {
    state: "attention",
    reason: "connection_disabled",
    message: "The identity provider connection is disabled: nobody can sign in through it. It can be re-enabled on the Identity page.",
  },
};

export const IDENTITY_NONE: S["IdentitySettings"] = {
  oidc: null,
  can_add_connection: true,
  sign_in_status: { state: "required", reason: "no_connection", message: "No identity provider is connected, so nobody can sign in. Connect one on the Identity page." },
  scim: {
    base_url: "https://lattice.halden.example/api/scim/v2",
    identifying_claim: null,
    tokens: [],
    provisioned_members: 0,
    refused_recently: 0,
    status: { state: "optional", reason: "not_configured", message: "Automatic provisioning (SCIM) is not set up. It is optional; create a provisioning token on the Identity page to use it." },
  },
};

export const REVEALED_TOKEN: S["RevealedScimToken"] = {
  token: { ...SCIM_LIVE_TOKEN, id: "9c4f5d73-80b1-4ecd-8f61-4d5e6f708192", hint: "Zk9w", last_used_at: null, created_at: "2026-10-13T10:00:00Z" },
  revealed_token: "lsc_EXAMPLE-not-a-real-token-only-for-tests-Zk9w",
};

export const PROVISIONING_ACTIVITY: S["AuditPage"] = {
  items: [
    {
      id: "a1b2c3d4-0000-4000-8000-000000000001",
      created_at: "2026-10-12T09:03:00Z",
      action: "scim.request_refused",
      actor: { kind: "identity_provider", name: null, operator: null },
      reason: "Provisioning request refused (SCIM)",
      member: { id: ID.maya, name: "Maya Okafor" },
      document: null,
      effects: [],
      details: [
        { field: "operation", before: null, after: "create" },
        { field: "token_id", before: null, after: TOKEN_IDS.live },
        { field: "reason", before: null, after: "manual_member_conflict" },
        { field: "scim_type", before: null, after: "uniqueness" },
        { field: "status", before: null, after: "409" },
      ],
    },
  ],
  next_before: null,
};
