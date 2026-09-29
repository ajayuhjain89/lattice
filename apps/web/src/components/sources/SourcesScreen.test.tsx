import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FAILURES, ME_LENA, ME_MAYA, SOURCE, SOURCES_ADMIN, SOURCES_MEMBER } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { SourcesScreen } from "./SourcesScreen";

afterEach(() => vi.unstubAllGlobals());

describe("SourcesScreen", () => {
  it("shows members only their sources, with their own counts, and no controls", async () => {
    const calls = mockFetch({ "GET /v1/sources": [200, SOURCES_MEMBER] });
    renderInWorkspace(<SourcesScreen />, { me: ME_MAYA });
    const table = await screen.findByRole("table", { name: "Sources you can read from" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(table).toHaveTextContent("41");
    expect(table).toHaveTextContent("Partial");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    // A member never asks for the administrator view.
    expect(calls.map((c) => c.url)).toEqual(["/v1/sources"]);
  });

  it("says so when there is nothing a member can read", async () => {
    mockFetch({ "GET /v1/sources": [200, { sources: [] }] });
    renderInWorkspace(<SourcesScreen />, { me: ME_MAYA });
    expect(await screen.findByText(/hasn't indexed any sources you can access yet/)).toBeInTheDocument();
  });

  it("shows administrators totals, sync state and failure codes", async () => {
    mockFetch({ "GET /v1/admin/sources": [200, SOURCES_ADMIN] });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Connected sources and their sync status" });
    expect(table).toHaveTextContent("214");
    expect(table).toHaveTextContent("access_unmappable:account");
    expect(table).toHaveTextContent("Never synced");
    expect(within(table).getByRole("button", { name: "Syncing…: Halden Drive" })).toBeDisabled();
    expect(within(table).getByRole("button", { name: "Sync now: Halden Slack" })).toBeEnabled();
  });

  it("requests a sync with the CSRF header and refreshes the listing", async () => {
    const calls = mockFetch({
      "GET /v1/admin/sources": [200, SOURCES_ADMIN],
      [`POST /v1/admin/sources/${SOURCE.slack}/sync`]: [202, { ...SOURCES_ADMIN.sources[0]!.job, source_id: SOURCE.slack, status: "queued" }],
    });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Connected sources and their sync status" });
    await userEvent.click(within(table).getByRole("button", { name: "Sync now: Halden Slack" }));
    await vi.waitFor(() => expect(calls.filter((c) => c.url === "/v1/admin/sources")).toHaveLength(2));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe(`/v1/admin/sources/${SOURCE.slack}/sync`);
    expect(post?.headers.get("X-Lattice-Request")).toBe("1");
  });

  it("shows failed records by opaque reference, loaded on demand", async () => {
    const calls = mockFetch({
      "GET /v1/admin/sources": [200, SOURCES_ADMIN],
      [`GET /v1/admin/sources/${SOURCE.support}/failures`]: [200, FAILURES],
    });
    renderInWorkspace(<SourcesScreen />, { me: ME_LENA });
    const toggle = await screen.findByRole("button", { name: "Show records" });
    expect(calls.some((c) => c.url.endsWith("/failures"))).toBe(false);
    await userEvent.click(toggle);
    expect(await screen.findByText("rec_3f9a0c1d2e4b5a67")).toBeInTheDocument();
    expect(screen.getByText(/External account ref: 360009913/)).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
