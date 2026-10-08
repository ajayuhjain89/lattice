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
import type { AdminSourcesResponse, AskRequest, MemberChange, MemberChangeRequest, OrganizationChange } from "./types";

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
  accessDocuments: (q: string) => ["admin", "access", "documents", q] as const,
  explanation: (member: string, doc: string) => ["admin", "access", "explanation", member, doc] as const,
  audit: ["admin", "audit"] as const,
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
