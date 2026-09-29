import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ACTIVITY_ADMIN, ACTIVITY_OWN, ME_LENA, ME_MAYA, TRACE } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { ActivityScreen } from "./ActivityScreen";

afterEach(() => vi.unstubAllGlobals());

describe("ActivityScreen", () => {
  it("lists a member's own questions, each linking to its trace", async () => {
    const calls = mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN] });
    renderInWorkspace(<ActivityScreen />, { me: ME_MAYA });
    const link = await screen.findByRole("link", { name: /When will SCIM ship for Acme/ });
    expect(link).toHaveAttribute("href", `/traces/${TRACE.answered}`);
    expect(screen.queryByRole("heading", { name: "Organization activity" })).toBeNull();
    expect(calls.map((c) => c.url)).toEqual(["/v1/activity"]);
  });

  it("shows administrators operational rows and says why content is absent", async () => {
    mockFetch({ "GET /v1/activity": [200, { items: [] }], "GET /v1/admin/activity": [200, ACTIVITY_ADMIN] });
    renderInWorkspace(<ActivityScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Organization activity (operational view)" });
    expect(table).toHaveTextContent("Grace Whitfield");
    expect(table).toHaveTextContent("No model call");
    expect(table).toHaveTextContent("4.2 s");
    expect(table).toHaveTextContent("2 calls");
    expect(table.textContent?.match(/No model call/g)).toHaveLength(1);
    expect(screen.getByText(/Content of other members' questions isn't visible to administrators/)).toBeInTheDocument();
    expect(await screen.findByText("You haven't asked anything yet")).toBeInTheDocument();
  });
});
