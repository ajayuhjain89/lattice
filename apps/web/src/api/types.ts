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
export type MemberSourcesResponse = Schemas["MemberSourcesResponse"];
export type MemberSource = Schemas["MemberSourceOut"];
export type AdminSourcesResponse = Schemas["AdminSourcesResponse"];
export type AdminSource = Schemas["AdminSourceOut"];
export type SyncJob = Schemas["JobOut"];
export type FailuresResponse = Schemas["FailuresResponse"];
export type SyncFailure = Schemas["FailureOut"];
export type OwnActivityResponse = Schemas["OwnActivityResponse"];
export type OwnActivity = Schemas["OwnActivityOut"];
export type OperationalActivityResponse = Schemas["OperationalActivityResponse"];
export type OperationalActivity = Schemas["OperationalActivityOut"];
export type MemberAccounts = Schemas["MemberAccounts"];
export type VisibleAccount = Schemas["VisibleAccount"];
export type MembersPage = Schemas["MembersPage"];
export type MemberSummary = Schemas["MemberSummary"];
export type Directory = Schemas["Directory"];
export type MemberAccess = Schemas["MemberAccess"];
export type GroupAccess = Schemas["GroupAccess"];
export type GrantOut = Schemas["GrantOut"];
export type DocumentRef = Schemas["DocumentRef"];
export type MemberChange = Schemas["MemberChange"];
export type MemberChangeRequest = Schemas["MemberChangeRequest"];
export type AccessPreview = Schemas["AccessPreview"];
// Members and administrators (Phase 12, P12.2).
export type IdentityRequirement = Schemas["IdentityRequirement"];
export type NewMemberRequest = Schemas["NewMemberRequest"];
export type MemberLifecycle = Schemas["MemberLifecycle"];
export type LifecycleAction = Schemas["LifecycleAction"];
export type LifecycleBlock = NonNullable<LifecycleAction["blocked_by"]>;
export type AdministratorPreview = Schemas["AdministratorPreview"];
export type AdministratorRequest = Schemas["AdministratorRequest"];
export type ReinstateRequest = Schemas["ReinstateRequest"];
export type AccessEffect = Schemas["AccessEffect"];
export type DocumentsPage = Schemas["DocumentsPage"];
export type DocumentExplanation = Schemas["DocumentExplanation"];
export type AuditPage = Schemas["AuditPage"];
export type AuditEvent = Schemas["AuditEventOut"];
export type ErrorBody = Schemas["ErrorBody"];
export type ErrorCode = Schemas["ErrorCode"];
export type SourceType = NonNullable<RetrievalScope["source_types"]>[number];
export type Level = NonNullable<AskRequest["classification"]>;

export const SOURCE_TYPES = ["doc", "ticket", "chat"] as const satisfies readonly SourceType[];
export const LEVELS = ["internal", "confidential", "restricted"] as const satisfies readonly Level[];

// Organization setup (Phase 12, P12.1): the backend-owned setup record.
export type SetupStatus = Schemas["SetupStatus"];
export type SetupCategory = Schemas["SetupCategory"];
export type SetupState = SetupCategory["state"];
export type SetupCategoryKey = SetupCategory["key"];
export type SetupOrganization = Schemas["SetupOrganization"];
export type OrganizationChange = Schemas["OrganizationChange"];

// Accounts and external-reference mappings (Phase 12, P12.3).
export type AccountsPage = Schemas["AccountsPage"];
export type AccountSummary = Schemas["AccountSummary"];
export type AccountDetail = Schemas["AccountDetail"];
export type AccountMapping = Schemas["AccountMapping"];
export type AccountRef = Schemas["AccountRef"];
export type SourceRef = Schemas["SourceRef"];
export type NewAccountRequest = Schemas["NewAccountRequest"];
export type AccountChangeRequest = Schemas["AccountChangeRequest"];
export type AccountLifecycleRequest = Schemas["AccountLifecycleRequest"];
export type BusinessStatus = AccountSummary["status"];
export type AccountLifecycle = AccountSummary["lifecycle"];
export type UnmappedReferences = Schemas["UnmappedReferences"];
export type UnmappedReference = Schemas["UnmappedReference"];
export type RefKind = UnmappedReference["ref_kind"];
export type MappingPreviewRequest = Schemas["MappingPreviewRequest"];
export type MappingPreview = Schemas["MappingPreview"];
export type MappingEffect = MappingPreview["effect"];
export type MappingResult = Schemas["MappingResult"];
export type MapRequest = Schemas["MapRequest"];
export type RemapRequest = Schemas["RemapRequest"];
export type UnmapRequest = Schemas["UnmapRequest"];
