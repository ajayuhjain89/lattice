/**
 * Centralized server state. Every read goes through these hooks; every key lives here.
 *
 * Evidence and traces are re-authorized by the API on every read, so the UI never keeps
 * them once no component shows them (gcTime 0) and always asks again when one mounts:
 * nothing the API stops returning can be resurrected from a client cache.
 */
import {
  QueryClient,
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClientConfig,
} from "@tanstack/react-query";

import { api, MEMBERS_PAGE } from "./client";
import { isApiError } from "./errors";
import type {
  AccountChangeRequest,
  ClientIdRequest,
  ClientSecretRequest,
  IdentityChangeRequest,
  NewConnectionRequest,
  RemoveSecretRequest,
  StatusRequest,
  TestSignInRequest,
  AccountLifecycle,
  AccountLifecycleRequest,
  AdministratorRequest,
  AdminSourcesResponse,
  AskRequest,
  MemberChange,
  MemberChangeRequest,
  MappingPreviewRequest,
  MapRequest,
  NewAccountRequest,
  NewMemberRequest,
  OrganizationChange,
  ReinstateRequest,
  RemapRequest,
  UnmapRequest,
  ChangeRequest,
  CredentialRequest,
  EgressChange,
  EndpointRequest,
  NewProviderRequest,
  RevokeCredentialRequest,
  StepUpRequest,
} from "./types";

export const queryKeys = {
  me: ["me"] as const,
  evidence: (id: string) => ["evidence", id] as const,
  trace: (id: string) => ["trace", id] as const,
  providers: ["admin", "providers"] as const,
  setup: ["admin", "setup"] as const,
  sources: ["sources"] as const,
  adminSources: ["admin", "sources"] as const,
  sourceFailures: (id: string) => ["admin", "sources", id, "failures"] as const,
  activity: ["activity"] as const,
  adminActivity: ["admin", "activity"] as const,
  accounts: ["accounts"] as const,
  accessMembers: (q: string) => ["admin", "access", "members", q] as const,
  accessDirectory: ["admin", "access", "directory"] as const,
  accessMember: (id: string) => ["admin", "access", "member", id] as const,
  identityRequirement: ["admin", "access", "identity-requirement"] as const,
  accessDocuments: (q: string) => ["admin", "access", "documents", q] as const,
  explanation: (member: string, doc: string) => ["admin", "access", "explanation", member, doc] as const,
  audit: ["admin", "audit"] as const,
  accountsList: (q: string, lifecycle: string) => ["admin", "access", "accounts", "list", q, lifecycle] as const,
  account: (id: string) => ["admin", "access", "accounts", "one", id] as const,
  unmappedReferences: ["admin", "access", "account-references", "unmapped"] as const,
  identity: ["admin", "identity"] as const,
  testSignIn: (id: string) => ["admin", "identity", "test", id] as const,
  stepUp: (id: string) => ["admin", "providers", "step-up", id] as const,
  provisioningActivity: ["admin", "audit", "identity-provider"] as const,
};

export const queryClientConfig: QueryClientConfig = {
  defaultOptions: {
    queries: {
      // 4xx answers are decisions, not glitches: never retried. One retry for the rest.
      retry: (count, error) => !(isApiError(error) && error.status >= 400 && error.status < 500) && count < 1,
      refetchOnWindowFocus: false,
      staleTime: 0,
    },
    mutations: { retry: false },
  },
};

export function makeQueryClient(): QueryClient {
  return new QueryClient(queryClientConfig);
}

const REAUTHORIZED = { gcTime: 0, staleTime: 0, refetchOnMount: "always" } as const;

export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: api.me, staleTime: 60_000 });
}

export function useEvidence(id: string | null) {
  return useQuery({
    queryKey: queryKeys.evidence(id ?? ""),
    queryFn: ({ signal }) => api.evidence(id as string, signal),
    enabled: id !== null,
    ...REAUTHORIZED,
  });
}

/**
 * Several evidence items at once (a trace's exhibits). Each is a separate, re-authorized
 * read of GET /v1/evidence/{id}, exactly as opening it would be.
 */
export function useEvidenceList(ids: readonly string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: queryKeys.evidence(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => api.evidence(id, signal),
      ...REAUTHORIZED,
    })),
  });
}

export function useTrace(id: string) {
  return useQuery({
    queryKey: queryKeys.trace(id),
    queryFn: ({ signal }) => api.trace(id, signal),
    ...REAUTHORIZED,
  });
}

export function useProviders(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.providers,
    queryFn: ({ signal }) => api.providers(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** The setup record: derived by the API on every read, never cached across visits. */
export function useSetup(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.setup,
    queryFn: ({ signal }) => api.setup(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** Rename the organization; the setup record and the masthead (/v1/me) are read again. */
export function useRenameOrganization() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: OrganizationChange) => api.renameOrganization(body),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.setup }),
      client.invalidateQueries({ queryKey: queryKeys.me }),
    ]),
  });
}

export function useSources(enabled = true) {
  return useQuery({
    queryKey: queryKeys.sources,
    queryFn: ({ signal }) => api.sources(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** While a requested sync is queued or running, the listing is refreshed every 3 s. */
export function syncInProgress(data: AdminSourcesResponse | undefined): boolean {
  return (data?.sources ?? []).some((s) => s.job?.status === "queued" || s.job?.status === "running");
}

export function useAdminSources(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.adminSources,
    queryFn: ({ signal }) => api.adminSources(signal),
    enabled,
    refetchInterval: (query) => (syncInProgress(query.state.data) ? 3000 : false),
    ...REAUTHORIZED,
  });
}

export function useSourceFailures(id: string | null) {
  return useQuery({
    queryKey: queryKeys.sourceFailures(id ?? ""),
    queryFn: ({ signal }) => api.sourceFailures(id as string, signal),
    enabled: id !== null,
    ...REAUTHORIZED,
  });
}

export function useRequestSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => api.requestSync(sourceId),
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.adminSources }),
  });
}

const PAGE = 25;

/** The viewer's own questions, newest first (Activity, and recent questions in Jump). */
export function useActivity(enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.activity,
    queryFn: ({ pageParam, signal }) => api.activity(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.items.length >= PAGE ? last.items.at(-1)?.trace_id : undefined),
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAdminActivity(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.adminActivity,
    queryFn: ({ pageParam, signal }) => api.adminActivity(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.items.length >= 50 ? last.items.at(-1)?.trace_id : undefined),
    enabled,
    ...REAUTHORIZED,
  });
}

/** The accounts an Ask scope may name (GET /v1/accounts): the picker's only source. */
export function useAccounts() {
  return useQuery({ queryKey: queryKeys.accounts, queryFn: ({ signal }) => api.accounts(signal), ...REAUTHORIZED });
}

/* --- access management (administrators) ---------------------------------------------- */

export function useAccessMembers(q: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.accessMembers(q),
    queryFn: ({ pageParam, signal }) => api.accessMembers(q, pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_after ?? undefined,
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAccessDirectory(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.accessDirectory,
    queryFn: ({ signal }) => api.accessDirectory(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAccessMember(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.accessMember(id),
    queryFn: ({ signal }) => api.accessMember(id, signal),
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAccessDocuments(q: string) {
  return useQuery({
    queryKey: queryKeys.accessDocuments(q),
    queryFn: ({ signal }) => api.accessDocuments(q, signal),
    enabled: q.trim().length >= 2,
    ...REAUTHORIZED,
  });
}

export function useExplanation(memberId: string, documentId: string | null) {
  return useQuery({
    queryKey: queryKeys.explanation(memberId, documentId ?? ""),
    queryFn: ({ signal }) => api.explainDocument(memberId, documentId as string, signal),
    enabled: documentId !== null,
    ...REAUTHORIZED,
  });
}

export function usePreviewMember(id: string) {
  return useMutation({ mutationFn: (change: MemberChange) => api.previewMember(id, change) });
}

/** Every access change refreshes the member, the directory listing and the audit trail. */
function useAccessChanged(id: string) {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.accessMember(id) }),
      client.invalidateQueries({ queryKey: ["admin", "access", "members"] }),
      client.invalidateQueries({ queryKey: ["admin", "access", "explanation", id] }),
      client.invalidateQueries({ queryKey: queryKeys.audit }),
    ]);
}

export function useChangeMember(id: string) {
  const client = useQueryClient();
  const changed = useAccessChanged(id);
  return useMutation({
    mutationFn: (body: MemberChangeRequest) => api.changeMember(id, body),
    onSuccess: (member) => client.setQueryData(queryKeys.accessMember(id), member),
    onSettled: changed,
  });
}

/* --- members and administrators (P12.2) --- */

/** What a member added by hand is bound to: the active connection's identifying claim. */
export function useIdentityRequirement(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.identityRequirement,
    queryFn: ({ signal }) => api.identityRequirement(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAddMember() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: NewMemberRequest) => api.addMember(body),
    onSuccess: (member) => client.setQueryData(queryKeys.accessMember(member.member.id), member),
    onSettled: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["admin", "access", "members"] }),
        client.invalidateQueries({ queryKey: queryKeys.audit }),
        client.invalidateQueries({ queryKey: queryKeys.setup }),
      ]),
  });
}

export function usePreviewAdministrator(id: string) {
  return useMutation({ mutationFn: (administrator: boolean) => api.previewAdministrator(id, administrator) });
}

export function useSetAdministrator(id: string) {
  const client = useQueryClient();
  const changed = useAccessChanged(id);
  return useMutation({
    mutationFn: (body: AdministratorRequest) => api.setAdministrator(id, body),
    onSuccess: (member) => client.setQueryData(queryKeys.accessMember(id), member),
    onSettled: () => Promise.all([changed(), client.invalidateQueries({ queryKey: queryKeys.setup })]),
  });
}

export function usePreviewReinstatement(id: string) {
  return useMutation({ mutationFn: () => api.previewReinstatement(id) });
}

export function useReinstate(id: string) {
  const client = useQueryClient();
  const changed = useAccessChanged(id);
  return useMutation({
    mutationFn: (body: ReinstateRequest) => api.reinstate(id, body),
    onSuccess: (member) => client.setQueryData(queryKeys.accessMember(id), member),
    onSettled: changed,
  });
}

export function useGrant(memberId: string) {
  const changed = useAccessChanged(memberId);
  return useMutation({
    mutationFn: ({ documentId, reason }: { documentId: string; reason: string }) => api.grant(memberId, documentId, reason),
    onSettled: changed,
  });
}

export function useRevokeGrant(memberId: string) {
  const changed = useAccessChanged(memberId);
  return useMutation({
    mutationFn: ({ grantId, reason }: { grantId: string; reason: string }) => api.revokeGrant(grantId, reason),
    onSettled: changed,
  });
}

/* --- accounts and external-reference mappings (P12.3) --- */

export function useAccountsList(q: string, lifecycle: AccountLifecycle | null, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.accountsList(q, lifecycle ?? "all"),
    queryFn: ({ pageParam, signal }) => api.accountsList(q, lifecycle, pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_after ?? undefined,
    enabled,
    ...REAUTHORIZED,
  });
}

export function useAccount(id: string, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.account(id), queryFn: ({ signal }) => api.account(id, signal), enabled, ...REAUTHORIZED });
}

export function useUnmappedReferences(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.unmappedReferences,
    queryFn: ({ signal }) => api.unmappedReferences(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** Accounts and mappings feed the account pages, the member editor's directory, the
 *  setup record and the audit trail: every change refreshes them all. */
function useAccountsChanged() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["admin", "access", "accounts"] }),
      client.invalidateQueries({ queryKey: ["admin", "access", "account-references"] }),
      client.invalidateQueries({ queryKey: queryKeys.accessDirectory }),
      client.invalidateQueries({ queryKey: queryKeys.setup }),
      client.invalidateQueries({ queryKey: queryKeys.audit }),
    ]);
}

export function useCreateAccount() {
  const client = useQueryClient();
  const changed = useAccountsChanged();
  return useMutation({
    mutationFn: (body: NewAccountRequest) => api.createAccount(body),
    onSuccess: (account) => client.setQueryData(queryKeys.account(account.id), account),
    onSettled: changed,
  });
}

export function useUpdateAccount(id: string) {
  const client = useQueryClient();
  const changed = useAccountsChanged();
  return useMutation({
    mutationFn: (body: AccountChangeRequest) => api.updateAccount(id, body),
    onSuccess: (account) => client.setQueryData(queryKeys.account(id), account),
    onSettled: changed,
  });
}

export function useAccountLifecycle(id: string, archive: boolean) {
  const client = useQueryClient();
  const changed = useAccountsChanged();
  return useMutation({
    mutationFn: (body: AccountLifecycleRequest) => (archive ? api.archiveAccount(id, body) : api.reactivateAccount(id, body)),
    onSuccess: (account) => client.setQueryData(queryKeys.account(id), account),
    onSettled: changed,
  });
}

export function usePreviewMapping() {
  return useMutation({ mutationFn: (body: MappingPreviewRequest) => api.previewMapping(body) });
}

export type MappingChange = { operation: "map"; body: MapRequest } | { operation: "remap"; body: RemapRequest } | { operation: "unmap"; body: UnmapRequest };

export function useChangeMapping() {
  const changed = useAccountsChanged();
  return useMutation({
    mutationFn: (change: MappingChange) =>
      change.operation === "map"
        ? api.mapReference(change.body)
        : change.operation === "remap"
          ? api.remapReference(change.body)
          : api.unmapReference(change.body),
    onSettled: changed,
  });
}

export function useAuditEvents(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit,
    queryFn: ({ pageParam, signal }) => api.auditEvents(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
    enabled,
    ...REAUTHORIZED,
  });
}

export { MEMBERS_PAGE };

/* --- identity administration (Phase 12, P12.4) ------------------------------------------- */

/** The organization's identity settings: read again on every visit (never cached). */
export function useIdentity(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.identity,
    queryFn: ({ signal }) => api.identity(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** A test sign-in's result; read until it completes or expires. */
export function useTestSignIn(id: string | null) {
  return useQuery({
    queryKey: queryKeys.testSignIn(id ?? ""),
    queryFn: ({ signal }) => api.testSignIn(id as string, signal),
    enabled: id !== null,
    ...REAUTHORIZED,
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 2_000 : false),
  });
}

export function useProvisioningActivity(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.provisioningActivity,
    queryFn: ({ signal }) => api.provisioningActivity(signal),
    enabled,
    ...REAUTHORIZED,
  });
}

/** Identity changes feed the identity page, the setup record and the audit log. */
function useIdentityChanged() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.identity }),
      client.invalidateQueries({ queryKey: queryKeys.setup }),
      client.invalidateQueries({ queryKey: ["admin", "audit"] }),
    ]);
}

export type ConnectionChange =
  | { kind: "add"; body: NewConnectionRequest }
  | { kind: "client_id"; id: string; body: ClientIdRequest }
  | { kind: "secret"; id: string; body: ClientSecretRequest }
  | { kind: "remove_secret"; id: string; body: RemoveSecretRequest }
  | { kind: "status"; id: string; body: StatusRequest }
  | { kind: "identity"; id: string; body: IdentityChangeRequest };

export function useChangeConnection() {
  const client = useQueryClient();
  const changed = useIdentityChanged();
  return useMutation({
    mutationFn: (change: ConnectionChange) => {
      switch (change.kind) {
        case "add":
          return api.addConnection(change.body);
        case "client_id":
          return api.updateClientId(change.id, change.body);
        case "secret":
          return api.setClientSecret(change.id, change.body);
        case "remove_secret":
          return api.removeClientSecret(change.id, change.body);
        case "status":
          return api.setConnectionStatus(change.id, change.body);
        case "identity":
          return api.changeIdentity(change.id, change.body);
      }
    },
    onSuccess: (settings) => client.setQueryData(queryKeys.identity, settings),
    onSettled: changed,
  });
}

export function usePreviewConnectionStatus() {
  return useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.previewConnectionStatus(id, enabled) });
}

export function usePreviewIdentityChange() {
  return useMutation({
    mutationFn: ({ id, issuer, subject_claim }: { id: string; issuer?: string | null; subject_claim?: string | null }) =>
      api.previewIdentityChange(id, { issuer, subject_claim }),
  });
}

export function useCheckConnection() {
  const changed = useIdentityChanged();
  return useMutation({ mutationFn: (id: string) => api.checkConnection(id), onSettled: changed });
}

export function useStartTestSignIn() {
  return useMutation({ mutationFn: (body: TestSignInRequest) => api.startTestSignIn(body) });
}

export type TokenChange = { kind: "create"; reason: string } | { kind: "rotate"; id: string; reason: string };

/** Create or rotate a SCIM token: the response holds the token once; it is never cached. */
export function useIssueScimToken() {
  const changed = useIdentityChanged();
  return useMutation({
    mutationFn: (change: TokenChange) =>
      change.kind === "create" ? api.createScimToken(change.reason) : api.rotateScimToken(change.id, change.reason),
    onSettled: changed,
    gcTime: 0,
  });
}

export function usePreviewRevokeScimToken() {
  return useMutation({ mutationFn: (id: string) => api.previewRevokeScimToken(id) });
}

export function useRevokeScimToken() {
  const changed = useIdentityChanged();
  return useMutation({ mutationFn: ({ id, reason }: { id: string; reason: string }) => api.revokeScimToken(id, reason), onSettled: changed });
}

/* --- AI providers, BYOK and egress (Phase 12, P12.5) --------------------------------------- */

/** A step-up's result: read until the sign-in at the identity provider completes. */
export function useStepUp(id: string | null) {
  return useQuery({
    queryKey: queryKeys.stepUp(id ?? ""),
    queryFn: ({ signal }) => api.stepUp(id as string, signal),
    enabled: id !== null,
    ...REAUTHORIZED,
    refetchInterval: (query) => (query.state.data?.outcome === "pending" ? 2_000 : false),
  });
}

/** Provider and policy changes feed the providers page, the setup record and the audit log. */
function useProvidersChanged() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: queryKeys.providers }),
      client.invalidateQueries({ queryKey: queryKeys.setup }),
      client.invalidateQueries({ queryKey: ["admin", "audit"] }),
    ]);
}

/** Now -> After, computed by the API on the current policy. Changes nothing. */
export function usePreviewEgressChange() {
  return useMutation({ mutationFn: (change: EgressChange) => api.previewEgressChange(change) });
}

export type ProviderMutation =
  | { kind: "create"; body: NewProviderRequest }
  | { kind: "change"; body: ChangeRequest }
  | { kind: "apply_step_up"; id: string }
  | { kind: "endpoint"; id: string; body: EndpointRequest }
  | { kind: "credential"; id: string; body: CredentialRequest }
  | { kind: "revoke_credential"; id: string; body: RevokeCredentialRequest };

/** Every change that answers with the new settings. A key is in the request only; the
 * mutation keeps no variables after it settles (gcTime 0). */
export function useChangeProviders() {
  const client = useQueryClient();
  const changed = useProvidersChanged();
  return useMutation({
    mutationFn: (m: ProviderMutation) => {
      switch (m.kind) {
        case "create":
          return api.createProvider(m.body);
        case "change":
          return api.applyEgressChange(m.body);
        case "apply_step_up":
          return api.applyStepUp(m.id);
        case "endpoint":
          return api.setProviderEndpoint(m.id, m.body);
        case "credential":
          return api.setProviderCredential(m.id, m.body);
        case "revoke_credential":
          return api.revokeProviderCredential(m.id, m.body);
      }
    },
    onSuccess: (settings) => client.setQueryData(queryKeys.providers, settings),
    onSettled: changed,
    gcTime: 0,
  });
}

export function useStartStepUp() {
  return useMutation({ mutationFn: (body: StepUpRequest) => api.startStepUp(body) });
}

export function useTestProvider() {
  const changed = useProvidersChanged();
  return useMutation({ mutationFn: (id: string) => api.testProvider(id), onSettled: changed });
}

export function useAsk() {
  return useMutation({ mutationFn: (body: AskRequest) => api.ask(body) });
}

export function useSignIn() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ subject, organization }: { subject: string; organization?: string }) =>
      api.devLogin(subject, organization),
    onSuccess: () => client.clear(),
  });
}

export function useSignOut() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    // Whatever happens, nothing of the previous session stays in memory.
    onSettled: () => client.clear(),
  });
}
