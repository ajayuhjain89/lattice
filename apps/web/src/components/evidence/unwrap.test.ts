import { describe, expect, it } from "vitest";

import { unwrap } from "./EvidenceBody";

describe("unwrap", () => {
  it("joins hard-wrapped lines, keeps paragraphs, lists and headings, and preserves length", () => {
    const text = "Derek's ask: once on Okta,\nusers must lose access.\n\n- first\n- second\n# Heading\n1. one";
    const out = unwrap(text);
    expect(out).toBe("Derek's ask: once on Okta, users must lose access.\n\n- first\n- second\n# Heading\n1. one");
    expect(out).toHaveLength(text.length);
  });
});
