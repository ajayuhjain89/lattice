import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/schema.gen";
import {
  ACCOUNT_ACME,
  ACCOUNT_ORBITAL_ARCHIVED,
  ACCOUNTS_MAYA,
  ACTIVITY_ADMIN,
  ACTIVITY_OWN,
  AUDIT,
  DETAILS,
  DIRECTORY,
  DOCUMENTS,
  EXPLANATION_DENIED,
  FAILURES,
  IDENTITY_REQUIREMENT,
  IDENTITY_VERIFIED,
  PROVISIONING_ACTIVITY,
  REVEALED_TOKEN,
  ME_MAYA,
  MEMBER_MAYA,
  MEMBER_NEW,
  MEMBER_RAE_REINSTATED,
  MEMBERS_PAGE,
  PREVIEW_ADD_KESTREL,
  PREVIEW_REINSTATE,
  NOT_FOUND,
  PROVIDERS,
  SETUP_FRESH,
  SOURCES_ADMIN,
  SOURCES_MEMBER,
  TRACE_ADMIN,
  TRACE_ASKER,
  TRACE_HIDDEN,
  UNMAPPED,
} from "../src/test/fixtures";

type S = components["schemas"];

export interface MockOptions {
  me?: S["MeResponse"] | "unauthenticated";
  /** Answer for each POST /v1/ask, chosen from the request body. */
  ask?: (
    body: S["AskRequest"],
  ) => S["Answer"] | { status: number; body: unknown; headers?: Record<string, string> } | "network";
  evidence?: Record<string, S["EvidenceDetail"]>;
  traces?: Record<string, S["AskerTrace"] | S["AdminTrace"]>;
  providers?: S["ProvidersResponse"] | "forbidden";
  /** Milliseconds before the Ask response (to observe the pending state). */
  askDelay?: number;
  sources?: S["MemberSourcesResponse"];
  /** The administrator listing, or a function of how many times it has been read (a sync progressing). */
  adminSources?: S["AdminSourcesResponse"] | "forbidden" | ((reads: number) => S["AdminSourcesResponse"]);
  /** Answer for POST /v1/admin/sources/{id}/sync (default: 202 with a queued job). */
  sync?: (sourceId: string) => { status: number; body: unknown };
  failures?: S["FailuresResponse"];
  activity?: S["OwnActivityResponse"];
  adminActivity?: S["OperationalActivityResponse"] | "forbidden";
  accounts?: S["MemberAccounts"];
  /** Access management (administrators); "forbidden" answers every /v1/admin/access call 403. */
  access?: "forbidden";
  members?: S["MembersPage"];
  member?: Record<string, S["MemberAccess"]>;
  preview?: S["AccessPreview"];
  /** Answer for PATCH /v1/admin/access/members/{id}. */
  change?: (body: S["MemberChangeRequest"]) => S["MemberAccess"] | { status: number; body: unknown };
  /** P12.2: the identity requirement (GET /v1/admin/access/identity-requirement), and a delay before it. */
  identity?: S["IdentityRequirement"] | "fault";
  identityDelay?: number;
  /** Answer for POST /v1/admin/access/members (default: 201 with MEMBER_NEW). */
  addMember?: (body: S["NewMemberRequest"]) => S["MemberAccess"] | { status: number; body: unknown } | "network";
  /** Answer for POST .../administrator (default: the member with the capability flipped). */
  administrator?: (id: string, body: S["AdministratorRequest"]) => S["MemberAccess"] | { status: number; body: unknown };
  /** Answer for POST .../reinstate (default: 200 with MEMBER_RAE_REINSTATED). */
  reinstate?: (id: string, body: S["ReinstateRequest"]) => S["MemberAccess"] | { status: number; body: unknown };
  /** Answer for POST /v1/auth/dev-login (default: 200 with `me`). */
  devLogin?: { status: number; body: unknown; headers?: Record<string, string> };
  /** The audit log, or a function of the `before` cursor (keyset paging). */
  audit?: S["AuditPage"] | ((before: string | null) => S["AuditPage"]);
  /** The setup record (GET /v1/admin/setup). */
  setup?: S["SetupStatus"] | "forbidden" | "fault";
  /** Answer for PATCH /v1/admin/organization (default: 200 with the new name). */
  rename?: (body: S["OrganizationChange"]) => { status: number; body: unknown };
  /** P12.3: accounts by ID (default: Acme and archived Orbital), and the unmapped references. */
  accountRecords?: Record<string, S["AccountDetail"]>;
  unmapped?: S["UnmappedReferences"];
  /** Answer for every account or mapping write instead of applying it (e.g. a 409). */
  accountWrite?: (path: string, body: unknown) => { status: number; body: unknown } | undefined;
  /** P12.4: the identity settings (GET /v1/admin/identity), changed by the writes below. */
  identitySettings?: S["IdentitySettings"] | "forbidden";
  /** The result of every test sign-in (default: matched, the connection as configured). */
  testSignIn?: S["TestSignInOut"];
  /** Answer for POST .../identity/preview (default: the values, no proof yet). */
  identityPreview?: S["IdentityChangePreview"];
  /** Answer for POST .../check (default: valid). */
  check?: S["CheckOut"];
}

export interface MockLog {
  changes: S["MemberChangeRequest"][];
  grants: unknown[];
  revokes: string[];
  syncs: string[];
  asks: S["AskRequest"][];
  devLogins: unknown[];
  evidence: string[];
  traces: string[];
  headers: Record<string, string>[];
  renames: S["OrganizationChange"][];
  added: S["NewMemberRequest"][];
  administrators: { id: string; body: S["AdministratorRequest"] }[];
  reinstates: { id: string; body: S["ReinstateRequest"] }[];
  accountWrites: { path: string; body: unknown }[];
  identityWrites: { method: string; path: string; body: unknown }[];
}

const json = (route: Route, status: number, body: unknown, headers: Record<string, string> = {}) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
    headers: { "x-request-id": "req-fixture", ...headers },
  });

/** Fulfil every /api/* request from typed fixtures; anything unknown is a 404 envelope. */
export async function mockApi(page: Page, options: MockOptions = {}): Promise<MockLog> {
  const log: MockLog = {
    changes: [],
    grants: [],
    revokes: [],
    syncs: [],
    asks: [],
    devLogins: [],
    evidence: [],
    traces: [],
    headers: [],
    renames: [],
    added: [],
    administrators: [],
    reinstates: [],
    accountWrites: [],
    identityWrites: [],
  };
  const answer = (route: Route, ok: number, result: unknown) =>
    typeof result === "object" && result !== null && "status" in result && "body" in result && typeof result.status === "number"
      ? json(route, result.status, result.body)
      : json(route, ok, result);
  let adminSourceReads = 0;
  // Members by ID; P12.2 writes (add, administrator, reinstate) update it, so the page's
  // reload after a write reads what the write returned.
  const members: Record<string, S["MemberAccess"]> = { ...(options.member ?? { [MEMBER_MAYA.member.id]: MEMBER_MAYA }) };
  let identity: S["IdentitySettings"] | "forbidden" = options.identitySettings ?? IDENTITY_VERIFIED;
  const forbidden = (route: Route) =>
    json(route, 403, { error: { code: "permission_denied", message: "You don't have permission to do this.", request_id: "req-403" } });
  const evidence = options.evidence ?? DETAILS;
  const traces = options.traces ?? {
    [TRACE_ASKER.trace_id]: TRACE_ASKER,
    [TRACE_HIDDEN.trace_id]: TRACE_HIDDEN,
    [TRACE_ADMIN.trace_id]: TRACE_ADMIN,
  };

  // Accounts (P12.3), applied in memory so the page's reload reads what a write returned.
  const accounts: Record<string, S["AccountDetail"]> = {
    ...(options.accountRecords ?? { [ACCOUNT_ACME.id]: ACCOUNT_ACME, [ACCOUNT_ORBITAL_ARCHIVED.id]: ACCOUNT_ORBITAL_ARCHIVED }),
  };
  let unmapped: S["UnmappedReferences"] = options.unmapped ?? UNMAPPED;
  const ref = (a: S["AccountDetail"]): S["AccountRef"] => ({ id: a.id, key: a.key, name: a.name, lifecycle: a.lifecycle });
  const summary = ({ version: _v, created_at: _c, mappings: _m, ...rest }: S["AccountDetail"]): S["AccountSummary"] => rest;
  const sources: Record<string, S["SourceRef"]> = Object.fromEntries(
    SOURCES_ADMIN.sources.map((x) => [x.id, { id: x.id, name: x.name, connector_type: x.connector_type }]),
  );
  const owner = (sourceId: string, kind: string, value: string) =>
    Object.values(accounts).find((a) => a.mappings.some((m) => m.source.id === sourceId && m.ref_kind === kind && m.external_ref === value));
  const touched = (a: S["AccountDetail"]): S["AccountDetail"] => ({ ...a, version: `2026-10-12T10:${String(log.accountWrites.length).padStart(2, "0")}:00.000000Z` });
  const accountsRoute = (route: Route, path: string, method: string, url: URL, body: Record<string, unknown> | null) => {
    if (method !== "GET") {
      log.accountWrites.push({ path, body });
      const answerWith = options.accountWrite?.(path, body);
      if (answerWith) return json(route, answerWith.status, answerWith.body);
    }
    if (path === "/v1/admin/access/accounts" && method === "GET") {
      const q = url.searchParams.get("q")?.toLowerCase();
      const lifecycle = url.searchParams.get("lifecycle");
      const items = Object.values(accounts)
        .filter((a) => !q || `${a.name} ${a.key} ${a.aliases.join(" ")}`.toLowerCase().includes(q))
        .filter((a) => !lifecycle || a.lifecycle === lifecycle)
        .sort((a, b) => a.name.localeCompare(b.name))
        .map(summary);
      return json(route, 200, { items, next_after: null });
    }
    if (path === "/v1/admin/access/accounts" && method === "POST") {
      const id = `ac000000-0000-4000-8000-0000000000${String(10 + log.accountWrites.length).padStart(2, "0")}`;
      const b = body as S["NewAccountRequest"];
      accounts[id] = {
        id,
        key: b.key,
        name: b.name,
        aliases: b.aliases ?? [],
        status: b.status ?? "active",
        lifecycle: "active",
        archived_at: null,
        references: 0,
        assigned_members: 0,
        version: "2026-10-12T10:00:00.000000Z",
        created_at: "2026-10-12T10:00:00Z",
        mappings: [],
      };
      return json(route, 201, accounts[id]);
    }
    if (path === "/v1/admin/access/account-references/unmapped" && method === "GET") return json(route, 200, unmapped);
    if (path === "/v1/admin/access/account-references/preview" && method === "POST") {
      const b = body as S["MappingPreviewRequest"];
      const source = sources[b.source_id]!;
      const now = owner(b.source_id, b.ref_kind, b.external_ref);
      const after = b.account_id ? accounts[b.account_id] : undefined;
      const scoped = source.connector_type === "ticketing";
      const operation = !now ? "map" : after ? "remap" : "unmap";
      // As the API: only a never-mapped channel of a source that is not account-scoped is
      // indexed without an account; an unmap always quarantines (never widens).
      const waiting = unmapped.items.find((r) => r.external_ref === b.external_ref && r.source.id === b.source_id)?.records_waiting ?? 0;
      return json(route, 200, {
        operation,
        source,
        ref_kind: b.ref_kind,
        external_ref: b.external_ref,
        account_scoped: scoped,
        now: now ? ref(now) : null,
        after: after ? ref(after) : null,
        effect: operation === "map" ? (scoped ? "indexed" : "scoped") : operation === "remap" ? "moved" : "quarantined",
        records_waiting: waiting,
        takes_effect: "next_sync",
      } satisfies S["MappingPreview"]);
    }
    const mapping = /^\/v1\/admin\/access\/account-references(\/remap|\/unmap)?$/.exec(path);
    if (mapping && method === "POST") {
      const b = body as S["RemapRequest"] & S["UnmapRequest"];
      const source = sources[b.source_id]!;
      const now = owner(b.source_id, b.ref_kind, b.external_ref);
      if (now) {
        accounts[now.id] = touched({ ...now, mappings: now.mappings.filter((m) => !(m.source.id === b.source_id && m.external_ref === b.external_ref)) });
        accounts[now.id]!.references = accounts[now.id]!.mappings.length;
      }
      const target = mapping[1] === "/unmap" ? undefined : accounts[b.account_id];
      if (target) {
        const m: S["AccountMapping"] = { source, ref_kind: b.ref_kind, external_ref: b.external_ref, created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z" };
        accounts[target.id] = touched({ ...target, mappings: [...target.mappings, m], references: target.mappings.length + 1 });
      }
      unmapped = {
        items: unmapped.items.map((r) =>
          r.source.id === b.source_id && r.external_ref === b.external_ref
            ? { ...r, state: target ? "mapped_pending_sync" : "unmapped", account: target ? ref(accounts[target.id]!) : null }
            : r,
        ),
      };
      return json(route, mapping[1] ? 200 : 201, {
        operation: mapping[1] === "/remap" ? "remap" : mapping[1] === "/unmap" ? "unmap" : "map",
        source,
        ref_kind: b.ref_kind,
        external_ref: b.external_ref,
        account: target ? ref(accounts[target.id]!) : null,
        takes_effect: "next_sync",
      } satisfies S["MappingResult"]);
    }
    const one = /^\/v1\/admin\/access\/accounts\/([^/]+)(\/archive|\/reactivate)?$/.exec(path);
    const found = one ? accounts[one[1]!] : undefined;
    if (!one || !found) return json(route, 404, NOT_FOUND);
    if (method === "GET" && !one[2]) return json(route, 200, found);
    if (method === "PATCH") {
      const b = body as S["AccountChangeRequest"];
      accounts[found.id] = touched({ ...found, ...(b.name ? { name: b.name } : {}), ...(b.aliases ? { aliases: b.aliases } : {}), ...(b.status ? { status: b.status } : {}) });
      return json(route, 200, accounts[found.id]);
    }
    if (method === "POST" && one[2]) {
      const archive = one[2] === "/archive";
      accounts[found.id] = touched({ ...found, lifecycle: archive ? "archived" : "active", archived_at: archive ? "2026-10-12T10:00:00Z" : null });
      return json(route, 200, accounts[found.id]);
    }
    return json(route, 404, NOT_FOUND);
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    log.headers.push(request.headers());

    if (path === "/v1/me" && method === "GET") {
      const me = options.me ?? ME_MAYA;
      return me === "unauthenticated"
        ? json(route, 401, { error: { code: "authentication_required", message: "Sign in to continue.", request_id: "req-401" } })
        : json(route, 200, me);
    }
    if (path === "/v1/auth/dev-login" && method === "POST") {
      log.devLogins.push(request.postDataJSON());
      if (options.devLogin) return json(route, options.devLogin.status, options.devLogin.body, options.devLogin.headers);
      return json(route, 200, options.me === "unauthenticated" ? ME_MAYA : (options.me ?? ME_MAYA));
    }
    if (path === "/v1/auth/logout" && method === "POST") return route.fulfill({ status: 204 });

    if (path === "/v1/ask" && method === "POST") {
      const body = request.postDataJSON() as S["AskRequest"];
      log.asks.push(body);
      if (options.askDelay) await new Promise((r) => setTimeout(r, options.askDelay));
      const result = options.ask?.(body);
      if (!result) return json(route, 500, { error: { code: "internal_error", message: "No fixture.", request_id: "req-500" } });
      if (result === "network") return route.abort("connectionrefused");
      if ("status" in result && "body" in result && typeof result.status === "number") {
        return json(route, result.status, result.body, "headers" in result ? result.headers : {});
      }
      return json(route, 200, result);
    }
    const ev = /^\/v1\/evidence\/([^/]+)$/.exec(path);
    if (ev && method === "GET") {
      log.evidence.push(ev[1]!);
      const found = evidence[ev[1]!];
      return found ? json(route, 200, found) : json(route, 404, NOT_FOUND);
    }
    const tr = /^\/v1\/traces\/([^/]+)$/.exec(path);
    if (tr && method === "GET") {
      log.traces.push(tr[1]!);
      const found = traces[tr[1]!];
      return found ? json(route, 200, found) : json(route, 404, NOT_FOUND);
    }
    if (path === "/v1/admin/providers" && method === "GET") {
      const p = options.providers ?? PROVIDERS;
      return p === "forbidden"
        ? json(route, 403, { error: { code: "permission_denied", message: "You don't have permission to do this.", request_id: "req-403" } })
        : json(route, 200, p);
    }
    if (path === "/v1/admin/setup" && method === "GET") {
      const setup = options.setup ?? SETUP_FRESH;
      if (setup === "forbidden") return forbidden(route);
      if (setup === "fault") return json(route, 500, { error: { code: "internal_error", message: "Something went wrong.", request_id: "req-500" } });
      return json(route, 200, setup);
    }
    if (path === "/v1/admin/organization" && method === "PATCH") {
      const body = request.postDataJSON() as S["OrganizationChange"];
      log.renames.push(body);
      const answer = options.rename?.(body);
      if (answer) return json(route, answer.status, answer.body);
      const current = options.setup && typeof options.setup === "object" ? options.setup : SETUP_FRESH;
      return json(route, 200, { ...current.organization, name: body.name, version: "2026-10-08T09:05:00.000000Z" });
    }
    if (path === "/v1/sources" && method === "GET") return json(route, 200, options.sources ?? SOURCES_MEMBER);
    if (path === "/v1/admin/sources" && method === "GET") {
      const a = options.adminSources ?? SOURCES_ADMIN;
      adminSourceReads += 1;
      if (a === "forbidden") return forbidden(route);
      return json(route, 200, typeof a === "function" ? a(adminSourceReads) : a);
    }
    const failures = /^\/v1\/admin\/sources\/([^/]+)\/failures$/.exec(path);
    if (failures && method === "GET") return json(route, 200, options.failures ?? FAILURES);
    const sync = /^\/v1\/admin\/sources\/([^/]+)\/sync$/.exec(path);
    if (sync && method === "POST") {
      log.syncs.push(sync[1]!);
      const answer = options.sync?.(sync[1]!);
      if (answer) return json(route, answer.status, answer.body);
      return json(route, 202, {
        id: "7f2a1b0c-9999-4d3e-9f4a-1b2c3d4e5f99",
        source_id: sync[1]!,
        mode: "full",
        status: "queued",
        attempts: 0,
        error_code: null,
        created_at: "2026-09-29T09:10:00Z",
        started_at: null,
        finished_at: null,
        sync_run_id: null,
      } satisfies S["JobOut"]);
    }
    if (path === "/v1/activity" && method === "GET") return json(route, 200, options.activity ?? ACTIVITY_OWN);
    if (path === "/v1/admin/activity" && method === "GET") {
      const a = options.adminActivity ?? ACTIVITY_ADMIN;
      return a === "forbidden" ? forbidden(route) : json(route, 200, a);
    }
    if (path === "/v1/accounts" && method === "GET") return json(route, 200, options.accounts ?? ACCOUNTS_MAYA);
    if (path.startsWith("/v1/admin/access") || path === "/v1/admin/audit-events") {
      if (options.access === "forbidden") return forbidden(route);
      if (path === "/v1/admin/access/members" && method === "GET") {
        // The server's search: names and e-mail addresses containing the query.
        const page = options.members ?? MEMBERS_PAGE;
        const q = url.searchParams.get("q")?.toLowerCase();
        return json(route, 200, q ? { ...page, items: page.items.filter((m) => `${m.name} ${m.email}`.toLowerCase().includes(q)) } : page);
      }
      if (path === "/v1/admin/access/identity-requirement" && method === "GET") {
        if (options.identityDelay) await new Promise((done) => setTimeout(done, options.identityDelay));
        if (options.identity === "fault") return json(route, 500, { error: { code: "internal_error", message: "Something went wrong.", request_id: "r" } });
        return json(route, 200, options.identity ?? IDENTITY_REQUIREMENT);
      }
      if (path === "/v1/admin/access/members" && method === "POST") {
        const body = request.postDataJSON() as S["NewMemberRequest"];
        log.added.push(body);
        const result = options.addMember?.(body) ?? MEMBER_NEW;
        if (result === "network") return route.abort("connectionrefused");
        if ("member" in result) members[result.member.id] = result;
        return answer(route, 201, result);
      }
      if (path === "/v1/admin/access/directory" && method === "GET") return json(route, 200, DIRECTORY);
      if (path === "/v1/admin/access/documents" && method === "GET") return json(route, 200, DOCUMENTS);
      if (path === "/v1/admin/audit-events" && method === "GET") {
        // The provisioning activity (P12.4): the identity provider's events only.
        if (url.searchParams.get("actor") === "identity_provider") return json(route, 200, PROVISIONING_ACTIVITY);
        const a = options.audit ?? AUDIT;
        return json(route, 200, typeof a === "function" ? a(url.searchParams.get("before")) : a);
      }
      if (path.startsWith("/v1/admin/access/account")) return accountsRoute(route, path, method, url, request.postDataJSON?.() ?? null);
      const explain = /^\/v1\/admin\/access\/members\/([^/]+)\/documents\/([^/]+)$/.exec(path);
      if (explain && method === "GET") return json(route, 200, EXPLANATION_DENIED);
      const adminPreview = /^\/v1\/admin\/access\/members\/([^/]+)\/administrator\/preview$/.exec(path);
      if (adminPreview && method === "POST") {
        const body = request.postDataJSON() as S["AdministratorChange"];
        const found = members[adminPreview[1]!];
        return found ? json(route, 200, { now: found.member.is_admin, after: body.administrator, content_access_changes: false }) : json(route, 404, NOT_FOUND);
      }
      const administrator = /^\/v1\/admin\/access\/members\/([^/]+)\/administrator$/.exec(path);
      if (administrator && method === "POST") {
        const id = administrator[1]!;
        const body = request.postDataJSON() as S["AdministratorRequest"];
        log.administrators.push({ id, body });
        const found = members[id];
        if (options.administrator) return answer(route, 200, options.administrator(id, body));
        if (!found) return json(route, 404, NOT_FOUND);
        const open = { available: true, blocked_by: null };
        members[id] = {
          ...found,
          member: { ...found.member, is_admin: body.administrator },
          lifecycle: { ...found.lifecycle, grant_administrator: body.administrator ? null : open, revoke_administrator: body.administrator ? open : null },
        };
        return json(route, 200, members[id]);
      }
      const reinstatePreview = /^\/v1\/admin\/access\/members\/([^/]+)\/reinstate\/preview$/.exec(path);
      if (reinstatePreview && method === "POST") return json(route, 200, PREVIEW_REINSTATE);
      const reinstate = /^\/v1\/admin\/access\/members\/([^/]+)\/reinstate$/.exec(path);
      if (reinstate && method === "POST") {
        const id = reinstate[1]!;
        const body = request.postDataJSON() as S["ReinstateRequest"];
        log.reinstates.push({ id, body });
        const result = options.reinstate?.(id, body) ?? MEMBER_RAE_REINSTATED;
        if ("member" in result) members[id] = result;
        return answer(route, 200, result);
      }
      const preview = /^\/v1\/admin\/access\/members\/([^/]+)\/preview$/.exec(path);
      if (preview && method === "POST") return json(route, 200, options.preview ?? PREVIEW_ADD_KESTREL);
      const grants = /^\/v1\/admin\/access\/members\/([^/]+)\/grants$/.exec(path);
      if (grants && method === "POST") {
        log.grants.push(request.postDataJSON());
        return json(route, 201, members[grants[1]!]?.grants[0] ?? NOT_FOUND);
      }
      const revoke = /^\/v1\/admin\/access\/grants\/([^/]+)\/revoke$/.exec(path);
      if (revoke && method === "POST") {
        log.revokes.push(revoke[1]!);
        return route.fulfill({ status: 204 });
      }
      const member = /^\/v1\/admin\/access\/members\/([^/]+)$/.exec(path);
      if (member && method === "GET") {
        const found = members[member[1]!];
        return found ? json(route, 200, found) : json(route, 404, NOT_FOUND);
      }
      if (member && method === "PATCH") {
        const body = request.postDataJSON() as S["MemberChangeRequest"];
        log.changes.push(body);
        const result = options.change?.(body) ?? members[member[1]!];
        if (!result) return json(route, 404, NOT_FOUND);
        if ("status" in result && "body" in result && typeof result.status === "number") return json(route, result.status, result.body);
        return json(route, 200, result);
      }
    }
    if (path.startsWith("/v1/admin/identity")) return identityRoute(route, path, method, request.postDataJSON?.() ?? null);
    return json(route, 404, NOT_FOUND);
  });
  return log;

  /** Identity administration (P12.4), as the API answers it, on a stateful copy. */
  function identityRoute(route: Route, path: string, method: string, body: Record<string, unknown> | null) {
    const settings = identity;
    if (settings === "forbidden") return forbidden(route);
    if (path === "/v1/admin/identity" && method === "GET") return json(route, 200, settings);
    if (method !== "GET") log.identityWrites.push({ method, path, body });
    const oidc = settings.oidc;
    const changed = (next: Partial<S["OidcConnectionOut"]>) => {
      identity = { ...settings, oidc: oidc ? { ...oidc, ...next, version: `2026-10-13T10:${String(log.identityWrites.length).padStart(2, "0")}:00.000000Z` } : null };
      return json(route, 200, identity);
    };
    if (path === "/v1/admin/identity/oidc/test-sign-in" && method === "POST") {
      const id = "c0ffee00-0000-4000-8000-000000000001";
      // The provider's round trip, simulated: straight back to the page with the result.
      return json(route, 201, { test_id: id, authorization_url: `/settings/identity?test=${id}` });
    }
    const test = /^\/v1\/admin\/identity\/oidc\/test-sign-in\/([^/]+)$/.exec(path);
    if (test && method === "GET") {
      return json(route, 200, options.testSignIn ?? {
        test_id: test[1],
        status: "completed",
        outcome: "matched",
        identifier: "sub-lena",
        issuer: oidc?.issuer ?? "",
        subject_claim: oidc?.subject_claim ?? "sub",
        proposed: false,
        completed_at: "2026-10-13T10:00:00Z",
        expires_at: "2026-10-13T10:10:00Z",
        proof_available: false,
      });
    }
    const one = /^\/v1\/admin\/identity\/oidc\/([^/]+)(\/.*)?$/.exec(path);
    if (one && oidc && one[1] === oidc.id) {
      const rest = one[2] ?? "";
      if (rest === "" && method === "PATCH") return changed({ client_id: String(body?.client_id) });
      if (rest === "/client-secret" && method === "PUT") return changed({ client_secret: { state: "encrypted", set_at: "2026-10-13T10:00:00Z" } });
      if (rest === "/client-secret/remove" && method === "POST") return changed({ client_secret: { state: "none", set_at: null } });
      if (rest === "/status/preview" && method === "POST")
        return json(route, 200, { enabled: Boolean(body?.enabled), sessions_ending: body?.enabled ? 0 : oidc.live_sessions, members_bound: oidc.members_bound, ends_own_session: false });
      if (rest === "/status" && method === "POST") return changed({ status: body?.enabled ? "active" : "disabled", live_sessions: 0 });
      if (rest === "/identity/preview" && method === "POST") {
        const issuer = String(body?.issuer ?? oidc.issuer);
        const claim = String(body?.subject_claim ?? oidc.subject_claim);
        return json(route, 200, options.identityPreview ?? {
          issuer,
          subject_claim: claim,
          issuer_changed: issuer !== oidc.issuer,
          claim_changed: claim !== oidc.subject_claim,
          issuer_access: "public",
          sessions_ending: oidc.live_sessions,
          members_bound: oidc.members_bound,
          ends_own_session: false,
          proof: null,
        });
      }
      if (rest === "/identity" && method === "POST") return changed({ issuer: String(body?.issuer), subject_claim: String(body?.subject_claim), live_sessions: 0 });
      if (rest === "/check" && method === "POST") return json(route, 200, options.check ?? { result: "valid", step: "client", checked_at: "2026-10-13T10:00:00Z" });
    }
    if (path === "/v1/admin/identity/scim/tokens" && method === "POST") {
      identity = { ...settings, scim: { ...settings.scim, tokens: [REVEALED_TOKEN.token, ...settings.scim.tokens] } };
      return json(route, 201, REVEALED_TOKEN);
    }
    const token = /^\/v1\/admin\/identity\/scim\/tokens\/([^/]+)\/(revoke\/preview|revoke|rotate)$/.exec(path);
    const found = token ? settings.scim.tokens.find((t) => t.id === token[1]) : undefined;
    if (token && found && method === "POST") {
      if (token[2] === "revoke/preview")
        return json(route, 200, { last_live_token: settings.scim.tokens.filter((t) => !t.revoked_at).length === 1, provisioned_members: settings.scim.provisioned_members });
      const revoked = { ...found, revoked_at: "2026-10-13T10:00:00Z" };
      const tokens = settings.scim.tokens.map((t) => (t.id === found.id ? revoked : t));
      if (token[2] === "revoke") {
        identity = { ...settings, scim: { ...settings.scim, tokens } };
        return json(route, 200, revoked);
      }
      identity = { ...settings, scim: { ...settings.scim, tokens: [REVEALED_TOKEN.token, ...tokens] } };
      return json(route, 200, REVEALED_TOKEN);
    }
    return json(route, 404, NOT_FOUND);
  }
}
