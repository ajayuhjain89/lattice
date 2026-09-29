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
  evidence: [{ ...I.ticket, used_for_answer: false }, { ...I.kickoff, used_for_answer: false }],
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

// --- evidence details (GET /v1/evidence/{id}) -----------------------------------------------

function detail(e: Evidence, charStart = 0): Detail {
  const { rank: _rank, used_for_answer: _used, not_used_reason: _reason, ...rest } = e;
  return { ...rest, char_start: charStart, char_end: charStart + e.text.length };
}

export const DETAILS: Record<string, Detail> = Object.fromEntries(
  [I.kickoff, I.ticket, I.decision, I.escalation, I.slack, I.roadmap, REFUND_V1, REFUND_V2].map((e, i) => [e.evidence_id, detail(e, i * 311)]),
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

export const PROVIDERS: S["ProvidersResponse"] = {
  egress: {
    external_egress_level: "internal",
    organization_controlled_level: null,
    default_query_sensitivity: "internal",
    data_origin: "synthetic",
  },
  providers: [
    {
      purpose: "generation",
      provider: "ollama",
      model: "qwen3.5:4b",
      deployment: "local",
      approved_for_level: "confidential",
      explicitly_approved: true,
      enabled: true,
      endpoint_configured: false,
      retention_mode: "none",
      training_opt_out: true,
      credential: { state: "none", source: null, resolvable: false, data_use: null },
    },
    {
      purpose: "generation",
      provider: "gemini",
      model: "gemini-3.5-flash-lite",
      deployment: "external_api",
      approved_for_level: "internal",
      explicitly_approved: true,
      enabled: false,
      endpoint_configured: false,
      retention_mode: "provider_default",
      training_opt_out: false,
      credential: { state: "active", source: "env", resolvable: false, data_use: "unpaid" },
    },
    {
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
      credential: { state: "none", source: null, resolvable: false, data_use: null },
    },
  ],
};

export const NOT_FOUND = {
  error: { code: "not_found", message: "Not found.", request_id: "req-404-fixture" },
} as const;
