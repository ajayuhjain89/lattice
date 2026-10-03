import { describe, expect, it } from "vitest";

import { afterSignIn, safeNextPath } from "./next-path";

describe("safeNextPath", () => {
  it.each(["/ask", "/access/members/40000000-0000-4000-8000-000000000001", "/traces/abc?x=1#y", "/sources", "/"])("keeps the same-origin path %s", (path) => {
    expect(safeNextPath(path)).toBe(path);
  });

  it.each([
    null,
    "",
    "ask",
    "https://evil.example/ask",
    "//evil.example",
    "/\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "/a\\b",
    "/\x00",
    "/\x7f",
    "javascript:alert(1)",
    `/${"a".repeat(512)}`,
    "/sign-in",
    "/sign-in?next=/ask",
    "/sso/halden-login-key-0123456789abc",
  ])("refuses %j", (value) => {
    expect(safeNextPath(value)).toBeNull();
  });

  it("falls back to Ask", () => {
    expect(afterSignIn("//evil.example")).toBe("/ask");
    expect(afterSignIn(null)).toBe("/ask");
    expect(afterSignIn("/activity")).toBe("/activity");
  });
});
