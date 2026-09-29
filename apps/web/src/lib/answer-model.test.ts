import { describe, expect, it } from "vitest";

import { ANSWERED, DANGLING, EV, SUPERSEDED } from "@/test/fixtures";

import { buildAnswerModel, excerpt, groupEvidence, locateQuote, markRanges } from "./answer-model";

const available = (a: { evidence: { evidence_id: string }[] }) => new Set(a.evidence.map((e) => e.evidence_id));

describe("buildAnswerModel", () => {
  it("numbers citations in reading order: direct answers, supporting claims, then conflicts", () => {
    const model = buildAnswerModel(ANSWERED, available(ANSWERED));
    expect(model.lead.map((c) => c.citations.map((x) => x.number))).toEqual([[1, 1]]);
    expect(model.supporting.map((c) => c.citations[0]?.number)).toEqual([2, 3]);
    expect(model.cited).toEqual([EV.decision, EV.kickoff, EV.ticket]);
    expect(model.conflicts[0]?.positions.map((p) => p.claim.citations[0]?.number)).toEqual([3, 1]);
  });

  it("never renders a claim whose citation is missing from the response (no stripped citations)", () => {
    const model = buildAnswerModel(DANGLING, available(DANGLING));
    const all = [...model.lead, ...model.supporting];
    expect(all.map((c) => c.text)).not.toContain("This claim cites evidence that is not in the response.");
    expect(all.flatMap((c) => c.citations).every((c) => available(DANGLING).has(c.evidence_id))).toBe(true);
    expect(model.numberOf.has(EV.missing)).toBe(false);
    expect(model.dropped).toBe(1);
  });

  it("drops a conflict that loses a position, rather than showing one side", () => {
    const withoutTicket = new Set([...available(ANSWERED)].filter((id) => id !== EV.ticket));
    const model = buildAnswerModel(ANSWERED, withoutTicket);
    expect(model.conflicts).toHaveLength(0);
  });

  it("keeps positions in date order with no winner field", () => {
    const model = buildAnswerModel(ANSWERED, available(ANSWERED));
    const dates = model.conflicts[0]!.positions.map((p) => p.occurredAt);
    expect(dates).toEqual([...dates].sort());
    expect(Object.keys(model.conflicts[0]!)).not.toContain("winner");
  });

  it("keeps a supersession only with its recorded link", () => {
    const model = buildAnswerModel(SUPERSEDED, available(SUPERSEDED));
    expect(model.conflicts.map((c) => c.kind)).toEqual(["superseded"]);
    const unlinked = buildAnswerModel({ ...SUPERSEDED, conflicts: [{ ...SUPERSEDED.conflicts[0]!, supersession: null }] }, available(SUPERSEDED));
    expect(unlinked.conflicts).toHaveLength(0);
  });
});

describe("groupEvidence", () => {
  it("separates cited, policy-excluded and other evidence without losing any", () => {
    const model = buildAnswerModel(ANSWERED, available(ANSWERED));
    const g = groupEvidence(ANSWERED.evidence, model);
    expect(g.cited.map((e) => e.evidence_id)).toEqual([EV.decision, EV.kickoff, EV.ticket]);
    expect(g.policyExcluded.map((e) => e.evidence_id)).toEqual([EV.escalation]);
    expect(g.other.map((e) => e.evidence_id)).toEqual([EV.slack, EV.roadmap]);
  });
});

describe("quotes", () => {
  it("trusts offsets only when they select the quote", () => {
    expect(locateQuote("abc quote def", "quote", 4, 9)).toEqual({ start: 4, end: 9 });
    expect(locateQuote("abc quote def", "quote", 0, 5)).toEqual({ start: 4, end: 9 });
    expect(locateQuote("abc", "missing", 0, 3)).toBeNull();
  });

  it("merges overlapping marks", () => {
    expect(markRanges("abcdef", [{ start: 1, end: 3 }, { start: 2, end: 4 }])).toEqual([
      { text: "a", marked: false },
      { text: "bcd", marked: true },
      { text: "ef", marked: false },
    ]);
  });

  it("keeps the quote visible in an excerpt of a long text", () => {
    const text = `${"x ".repeat(400)}the quote${" y".repeat(400)}`;
    const at = locateQuote(text, "the quote");
    const e = excerpt(text, at);
    expect(e.quote).toBe("the quote");
    expect(e.before.startsWith("…")).toBe(true);
    expect(e.after.endsWith("…")).toBe(true);
  });
});
