import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ME_LENA, ME_MAYA, SETUP_FRESH, SETUP_IN_USE } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { OrganizationScreen } from "./OrganizationScreen";
import { factLine, STATE } from "./setup-model";

afterEach(() => vi.unstubAllGlobals());

const record = () => screen.findByTestId("setup-record");
const row = (container: HTMLElement, label: string) => within(container).getByRole("listitem", { name: label });

describe("OrganizationScreen", () => {
  it("renders every category the API returns, in its order, with the API's state and message", async () => {
    mockFetch({ "GET /v1/admin/setup": [200, SETUP_IN_USE] });
    renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    const list = await record();
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.querySelector("h3")?.textContent)).toEqual([
      "Organization",
      "Identity provider",
      "Members",
      "Provisioning (SCIM)",
      "AI policy",
      "Sources",
      "Account mapping",
      "First answer",
    ]);
    const ai = row(list, "AI policy");
    expect(ai).toHaveAttribute("data-state", "attention");
    expect(ai).toHaveTextContent("Needs attention");
    expect(ai).toHaveTextContent("The generation provider's credential is missing or cannot be used");
    expect(ai).toHaveTextContent("gemini · gemini-3.5-flash-lite · Questions default to Internal");
    expect(within(ai).getByRole("link", { name: "Open AI providers" })).toHaveAttribute("href", "/settings/providers");
    expect(row(list, "Identity provider")).toHaveTextContent("Last sign-in 8 Oct 2026, 08:41:07 UTC");
    expect(row(list, "Account mapping")).toHaveTextContent("2 unmapped references");
  });

  it("never infers a state: an unexpected category state from the API is shown as the API sent it", async () => {
    const altered = {
      ...SETUP_IN_USE,
      categories: SETUP_IN_USE.categories.map((c) => (c.key === "sources" ? { ...c, state: "required" as const, reason: "no_sources", message: "No source is connected yet." } : c)),
    };
    mockFetch({ "GET /v1/admin/setup": [200, altered] });
    renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    const sources = row(await record(), "Sources");
    // Facts say three sources synced; the API's state wins and nothing is recomputed.
    expect(sources).toHaveTextContent("Required");
    expect(sources).toHaveTextContent("3 sources");
    expect(sources).not.toHaveTextContent("Complete");
  });

  it("says who acts when no page in the product owns the item yet, and offers no dead controls", async () => {
    mockFetch({ "GET /v1/admin/setup": [200, SETUP_FRESH] });
    const { container } = renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    const list = await record();
    // P12.4: identity is the Identity page's; sources still wait for the operator (P12.6).
    expect(within(row(list, "Identity provider")).getByRole("link", { name: "Test sign-in" })).toHaveAttribute("href", "/settings/identity");
    expect(row(list, "Sources")).toHaveTextContent("Your Lattice operator acts on this");
    expect(row(list, "Provisioning (SCIM)")).not.toHaveTextContent("Your Lattice operator acts on this"); // optional
    expect(row(list, "First answer")).not.toHaveTextContent("Your Lattice operator acts on this"); // blocked: waits on Sources
    // The only controls: Rename, and links to pages that exist.
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Rename"]);
    for (const link of within(list).getAllByRole("link")) {
      expect(["/settings/providers", "/settings/identity", "/sources", "/access", "/ask"]).toContain(link.getAttribute("href"));
    }
    expect(container.querySelectorAll("input, select, textarea")).toHaveLength(0);
    expect(container.textContent).not.toMatch(/%|score|progress/i);
  });

  it("renames with a reason and keeps the result on the page", async () => {
    const calls = mockFetch({
      "GET /v1/admin/setup": [200, SETUP_FRESH],
      "PATCH /v1/admin/organization": [200, { ...SETUP_FRESH.organization, name: "Halden Group", version: "2026-10-08T09:05:00Z" }],
    });
    renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    await record();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename the organization" });
    const name = within(dialog).getByRole("textbox", { name: /Name/ });
    await userEvent.clear(name);
    await userEvent.type(name, "Halden Group");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(within(dialog).getByText("Say why: the reason is recorded in the audit log.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Rebrand");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(await screen.findByTestId("renamed")).toHaveTextContent("Renamed to Halden Group");
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.headers.get("X-Lattice-Request")).toBe("1");
  });

  it("explains a conflict without retrying", async () => {
    mockFetch({
      "GET /v1/admin/setup": [200, SETUP_FRESH],
      "PATCH /v1/admin/organization": [409, { error: { code: "conflict", message: "The organization changed meanwhile.", request_id: "r" } }],
    });
    renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    await record();
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename the organization" });
    await userEvent.clear(within(dialog).getByRole("textbox", { name: /Name/ }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Name/ }), "Other");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Late");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Changed since you opened this");
    expect(within(dialog).getByRole("button", { name: "Rename" })).toBeDisabled();
  });

  it("is not available to members, and a 403 from the API looks the same", async () => {
    const calls = mockFetch({ "GET /v1/admin/setup": [403, { error: { code: "permission_denied", message: "No.", request_id: "r" } }] });
    renderInWorkspace(<OrganizationScreen />, { me: ME_MAYA });
    expect(await screen.findByText("You need administrator access to view this page")).toBeInTheDocument();
    expect(calls).toHaveLength(0); // members are not even asked for
  });

  it("shows the API's fault with a retry", async () => {
    mockFetch({ "GET /v1/admin/setup": [500, { error: { code: "internal_error", message: "Something went wrong.", request_id: "req-9" } }] });
    renderInWorkspace(<OrganizationScreen />, { me: ME_LENA });
    expect(await screen.findByText("Couldn't load the setup record")).toBeInTheDocument();
  });
});

describe("setup-model", () => {
  it("names every state with a word and an icon; only complete and in-progress carry a signal", () => {
    for (const [state, p] of Object.entries(STATE)) {
      expect(p.word.length).toBeGreaterThan(0);
      expect(p.icon).toBeDefined();
      expect(p.tone === "neutral" || ["complete", "in_progress"].includes(state)).toBe(true);
    }
  });

  it("formats facts without inventing any", () => {
    const sources = SETUP_IN_USE.categories.find((c) => c.key === "sources")!;
    expect(factLine(sources)).toEqual(["3 sources", "3 synced"]);
    const first = SETUP_IN_USE.categories.find((c) => c.key === "first_answer")!;
    expect(factLine(first)).toEqual([]);
    const identity = SETUP_FRESH.categories.find((c) => c.key === "identity")!;
    expect(factLine(identity)).toEqual(["Connection active"]);
  });
});
