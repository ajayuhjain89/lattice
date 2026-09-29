import { describe, expect, it } from "vitest";

import { TRACE_ADMIN, TRACE_ASKER, TRACE_HIDDEN } from "@/test/fixtures";

import { isAskerTrace, storedAnswer, traceTimings, traceValidation } from "./trace-model";

describe("trace model", () => {
  it("reads the stored answer only when the API returns it", () => {
    expect(storedAnswer(TRACE_ASKER)?.claims).toHaveLength(3);
    expect(storedAnswer(TRACE_HIDDEN)).toBeNull();
    // Hidden wins even if some answer object were present.
    expect(storedAnswer({ ...TRACE_HIDDEN, answer: TRACE_ASKER.answer })).toBeNull();
  });

  it("treats a malformed stored answer as absent instead of guessing", () => {
    expect(storedAnswer({ ...TRACE_ASKER, answer: { claims: [{ text: 1 }] } })).toBeNull();
    expect(storedAnswer({ ...TRACE_ASKER, answer: { claims: "nope" } })).toBeNull();
  });

  it("parses timings and validation defensively", () => {
    expect(traceTimings(TRACE_ASKER)).toEqual({ retrieval: 301.2, policy: 3.1, generation: 3890.4, total: 4212.7 });
    expect(traceTimings({ ...TRACE_ADMIN, timings_ms: { total: "x" } }).total).toBeNull();
    expect(traceValidation(TRACE_ASKER).rejectionCodes).toEqual([["quote_not_found", 1]]);
  });

  it("distinguishes the asker view from the operational view by the API's own field", () => {
    expect(isAskerTrace(TRACE_ASKER)).toBe(true);
    expect(isAskerTrace(TRACE_ADMIN)).toBe(false);
  });
});
