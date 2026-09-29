import { afterEach, describe, expect, it, vi } from "vitest";

import { mockFetch } from "@/test/render";
import { ANSWERED, ME_MAYA } from "@/test/fixtures";

import { api } from "./client";
import { ApiError } from "./errors";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the CSRF proof header on state-changing requests only", async () => {
    const calls = mockFetch({ "GET /v1/me": [200, ME_MAYA], "POST /v1/ask": [200, ANSWERED] });
    await api.me();
    await api.ask({ question: "q", scope: {} });
    expect(calls[0]?.headers.get("x-lattice-request")).toBeNull();
    expect(calls[1]?.headers.get("x-lattice-request")).toBe("1");
  });

  it("normalizes the error envelope and keeps only its safe fields", async () => {
    mockFetch({ "GET /v1/evidence/e0000000-0000-4000-8000-00000000dead": [404, { error: { code: "not_found", message: "Not found.", request_id: "abc" } }] });
    const error = await api.evidence("e0000000-0000-4000-8000-00000000dead").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "http", status: 404, code: "not_found", message: "Not found.", requestId: "abc" });
  });

  it("never renders a non-envelope body", async () => {
    mockFetch({ "GET /v1/me": [502, "<html>proxy stack trace</html>"] });
    const error = (await api.me().catch((e: unknown) => e)) as ApiError;
    expect(error.message).not.toContain("stack");
    expect(error.code).toBeNull();
  });

  it("reports an unreachable server as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const error = (await api.me().catch((e: unknown) => e)) as ApiError;
    expect(error.kind).toBe("network");
  });
});
