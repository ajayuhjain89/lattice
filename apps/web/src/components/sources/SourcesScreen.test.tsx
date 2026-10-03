import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FAILURES, FIXTURE_NOW, ME_LENA, ME_MAYA, SOURCE, SOURCE_FAILED, SOURCES_ADMIN, SOURCES_MEMBER } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { SourcesScreen } from "./SourcesScreen";

const TABLE = "Connected sources and their sync status";
const QUEUED = { ...SOURCES_ADMIN.sources[0]!.job!, id: "7f2a1b0c-9999-4d3e-9f4a-1b2c3d4e5f99", source_id: SOURCE.slack, status: "queued", created_at: "2026-09-29T09:10:00Z", started_at: null };

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"], now: new Date(FIXTURE_NOW) }));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** The table once its rows have arrived (loading rows hold its geometry until then). */
async function loaded(name: string) {
  const table = await screen.findByRole("table", { name });
  await vi.waitFor(() => expect(table).not.toHaveAttribute("aria-busy"));
  return table;
}

const row = (table: HTMLElement, name: string) => within(table).getAllByRole("row").find((r) => r.textContent?.includes(name))!;

describe("SourcesScreen", () => {
  it("shows members only their sources, with their own counts, and no controls", async () => {
    const calls = mockFetch({ "GET /v1/sources": [200, SOURCES_MEMBER] });
    renderInWorkspace(<SourcesScreen />, { me: ME_MAYA });
    const table = await loaded("Sources you can read from");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(row(table, "Halden Drive")).toHaveTextContent("41 documents");
    expect(row(table, "Halden Drive")).toHaveTextContent("Synced");
    // Partial: synced, but the last full sync is eight days old -- the time signal, in words.
    const support = row(table, "Halden Support");
    expect(support).toHaveTextContent("Partial");
    expect(support).toHaveTextContent("Last full sync 21 Sep 2026 · 8 days ago");
    expect(support.querySelector("[data-old]")).not.toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    // A member never asks for the administrator view.
    expect(calls.map((c) => c.url)).toEqual(["/v1/sources"]);
  });

  it("says so when there is nothing a member can read", async () => {
    mockFetch({ "GET /v1/sources": [200, { sources: [] }] });
    renderInWorkspace(<SourcesScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "Lattice hasn't indexed any sources you can access yet" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reads each administrator row from the listing: healthy, partial, never synced", async () => {
    mockFetch({ "GET /v1/admin/sources": [200, SOURCES_ADMIN] });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await loaded(TABLE);
    expect(screen.getByTestId("register-summary")).toHaveTextContent(
      "3 sources: 1 syncing now · 1 with failed records · 1 last fully synced over a week ago · 1 never synced.",
    );
    const drive = row(table, "Halden Drive");
    expect(drive).toHaveTextContent("Synced28 Sep 2026, 07:12");
    expect(drive).toHaveTextContent("Follows the source's sharing");
    expect(drive).toHaveTextContent("Syncing since 09:00");
    expect(within(drive).getByRole("button", { name: "Sync now: Halden Drive" })).toHaveAttribute("aria-disabled", "true");
    const support = row(table, "Halden Support");
    expect(support).toHaveTextContent("Follows the source, by account");
    expect(support).toHaveTextContent("1 record");
    expect(within(support).queryByText("Failed", { exact: true })).toBeNull(); // record failures aren't a failed source
    const slack = row(table, "Halden Slack");
    expect(slack).toHaveTextContent("Never synced");
    expect(slack).toHaveTextContent("Not applied yet");
    expect(within(slack).getByRole("button", { name: "Sync now: Halden Slack" })).not.toHaveAttribute("aria-disabled");
  });

  it("marks a failed source as a fault with its code, separately from records", async () => {
    mockFetch({ "GET /v1/admin/sources": [200, { sources: [SOURCE_FAILED] }] });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await loaded(TABLE);
    const wiki = row(table, "Halden Wiki");
    expect(wiki).toHaveTextContent("Failed");
    expect(wiki).toHaveTextContent("source_unavailable:timeout");
    expect(wiki).toHaveTextContent("Last full sync 12 Sep 2026 · 17 days ago");
    expect(wiki).toHaveTextContent("Requested sync failed");
    expect(screen.getByTestId("register-summary")).toHaveTextContent("1 whose last sync failed");
    expect(within(wiki).queryByRole("button", { name: /failed records/ })).toBeNull();
  });

  it("requests a sync once, with the CSRF header, and announces it", async () => {
    let listing = SOURCES_ADMIN;
    const calls = mockFetch({
      get "GET /v1/admin/sources"() {
        return [200, listing] as [number, unknown];
      },
      [`POST /v1/admin/sources/${SOURCE.slack}/sync`]: [202, QUEUED],
    });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await loaded(TABLE);
    const button = within(table).getByRole("button", { name: "Sync now: Halden Slack" });
    listing = { sources: SOURCES_ADMIN.sources.map((s) => (s.id === SOURCE.slack ? { ...s, job: QUEUED } : s)) } as typeof SOURCES_ADMIN;
    await userEvent.click(button);
    expect(await screen.findByTestId("sync-announcer")).toHaveTextContent("Sync requested for Halden Slack. It is queued.");
    await vi.waitFor(() => expect(row(table, "Halden Slack")).toHaveTextContent("Queued since 09:10"));
    // Inert while queued: a second activation sends nothing.
    await userEvent.click(within(row(table, "Halden Slack")).getByRole("button", { name: "Sync now: Halden Slack" }));
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.headers.get("X-Lattice-Request")).toBe("1");
    expect(document.activeElement).toHaveAccessibleName("Sync now: Halden Slack");
  });

  it("keeps a refused request in the row, as an alert", async () => {
    mockFetch({
      "GET /v1/admin/sources": [200, SOURCES_ADMIN],
      [`POST /v1/admin/sources/${SOURCE.slack}/sync`]: [
        422,
        { error: { code: "validation_failed", message: "This source isn't active.", request_id: "r", details: [{ field: "source", issue: "source_not_active" }] } },
      ],
    });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await loaded(TABLE);
    await userEvent.click(within(table).getByRole("button", { name: "Sync now: Halden Slack" }));
    expect(await within(row(table, "Halden Slack")).findByRole("alert")).toHaveTextContent("Couldn't request a sync: This source isn't active. Try again.");
  });

  it("opens failed records inline, by opaque reference, loaded on demand", async () => {
    const calls = mockFetch({
      "GET /v1/admin/sources": [200, SOURCES_ADMIN],
      [`GET /v1/admin/sources/${SOURCE.support}/failures`]: [200, FAILURES],
    });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const toggle = await screen.findByRole("button", { name: "Show failed records: Halden Support (Zendesk)" });
    expect(calls.some((c) => c.url.endsWith("/failures"))).toBe(false);
    await userEvent.click(toggle);
    const records = await screen.findByRole("table", { name: "Failed records in the last sync of Halden Support (Zendesk)" });
    expect(records).toHaveTextContent("rec_3f9a0c1d2e4b5a67");
    expect(records).toHaveTextContent("opaque reference");
    expect(records).toHaveTextContent("External account ref: 360009913");
    expect(screen.getByRole("button", { name: "Hide failed records: Halden Support (Zendesk)" })).toHaveAttribute("aria-expanded", "true");
  });

  it("says what to do when nothing is connected", async () => {
    mockFetch({ "GET /v1/admin/sources": [200, { sources: [] }] });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "No sources are connected" })).toBeInTheDocument();
    expect(screen.getByText("lattice sync")).toBeInTheDocument();
  });
});
