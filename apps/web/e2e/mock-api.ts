import type { Page, Route } from "@playwright/test";

import type { components } from "../src/api/schema.gen";
import { DETAILS, ME_MAYA, NOT_FOUND, PROVIDERS, TRACE_ADMIN, TRACE_ASKER, TRACE_HIDDEN } from "../src/test/fixtures";

type S = components["schemas"];

export interface MockOptions {
  me?: S["MeResponse"] | "unauthenticated";
  /** Answer for each POST /v1/ask, chosen from the request body. */
  ask?: (body: S["AskRequest"]) => S["Answer"] | { status: number; body: unknown } | "network";
  evidence?: Record<string, S["EvidenceDetail"]>;
  traces?: Record<string, S["AskerTrace"] | S["AdminTrace"]>;
  providers?: S["ProvidersResponse"] | "forbidden";
  /** Milliseconds before the Ask response (to observe the pending state). */
  askDelay?: number;
}

export interface MockLog {
  asks: S["AskRequest"][];
  evidence: string[];
  traces: string[];
  headers: Record<string, string>[];
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body), headers: { "x-request-id": "req-fixture" } });

/** Fulfil every /api/* request from typed fixtures; anything unknown is a 404 envelope. */
export async function mockApi(page: Page, options: MockOptions = {}): Promise<MockLog> {
  const log: MockLog = { asks: [], evidence: [], traces: [], headers: [] };
  const evidence = options.evidence ?? DETAILS;
  const traces = options.traces ?? {
    [TRACE_ASKER.trace_id]: TRACE_ASKER,
    [TRACE_HIDDEN.trace_id]: TRACE_HIDDEN,
    [TRACE_ADMIN.trace_id]: TRACE_ADMIN,
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
    if (path === "/v1/auth/dev-login" && method === "POST") return json(route, 200, options.me ?? ME_MAYA);
    if (path === "/v1/auth/logout" && method === "POST") return route.fulfill({ status: 204 });
    if (path === "/v1/ask" && method === "POST") {
      const body = request.postDataJSON() as S["AskRequest"];
      log.asks.push(body);
      if (options.askDelay) await new Promise((r) => setTimeout(r, options.askDelay));
      const result = options.ask?.(body);
      if (!result) return json(route, 500, { error: { code: "internal_error", message: "No fixture.", request_id: "req-500" } });
      if (result === "network") return route.abort("connectionrefused");
      if ("status" in result && "body" in result && typeof result.status === "number") return json(route, result.status, result.body);
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
    return json(route, 404, NOT_FOUND);
  });
  return log;
}
