/**
 * The one HTTP client. Same-origin only (`/api` is proxied to FastAPI), the session
 * cookie travels automatically, and every state-changing request carries the CSRF proof
 * header the API requires (authorization-model §2). Callers get typed data or an
 * ApiError; nobody else parses responses.
 */
import createClient, { type Middleware } from "openapi-fetch";

import { fromResponse, networkError } from "./errors";
import type { paths } from "./schema.gen";
import type {
  AccessPreview,
  AdministratorPreview,
  AdministratorRequest,
  AdminSourcesResponse,
  Answer,
  AskRequest,
  AuditPage,
  Directory,
  DocumentExplanation,
  DocumentsPage,
  EvidenceDetail,
  FailuresResponse,
  GrantOut,
  IdentityRequirement,
  Me,
  MemberAccess,
  MemberAccounts,
  MemberChange,
  MemberChangeRequest,
  MembersPage,
  MemberSourcesResponse,
  NewMemberRequest,
  OperationalActivityResponse,
  OrganizationChange,
  SetupOrganization,
  OwnActivityResponse,
  ProvidersResponse,
  ReinstateRequest,
  SetupStatus,
  SyncJob,
  Trace,
} from "./types";

const csrf: Middleware = {
  onRequest({ request }) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      request.headers.set("X-Lattice-Request", "1");
    }
    return request;
  },
};

function baseUrl(): string {
  // In the browser the path is enough; under test (jsdom/node) an origin is required.
  return typeof window === "undefined" ? "http://localhost/api" : `${window.location.origin}/api`;
}

export const http = createClient<paths>({
  baseUrl: baseUrl(),
  credentials: "same-origin",
  // Resolved per call, so tests and the browser's own fetch are always the one used.
  fetch: (request) => globalThis.fetch(request),
});
http.use(csrf);

type Result<T> = { data?: T; error?: unknown; response: Response };

async function call<T>(run: () => Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await run();
  } catch (cause) {
    // Cancellation (a superseded query) is not a failure the person should see.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    // fetch rejects with a TypeError only when the server could not be reached.
    if (cause instanceof TypeError) throw networkError();
    throw cause;
  }
  const { data, error, response } = result;
  if (!response.ok || error !== undefined) throw fromResponse(response, error);
  return data as T;
}

export const api = {
  me: () => call<Me>(() => http.GET("/v1/me")),
  devLogin: (subject: string, organization?: string) =>
    call<Me>(() =>
      http.POST("/v1/auth/dev-login", {
        body: organization ? { subject, organization } : { subject },
      }),
    ),
  logout: async () => {
    await call<unknown>(() => http.POST("/v1/auth/logout"));
  },
  ask: (body: AskRequest, signal?: AbortSignal) =>
    call<Answer>(() => http.POST("/v1/ask", { body, signal })),
  evidence: (id: string, signal?: AbortSignal) =>
    call<EvidenceDetail>(() =>
      http.GET("/v1/evidence/{evidence_id}", { params: { path: { evidence_id: id } }, signal }),
    ),
  trace: (id: string, signal?: AbortSignal) =>
    call<Trace>(() =>
      http.GET("/v1/traces/{trace_id}", { params: { path: { trace_id: id } }, signal }),
    ),
  providers: (signal?: AbortSignal) =>
    call<ProvidersResponse>(() => http.GET("/v1/admin/providers", { signal })),
  setup: (signal?: AbortSignal) => call<SetupStatus>(() => http.GET("/v1/admin/setup", { signal })),
  renameOrganization: (body: OrganizationChange) =>
    call<SetupOrganization>(() => http.PATCH("/v1/admin/organization", { body })),
  sources: (signal?: AbortSignal) =>
    call<MemberSourcesResponse>(() => http.GET("/v1/sources", { signal })),
  adminSources: (signal?: AbortSignal) =>
    call<AdminSourcesResponse>(() => http.GET("/v1/admin/sources", { signal })),
  sourceFailures: (id: string, signal?: AbortSignal) =>
    call<FailuresResponse>(() =>
      http.GET("/v1/admin/sources/{source_id}/failures", { params: { path: { source_id: id } }, signal }),
    ),
  requestSync: (id: string) =>
    call<SyncJob>(() =>
      http.POST("/v1/admin/sources/{source_id}/sync", {
        params: { path: { source_id: id } },
        body: { mode: "full" },
      }),
    ),
  activity: (before?: string, signal?: AbortSignal) =>
    call<OwnActivityResponse>(() =>
      http.GET("/v1/activity", { params: { query: before ? { before } : {} }, signal }),
    ),
  adminActivity: (before?: string, signal?: AbortSignal) =>
    call<OperationalActivityResponse>(() =>
      http.GET("/v1/admin/activity", { params: { query: before ? { before } : {} }, signal }),
    ),
  accounts: (signal?: AbortSignal) => call<MemberAccounts>(() => http.GET("/v1/accounts", { signal })),
  accessMembers: (q: string, after?: string, signal?: AbortSignal) =>
    call<MembersPage>(() =>
      http.GET("/v1/admin/access/members", {
        params: { query: { ...(q ? { q } : {}), ...(after ? { after } : {}), limit: MEMBERS_PAGE } },
        signal,
      }),
    ),
  accessDirectory: (signal?: AbortSignal) => call<Directory>(() => http.GET("/v1/admin/access/directory", { signal })),
  accessMember: (id: string, signal?: AbortSignal) =>
    call<MemberAccess>(() =>
      http.GET("/v1/admin/access/members/{membership_id}", { params: { path: { membership_id: id } }, signal }),
    ),
  previewMember: (id: string, body: MemberChange) =>
    call<AccessPreview>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/preview", { params: { path: { membership_id: id } }, body }),
    ),
  changeMember: (id: string, body: MemberChangeRequest) =>
    call<MemberAccess>(() =>
      http.PATCH("/v1/admin/access/members/{membership_id}", { params: { path: { membership_id: id } }, body }),
    ),
  identityRequirement: (signal?: AbortSignal) =>
    call<IdentityRequirement>(() => http.GET("/v1/admin/access/identity-requirement", { signal })),
  addMember: (body: NewMemberRequest) => call<MemberAccess>(() => http.POST("/v1/admin/access/members", { body })),
  previewAdministrator: (id: string, administrator: boolean) =>
    call<AdministratorPreview>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/administrator/preview", {
        params: { path: { membership_id: id } },
        body: { administrator },
      }),
    ),
  setAdministrator: (id: string, body: AdministratorRequest) =>
    call<MemberAccess>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/administrator", { params: { path: { membership_id: id } }, body }),
    ),
  previewReinstatement: (id: string) =>
    call<AccessPreview>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/reinstate/preview", { params: { path: { membership_id: id } } }),
    ),
  reinstate: (id: string, body: ReinstateRequest) =>
    call<MemberAccess>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/reinstate", { params: { path: { membership_id: id } }, body }),
    ),
  accessDocuments: (q: string, signal?: AbortSignal) =>
    call<DocumentsPage>(() => http.GET("/v1/admin/access/documents", { params: { query: { q } }, signal })),
  explainDocument: (memberId: string, documentId: string, signal?: AbortSignal) =>
    call<DocumentExplanation>(() =>
      http.GET("/v1/admin/access/members/{membership_id}/documents/{document_id}", {
        params: { path: { membership_id: memberId, document_id: documentId } },
        signal,
      }),
    ),
  grant: (memberId: string, documentId: string, reason: string) =>
    call<GrantOut>(() =>
      http.POST("/v1/admin/access/members/{membership_id}/grants", {
        params: { path: { membership_id: memberId } },
        body: { document_id: documentId, reason },
      }),
    ),
  revokeGrant: async (grantId: string, reason: string) => {
    await call<unknown>(() =>
      http.POST("/v1/admin/access/grants/{grant_id}/revoke", { params: { path: { grant_id: grantId } }, body: { reason } }),
    );
  },
  auditEvents: (before?: string, signal?: AbortSignal) =>
    call<AuditPage>(() =>
      http.GET("/v1/admin/audit-events", { params: { query: before ? { before } : {} }, signal }),
    ),
};

export const MEMBERS_PAGE = 50;
