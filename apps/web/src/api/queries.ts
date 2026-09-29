/**
 * Centralized server state. Every read goes through these hooks; every key lives here.
 *
 * Evidence and traces are re-authorized by the API on every read, so the UI never keeps
 * them once no component shows them (gcTime 0) and always asks again when one mounts:
 * nothing the API stops returning can be resurrected from a client cache.
 */
import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClientConfig,
} from "@tanstack/react-query";

import { api } from "./client";
import { isApiError } from "./errors";
import type { AskRequest } from "./types";

export const queryKeys = {
  me: ["me"] as const,
  evidence: (id: string) => ["evidence", id] as const,
  trace: (id: string) => ["trace", id] as const,
  providers: ["admin", "providers"] as const,
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
