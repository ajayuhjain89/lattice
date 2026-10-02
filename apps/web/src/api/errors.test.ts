import { describe, expect, it } from "vitest";

import { fromResponse, retryAfterSeconds, retryPhrase } from "./errors";

describe("rate-limit errors", () => {
  it("reads Retry-After seconds only in their delta form", () => {
    expect(retryAfterSeconds("17")).toBe(17);
    expect(retryAfterSeconds(" 3 ")).toBe(3);
    expect(retryAfterSeconds("9999999")).toBeNull();
    expect(retryAfterSeconds("Wed, 21 Oct 2026 07:28:00 GMT")).toBeNull();
    expect(retryAfterSeconds("-1")).toBeNull();
    expect(retryAfterSeconds(null)).toBeNull();
    expect(retryAfterSeconds("200000")).toBe(86_400);
  });

  it("carries the limit and its wait on the error", () => {
    const response = new Response(null, { status: 429, headers: { "retry-after": "42", "x-request-id": "r" } });
    const error = fromResponse(response, {
      error: { code: "rate_limited", message: "Too many requests. Wait a moment and try again.", request_id: "r" },
    });
    expect(error.isRateLimited).toBe(true);
    expect(error.retryAfter).toBe(42);
    expect(error.message).toBe("Too many requests. Wait a moment and try again.");
  });

  it("phrases the wait", () => {
    expect(retryPhrase(null)).toBe("in a moment");
    expect(retryPhrase(1)).toBe("in 1 second");
    expect(retryPhrase(40)).toBe("in 40 seconds");
    expect(retryPhrase(600)).toBe("in 10 minutes");
    expect(retryPhrase(43_200)).toBe("in about 12 hours");
  });
});
