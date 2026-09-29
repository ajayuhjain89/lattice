/**
 * The trace's free-form fields (`answer`, `timings_ms`, `validation`, `usage`, `scope`)
 * are untyped objects in the OpenAPI contract. They are read here, once, defensively:
 * anything that does not have the expected shape is treated as absent -- never guessed.
 */
import type { AskerTrace, Claim, Conflict, Trace } from "@/api/types";

export interface StoredAnswer {
  status: string;
  message: string | null;
  claims: Claim[];
  conflicts: Conflict[];
  unknowns: string[];
}

export interface TraceTimings {
  retrieval: number | null;
  policy: number | null;
  generation: number | null;
  total: number | null;
}

export interface TraceValidation {
  generationCalls: number | null;
  regenerated: boolean | null;
  schemaFailures: number | null;
  claimsProposed: number | null;
  claimsValid: number | null;
  claimsRejected: number | null;
  rejectionCodes: [string, number][];
  conflictsProposed: number | null;
  conflictsDiscarded: number | null;
  modelStatus: string | null;
}

export interface TraceScope {
  account: string | null;
  sourceTypes: string[];
  since: string | null;
  until: string | null;
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

export function isAskerTrace(trace: Trace): trace is AskerTrace {
  return trace.view === "asker";
}

function isCitation(v: unknown): boolean {
  return (
    isObj(v) &&
    typeof v.evidence_id === "string" &&
    typeof v.document_id === "string" &&
    typeof v.quote === "string" &&
    typeof v.quote_start === "number" &&
    typeof v.quote_end === "number"
  );
}

function isClaim(v: unknown): v is Claim {
  return (
    isObj(v) &&
    typeof v.text === "string" &&
    (v.role === "direct_answer" || v.role === "supporting") &&
    Array.isArray(v.citations) &&
    v.citations.every(isCitation)
  );
}

function isConflict(v: unknown): v is Conflict {
  return (
    isObj(v) &&
    (v.kind === "superseded" || v.kind === "potential_conflict") &&
    typeof v.summary === "string" &&
    Array.isArray(v.positions) &&
    v.positions.every((p) => isObj(p) && isClaim(p.claim) && typeof p.title === "string")
  );
}

/** The stored answer, or null when hidden, absent or not in the expected shape. */
export function storedAnswer(trace: AskerTrace): StoredAnswer | null {
  if (trace.answer_hidden || !isObj(trace.answer)) return null;
  const a = trace.answer;
  const claims = Array.isArray(a.claims) ? a.claims : null;
  const conflicts = Array.isArray(a.conflicts) ? a.conflicts : [];
  if (!claims || !claims.every(isClaim) || !conflicts.every(isConflict)) return null;
  return {
    status: str(a.status) ?? trace.status,
    message: str(a.message),
    claims,
    conflicts,
    unknowns: Array.isArray(a.unknowns) ? a.unknowns.filter((u): u is string => typeof u === "string") : [],
  };
}

export function traceTimings(trace: Trace): TraceTimings {
  const t: Obj = isObj(trace.timings_ms) ? trace.timings_ms : {};
  return {
    retrieval: num(t.retrieval_total),
    policy: num(t.policy),
    generation: num(t.generation),
    total: num(t.total),
  };
}

export function traceValidation(trace: AskerTrace): TraceValidation {
  const v: Obj = isObj(trace.validation) ? trace.validation : {};
  const codes = isObj(v.rejection_codes)
    ? Object.entries(v.rejection_codes).flatMap(([k, n]) => (typeof n === "number" ? [[k, n] as [string, number]] : []))
    : [];
  return {
    generationCalls: num(v.generation_calls),
    regenerated: bool(v.regenerated),
    schemaFailures: num(v.schema_failures),
    claimsProposed: num(v.claims_proposed),
    claimsValid: num(v.claims_valid),
    claimsRejected: num(v.claims_rejected),
    rejectionCodes: codes,
    conflictsProposed: num(v.conflicts_proposed),
    conflictsDiscarded: num(v.conflicts_discarded),
    modelStatus: str(v.model_status),
  };
}

export function traceUsage(trace: Trace): { input: number | null; output: number | null } {
  const u: Obj = isObj(trace.usage) ? trace.usage : {};
  return { input: num(u.input_tokens), output: num(u.output_tokens) };
}

export function traceScope(trace: AskerTrace): TraceScope {
  const s: Obj = isObj(trace.scope) ? trace.scope : {};
  return {
    account: str(s.account),
    sourceTypes: Array.isArray(s.source_types) ? s.source_types.filter((x): x is string => typeof x === "string") : [],
    since: str(s.since),
    until: str(s.until),
  };
}

/** Plain-language descriptions of the generation decision codes (providers.egress). */
export const DECISION_REASON: Record<string, string> = {
  not_configured: "No generation provider is configured for the organization.",
  provider_disabled: "The organization's generation provider is disabled.",
  credential_unavailable: "The provider's credential could not be resolved.",
  question_classification: "The question's sensitivity may not be sent to this provider.",
  deployment_unknown: "The provider's trust class is unknown, so nothing is sent.",
  provider_deployment_mismatch: "The provider's declared trust class does not match its record.",
  deployment_not_allowed: "The organization does not allow this trust class.",
  provider_unapproved: "The provider is not approved for this data.",
  provider_data_use: "The provider's data-use terms are not acceptable for this data.",
  policy_unrecognised: "The organization's AI data policy has an unrecognised value.",
};

export const REJECTION_CODE: Record<string, string> = {
  unknown_handle: "Cited an evidence handle that does not exist",
  quote_not_found: "Quote not found verbatim in the cited evidence",
  quote_too_short: "Quote too short to verify",
  quote_too_long: "Quote longer than allowed",
  no_citations: "Claim without a citation",
  too_many_citations: "Too many citations on one claim",
  claim_too_long: "Claim longer than allowed",
  unsupported_number: "Number not present in the cited evidence",
  no_shared_terms: "Claim shares no terms with its quotes",
  unsafe_content: "Link, address or markup in the claim",
  malformed_claim: "Claim did not match the schema",
  conflict_invalid: "Conflict without two valid positions from two sources",
};
