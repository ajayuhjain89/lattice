import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACTIVITY_ADMIN, ACTIVITY_OWN, FIXTURE_NOW, ID, ME_LENA, ME_MAYA, TRACE } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { ActivityScreen } from "./ActivityScreen";
import { OrganizationActivityScreen } from "./OrganizationActivityScreen";

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"], now: new Date(FIXTURE_NOW) }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ActivityScreen", () => {
  it("lists a member's own questions by day, each at its time, linking to its trace", async () => {
    const calls = mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN] });
    renderInWorkspace(<ActivityScreen />, { me: ME_MAYA });
    const link = await screen.findByRole("link", { name: "When will SCIM ship for Acme, and what did we tell them?" });
    expect(link).toHaveAttribute("href", `/traces/${TRACE.answered}`);
    const days = screen.getAllByRole("heading", { level: 3 });
    expect(days.map((d) => d.textContent)).toEqual(["Today29 Sep 2026 · 1 question", "Yesterday28 Sep 2026 · 1 question"]);
    const today = days[0]!.closest("li")!;
    expect(today).toHaveTextContent("08:41");
    expect(today).toHaveTextContent("Answered");
    expect(screen.getByText("No older questions.")).toBeInTheDocument();
    // Members get no view switch, and never ask for the organization's runs.
    expect(screen.queryByRole("navigation", { name: "Activity" })).toBeNull();
    expect(calls.map((c) => c.url)).toEqual(["/v1/activity"]);
  });

  it("puts a question back in the composer with Ask again", async () => {
    mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN] });
    const { state } = renderInWorkspace(<ActivityScreen />, { me: ME_MAYA });
    await userEvent.click(await screen.findByRole("button", { name: "Ask again: What did Kestrel agree to in the renewal?" }));
    expect(state.prefill.get()).toBe("What did Kestrel agree to in the renewal?");
  });

  it("teaches what Activity records when it is empty", async () => {
    mockFetch({ "GET /v1/activity": [200, { items: [] }] });
    renderInWorkspace(<ActivityScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You haven't asked anything yet" })).toBeInTheDocument();
    expect(screen.getByText(/re-checked against what you can read at that moment/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ask a question" })).toHaveAttribute("href", "/ask");
  });

  it("gives administrators a switch to the organization's runs", async () => {
    mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN] });
    renderInWorkspace(<ActivityScreen />, { me: ME_LENA });
    const views = await screen.findByRole("navigation", { name: "Activity" });
    expect(within(views).getByRole("link", { name: "Your questions" })).toHaveAttribute("aria-current", "page");
    expect(within(views).getByRole("link", { name: "Organization runs" })).toHaveAttribute("href", "/activity/organization");
  });
});

describe("OrganizationActivityScreen", () => {
  it("shows administrators how each run went, never what was asked", async () => {
    mockFetch({ "GET /v1/admin/activity": [200, ACTIVITY_ADMIN] });
    renderInWorkspace(<OrganizationActivityScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Organization runs (operational view)" });
    await vi.waitFor(() => expect(table).not.toHaveAttribute("aria-busy"));
    expect(table).toHaveTextContent("Grace Whitfield");
    expect(table).toHaveTextContent("No model call");
    expect(table).toHaveTextContent("4.2 s");
    expect(table).toHaveTextContent("2 calls");
    expect(table.textContent?.match(/No model call/g)).toHaveLength(1);
    expect(within(table).getByRole("link", { name: "Grace Whitfield" })).toHaveAttribute("href", `/access/members/${ID.grace}`);
    expect(within(table).getByRole("link", { name: /^Run details: Maya Okafor/ })).toHaveAttribute("href", `/traces/${TRACE.answered}`);
    expect(screen.getByText(/Questions, answers and evidence aren't visible to administrators/)).toBeInTheDocument();
    expect(table).not.toHaveTextContent("SCIM");
  });

  it("shows members the administrators-only page and asks nothing", async () => {
    const calls = mockFetch({});
    renderInWorkspace(<OrganizationActivityScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You need administrator access to view this page" })).toBeInTheDocument();
    expect(calls).toEqual([]);
  });
});
