import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DETAILS, EV } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { EvidenceInspector } from "./EvidenceInspector";

afterEach(() => vi.unstubAllGlobals());

const open = (id: string) =>
  renderInWorkspace(<EvidenceInspector evidenceId={id} context={null} onClose={vi.fn()} onNavigate={vi.fn()} focusOnOpen={false} />);

describe("EvidenceInspector", () => {
  it("shows what the evidence endpoint returned", async () => {
    mockFetch({ [`GET /v1/evidence/${EV.decision}`]: [200, DETAILS[EV.decision]] });
    open(EV.decision);
    expect(await screen.findByRole("heading", { name: "DR-112 · Identity roadmap decision" })).toBeInTheDocument();
    expect(screen.getByText("System of record")).toBeInTheDocument();
  });

  it("renders one identical state for a 404, whatever its cause, and for a malformed id", async () => {
    const texts: string[] = [];
    for (const id of [EV.missing, EV.escalation, "not-a-uuid"]) {
      const calls = mockFetch({});
      const { container, unmount } = open(id);
      await screen.findByRole("heading", { name: "This evidence isn't available" });
      texts.push(container.textContent ?? "");
      if (id === "not-a-uuid") expect(calls).toHaveLength(0);
      unmount();
      vi.unstubAllGlobals();
    }
    expect(new Set(texts).size).toBe(1);
  });

  it("does not show the account row when the API omits the account (D-8)", async () => {
    mockFetch({ [`GET /v1/evidence/${EV.decision}`]: [200, { ...DETAILS[EV.decision], account: null }] });
    open(EV.decision);
    await screen.findByRole("heading", { name: /Identity roadmap decision/ });
    expect(screen.queryByText("Account")).toBeNull();
  });
});
