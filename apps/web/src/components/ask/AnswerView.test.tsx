import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Answer } from "@/api/types";
import {
  AMBIGUOUS,
  ANSWERED,
  DANGLING,
  EV,
  EVIDENCE_ONLY,
  FAILED,
  INSUFFICIENT,
  PARTIAL,
  SUPERSEDED,
  UNAVAILABLE,
  UNSUPPORTED,
  UNVERIFIED,
} from "@/test/fixtures";
import { renderInWorkspace } from "@/test/render";

import { AnswerView } from "./AnswerView";

function show(answer: Answer, overrides: Partial<Parameters<typeof AnswerView>[0]> = {}) {
  const props = {
    answer,
    request: { question: "When will SCIM ship for Acme?", scope: {} },
    accountName: null,
    draft: "When will SCIM ship for Acme?",
    pending: false,
    onRetry: vi.fn(),
    onChoose: vi.fn(),
    onEdit: vi.fn(),
    ...overrides,
  };
  return { ...renderInWorkspace(<AnswerView {...props} />), props };
}

const chips = () => screen.queryAllByRole("button", { name: /^Citation \d+/ });

describe("AnswerView", () => {
  it("answered: claim prose with citation chips, the conflict both ways, and grouped evidence", () => {
    show(ANSWERED);
    expect(screen.getByTestId("answer-prose")).toHaveTextContent("SCIM general availability to December");
    expect(chips().length).toBeGreaterThan(0);
    const conflict = screen.getByRole("region", { name: "Potential conflict" });
    expect(within(conflict).getByText(/expected to be generally available in October/)).toBeInTheDocument();
    expect(within(conflict).getByText(/moves to December/)).toBeInTheDocument();
    expect(conflict).not.toHaveTextContent(/supersedes|correct|resolved/i);
    expect(screen.getByText(/Not used to draft this answer/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Also retrieved, not cited/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("never renders a chip, or its claim, for evidence absent from the response", () => {
    show(DANGLING);
    expect(screen.queryByText("This claim cites evidence that is not in the response.")).toBeNull();
    for (const chip of chips()) expect(chip).not.toHaveAttribute("data-evidence", EV.missing);
    expect(chips().map((c) => c.textContent)).toEqual(["1"]);
  });

  it("opens the inspector with the chip's evidence and remembers where focus came from", async () => {
    const { state } = show(ANSWERED);
    const chip = chips()[0]!;
    await userEvent.click(chip);
    expect(state.inspector.get().evidenceId).toBe(EV.decision);
    expect(state.inspector.get().returnFocus).toBe(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
  });

  it("partial: the answer plus what the evidence doesn't establish", () => {
    show(PARTIAL);
    expect(screen.getByText("Partial answer")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /What the evidence doesn.t establish/ })).toBeInTheDocument();
    expect(screen.getAllByText("Some statements couldn't be verified against sources and were removed.")).toHaveLength(1);
  });

  it("insufficient_evidence: a quiet state with what was searched, no prose, no error styling", () => {
    show(INSUFFICIENT, { request: { question: "q", scope: { source_types: ["ticket"] } } });
    expect(screen.getByRole("heading", { name: "Nothing you can access answers this" })).toBeInTheDocument();
    expect(screen.getByText("Tickets")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("answer-prose")).toBeNull();
  });

  it("claims_unverified: evidence without prose", () => {
    show(UNVERIFIED);
    expect(screen.getByRole("heading", { name: "No verified answer" })).toBeInTheDocument();
    expect(screen.queryByTestId("answer-prose")).toBeNull();
    expect(screen.getByTestId("evidence")).toBeInTheDocument();
  });

  it("evidence_only (policy): says the evidence is readable and nothing was drafted", () => {
    show(EVIDENCE_ONLY);
    expect(screen.getByRole("heading", { name: "Sources found — not sent to the AI model" })).toBeInTheDocument();
    expect(screen.getByText(/Lattice found sources you can access/)).toBeInTheDocument();
    expect(screen.queryByTestId("answer-prose")).toBeNull();
    expect(within(screen.getByTestId("evidence")).getAllByRole("button")).toHaveLength(2);
  });

  it("evidence_only (no model): a different heading, and no per-item policy note contradicting it", () => {
    show(UNAVAILABLE);
    expect(screen.getByRole("heading", { name: "Sources found — no AI model available" })).toBeInTheDocument();
    expect(screen.queryByText(/AI data policy/)).toBeNull();
  });

  it("ambiguous: only the API's choices, sent back by key", async () => {
    const { props } = show(AMBIGUOUS);
    expect(screen.getByRole("heading", { name: "Which account do you mean?" })).toBeInTheDocument();
    const choices = within(screen.getByRole("group", { name: "Choices" })).getAllByRole("button");
    expect(choices.map((b) => b.textContent)).toEqual(["Acme Corp", "Acme Health"]);
    await userEvent.click(choices[1]!);
    expect(props.onChoose).toHaveBeenCalledWith("acme-health", "Acme Health");
    expect(screen.queryByTestId("evidence")).toBeNull();
  });

  it("unsupported_request: explains, shows no claims, keeps sources collapsed", () => {
    show(UNSUPPORTED);
    expect(screen.getByRole("heading", { name: "Lattice doesn't take actions" })).toBeInTheDocument();
    expect(screen.queryByTestId("answer-prose")).toBeNull();
    expect(screen.getByRole("button", { name: /Sources retrieved for this question/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("failed: a safe message and a retry, with evidence still listed", async () => {
    const { props } = show(FAILED);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByRole("heading", { name: "No answer was drafted" })).toBeInTheDocument();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(props.onRetry).toHaveBeenCalled();
    expect(screen.getByTestId("evidence")).toBeInTheDocument();
  });

  it("supersession: stated as recorded by the source, both documents inspectable, stale notice shown", () => {
    show(SUPERSEDED);
    const block = screen.getByRole("region", { name: "Superseded" });
    expect(block).toHaveTextContent("as recorded by its source");
    expect(within(block).getByRole("button", { name: "Inspect earlier" })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Inspect replacement" })).toBeInTheDocument();
    expect(screen.getByText("The most recent evidence available to you is from 3 Mar 2026.")).toBeInTheDocument();
  });

  it("says which question it answers once the composer text has changed", () => {
    show(ANSWERED, { draft: "something else" });
    expect(screen.getByText(/Answer to/)).toHaveTextContent("When will SCIM ship for Acme?");
  });
});
