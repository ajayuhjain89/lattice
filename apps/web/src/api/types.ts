/**
 * Named aliases over the generated OpenAPI types (schema.gen.ts). The backend's schema is
 * the contract; nothing here adds fields or reinterprets them.
 */
import type { components } from "./schema.gen";

type Schemas = components["schemas"];

export type Me = Schemas["MeResponse"];
export type AskRequest = Schemas["AskRequest"];
export type RetrievalScope = Schemas["RetrievalScope"];
export type Answer = Schemas["Answer"];
export type AnswerStatus = Answer["status"];
export type Claim = Schemas["Claim"];
export type Citation = Schemas["Citation"];
export type Conflict = Schemas["Conflict"];
export type ConflictPosition = Schemas["ConflictPosition"];
export type EvidenceItem = Schemas["EvidenceItem"];
export type EvidenceDetail = Schemas["EvidenceDetail"];
export type Notice = Schemas["Notice"];
export type Clarification = Schemas["Clarification"];
export type Freshness = Schemas["Freshness"];
export type AskerTrace = Schemas["AskerTrace"];
export type AdminTrace = Schemas["AdminTrace"];
export type Trace = AskerTrace | AdminTrace;
export type ProvidersResponse = Schemas["ProvidersResponse"];
export type ProviderOut = Schemas["ProviderOut"];
export type EgressOut = Schemas["EgressOut"];
export type ErrorBody = Schemas["ErrorBody"];
export type ErrorCode = Schemas["ErrorCode"];
export type SourceType = NonNullable<RetrievalScope["source_types"]>[number];
export type Level = NonNullable<AskRequest["classification"]>;

/** Every answer status the API can return, in the order the UI documents them. */
export const ANSWER_STATUSES = [
  "answered",
  "partial",
  "insufficient_evidence",
  "evidence_only",
  "ambiguous",
  "unsupported_request",
  "failed",
] as const satisfies readonly AnswerStatus[];

export const SOURCE_TYPES = ["doc", "ticket", "chat"] as const satisfies readonly SourceType[];
export const LEVELS = ["internal", "confidential", "restricted"] as const satisfies readonly Level[];
