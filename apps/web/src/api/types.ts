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
// AI providers, BYOK and egress (Phase 12, P12.5).
export type ProviderSettings = Schemas["ProviderSettings"];
export type ProviderOut = Schemas["ProviderOut"];
export type EgressSettings = Schemas["EgressSettingsOut"];
export type ProviderCredential = Schemas["CredentialOut"];
export type ProviderTest = Schemas["LastTestOut"];
export type ProviderTestResult = ProviderTest["result"];
export type Destination = Schemas["DestinationOut"];
export type EgressCell = Schemas["CellOut"];
export type GenerationTarget = Schemas["TargetOut"];
export type SupportedProvider = Schemas["SupportedProviderOut"];
export type StepUpAvailability = Schemas["StepUpAvailability"];
export type AiSetupStatus = Schemas["AiSetupStatus"];
export type SettingsChange = Schemas["SettingsChange"];
export type ProviderChange = Schemas["ProviderChange"];
export type EgressChange = SettingsChange | ProviderChange;
export type ChangePreview = Schemas["ChangePreview"];
export type Transition = Schemas["Transition"];
export type ChangeRequest = Schemas["ChangeRequest"];
export type StepUpRequest = Schemas["StepUpRequest"];
export type StepUpStarted = Schemas["StepUpStarted"];
export type StepUp = Schemas["StepUpOut"];
export type StepUpOutcome = StepUp["outcome"];
export type NewProviderRequest = Schemas["NewProviderRequest"];
export type EndpointRequest = Schemas["EndpointRequest"];
export type CredentialRequest = Schemas["CredentialRequest"];
export type RevokeCredentialRequest = Schemas["RevokeCredentialRequest"];
export type ProviderTestRun = Schemas["TestOut"];
export type MemberSourcesResponse = Schemas["MemberSourcesResponse"];
export type MemberSource = Schemas["MemberSourceOut"];
export type AdminSourcesResponse = Schemas["AdminSourcesResponse"];
export type AdminSource = Schemas["AdminSourceOut"];
export type SyncJob = Schemas["JobOut"];
export type FailuresResponse = Schemas["FailuresResponse"];
// Export sources operated in the product (Phase 12, P12.6).
export type SourceDetail = Schemas["SourceDetail"];
export type SourceSettings = Schemas["SourceSettings"];
export type SourceImport = Schemas["ImportOut"];
export type ImportCheck = Schemas["ImportCheck"];
export type UnresolvedReference = Schemas["UnresolvedReference"];
export type UploadLimits = Schemas["UploadLimits"];
export type SourceJob = Schemas["SourceJob"];
export type NextAction = SourceDetail["next_action"];
export type ConnectorType = SourceDetail["connector_type"];
export type NewSourceRequest = Schemas["NewSourceRequest"];
export type SourceChangeRequest = Schemas["SourceChangeRequest"];
export type SourceChangePreview = Schemas["SourceChangePreview"];
export type SettingChange = Schemas["SettingChange"];
export type SourceLifecycleRequest = Schemas["VersionedRequest"];
export type DisconnectRequest = Schemas["DisconnectRequest"];
export type DisconnectPreview = Schemas["DisconnectPreview"];
export type DisconnectResult = Schemas["DisconnectResult"];
export type UploadResult = Schemas["UploadResult"];
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

// Identity administration (Phase 12, P12.4): the OIDC connection and SCIM tokens.
export type IdentitySettings = Schemas["IdentitySettings"];
export type OidcConnection = Schemas["OidcConnectionOut"];
export type ScimSettings = Schemas["ScimSettings"];
export type ScimToken = Schemas["ScimTokenOut"];
export type IdentitySetupStatus = Schemas["IdentitySetupStatus"];
export type StatusPreview = Schemas["StatusPreview"];
export type IdentityChangePreview = Schemas["IdentityChangePreview"];
export type ConnectionCheck = Schemas["CheckOut"];
export type TestSignIn = Schemas["TestSignInOut"];
export type TestSignInStarted = Schemas["TestSignInStarted"];
export type TestOutcome = NonNullable<TestSignIn["outcome"]>;
export type RevealedScimToken = Schemas["RevealedScimToken"];
export type RevokePreview = Schemas["RevokePreview"];
export type NewConnectionRequest = Schemas["NewConnectionRequest"];
export type ClientIdRequest = Schemas["ClientIdRequest"];
export type ClientSecretRequest = Schemas["ClientSecretRequest"];
export type RemoveSecretRequest = Schemas["RemoveSecretRequest"];
export type StatusRequest = Schemas["StatusRequest"];
export type IdentityChangeRequest = Schemas["IdentityChangeRequest"];
export type TestSignInRequest = Schemas["TestSignInRequest"];
