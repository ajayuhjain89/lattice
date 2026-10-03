import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUDIT, FIXTURE_NOW, ID, ME_LENA, ME_MAYA, MEMBERS_PAGE } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { AccessScreen } from "./AccessScreen";
import { AuditScreen } from "./AuditScreen";

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"], now: new Date(FIXTURE_NOW) }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function loaded(name: string) {
  const table = await screen.findByRole("table", { name });
  await vi.waitFor(() => expect(table).not.toHaveAttribute("aria-busy"));
  return table;
}

describe("AccessScreen", () => {
  it("lists members with their broad access, in words", async () => {
    const managed = { ...MEMBERS_PAGE.items[2]!, id: "a1000000-0000-4000-8000-0000000000aa", name: "Pat Rivera", managed_by: "identity_provider", status: "suspended" as const };
    mockFetch({ "GET /v1/admin/access/members": [200, { items: [...MEMBERS_PAGE.items, managed], next_after: null }] });
    renderInWorkspace(<AccessScreen />, { me: ME_LENA });
    const table = await loaded("Members and their access");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("Grace Whitfield");
    expect(rows[0]).toHaveTextContent("Confidential");
    expect(rows[0]).toHaveTextContent("All accounts");
    expect(rows[1]).toHaveTextContent("IT Administrator · Administrator");
    expect(rows[2]).toHaveTextContent("2 assigned");
    expect(rows[3]).toHaveTextContent("Suspended");
    expect(rows[3]).toHaveTextContent("Managed by your identity provider");
    expect(within(table).getByRole("link", { name: "Maya Okafor" })).toHaveAttribute("href", `/access/members/${ID.maya}`);
    expect(screen.getByRole("status")).toHaveTextContent("4 members shown");
  });

  it("searches through the API and says when nothing matches", async () => {
    const calls = mockFetch({ "GET /v1/admin/access/members": [200, { items: [], next_after: null }] });
    renderInWorkspace(<AccessScreen />, { me: ME_LENA });
    await loaded("Members and their access");
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a member" }), "zz");
    expect(await screen.findByRole("heading", { name: "No member matches “zz”" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("No member matches “zz”");
    // The search is the API's: the query goes to the members endpoint, nowhere else.
    await vi.waitFor(() => expect(calls.some((c) => c.url === "/v1/admin/access/members")).toBe(true));
    expect(new Set(calls.map((c) => c.url))).toEqual(new Set(["/v1/admin/access/members"]));
  });

  it("shows members the administrators-only page and asks nothing", async () => {
    const calls = mockFetch({});
    renderInWorkspace(<AccessScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You need administrator access to view this page" })).toBeInTheDocument();
    expect(calls).toEqual([]);
  });
});

describe("AuditScreen", () => {
  it("rules the ledger by day, newest first: time, what, the change, by whom and why", async () => {
    vi.setSystemTime(new Date("2026-10-03T08:00:00Z"));
    mockFetch({ "GET /v1/admin/audit-events": [200, AUDIT] });
    renderInWorkspace(<AuditScreen />, { me: ME_LENA });
    await screen.findByText("Assigned Kestrel Bank");
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Yesterday2 Oct 2026 · 1 change",
      "Tue 29 Sep 20261 change",
      "Mon 28 Sep 20262 changes",
    ]);
    const entries = screen.getAllByTestId("audit-entry");
    expect(entries).toHaveLength(4);
    expect(entries[0]).toHaveTextContent("05:31:14");
    expect(entries[0]).toHaveTextContent("Access changed");
    expect(entries[0]).toHaveTextContent("By Lena Brandt · “Covering Kestrel during the renewal”");
    expect(entries[1]).toHaveTextContent("Employee handbook");
    expect(entries[1]).toHaveTextContent("By admin-grants.yaml");
    // Identity events from the identity provider (Phase 10), field by field.
    expect(entries[2]).toHaveTextContent("Profile updated");
    expect(entries[2]).toHaveTextContent("NamePat Doe → changed to Pat Rivera");
    expect(entries[2]).toHaveTextContent("By Your identity provider (SCIM)");
    expect(screen.getByText("No older changes.")).toBeInTheDocument();
    expect(screen.getByText(/Entries can't be edited or deleted/)).toBeInTheDocument();
  });

  it("pages older events by the API's cursor only", async () => {
    const first = { items: AUDIT.items.slice(0, 2), next_before: AUDIT.items[1]!.id };
    const calls = mockFetch({ "GET /v1/admin/audit-events": [200, first] });
    renderInWorkspace(<AuditScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Show older changes" }));
    await vi.waitFor(() => expect(calls.filter((c) => c.url === "/v1/admin/audit-events")).toHaveLength(2));
  });

  it("names a former member without a link", async () => {
    const gone = { ...AUDIT.items[0]!, member: { id: ID.maya, name: null } };
    mockFetch({ "GET /v1/admin/audit-events": [200, { items: [gone], next_after: null, next_before: null }] });
    renderInWorkspace(<AuditScreen />, { me: ME_LENA });
    expect(await screen.findByText("A former member")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "A former member" })).toBeNull();
  });
});
