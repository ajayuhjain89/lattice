import { describe, expect, it } from "vitest";

import { ApiError } from "@/api/errors";

import { changeError } from "./errors";

const error = (status: number, issues: string[] = [], retryAfter: number | null = null) =>
  new ApiError({ kind: "http", status, code: null, message: "x", requestId: null, issues, retryAfter });

describe("changeError", () => {
  it("points to the identity provider for a provisioned member's lifecycle", () => {
    expect(changeError(error(422, ["managed_by_identity_provider"]), "Pat Rivera")).toBe(
      "Pat Rivera is managed by your identity provider. Suspend or reactivate them there.",
    );
  });

  it("says when to try again after the write limit", () => {
    expect(changeError(error(429, [], 30), "Maya")).toBe("You're making changes quickly. Try again in 30 seconds.");
    expect(changeError(error(429), "Maya")).toBe("You're making changes quickly. Try again in a moment.");
  });
});
