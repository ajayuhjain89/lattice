import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ACCOUNTS_MAYA, ME_LENA, SOURCES_MEMBER } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { AskScreen } from "./AskScreen";

afterEach(() => vi.unstubAllGlobals());

describe("Ask scope (GET /v1/sources, GET /v1/accounts)", () => {
  it("states the member's own scope on first visit", async () => {
    mockFetch({ "GET /v1/sources": [200, SOURCES_MEMBER], "GET /v1/accounts": [200, ACCOUNTS_MAYA] });
    renderInWorkspace(<AskScreen />);
    expect(await screen.findByText("You can search 2 sources · Acme Corp, Northpeak Logistics")).toBeInTheDocument();
  });

  it("offers only the member's accounts and readable source types, and scopes the question", async () => {
    const calls = mockFetch({ "GET /v1/sources": [200, SOURCES_MEMBER], "GET /v1/accounts": [200, ACCOUNTS_MAYA] });
    renderInWorkspace(<AskScreen />);
    await screen.findByText(/You can search 2 sources/);

    await userEvent.click(screen.getByRole("button", { name: "Set account" }));
    const accounts = await screen.findByRole("group", { name: "Search only" });
    expect(within(accounts).getAllByRole("radio").map((r) => r.closest("label")?.textContent)).toEqual(["Acme Corp", "Northpeak Logistics"]);
    await userEvent.click(within(accounts).getByRole("radio", { name: "Acme Corp" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Account: Acme Corp. Change" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Set sources" }));
    const types = await screen.findByRole("group", { name: "Search only" });
    // Drive and Zendesk only: no chat source is readable, so Chat isn't offered.
    expect(within(types).getAllByRole("checkbox").map((c) => c.closest("label")?.textContent)).toEqual(["Documents", "Tickets"]);
    await userEvent.keyboard("{Escape}");

    await userEvent.type(screen.getByRole("textbox", { name: "Question" }), "What did we promise?{Enter}");
    await vi.waitFor(() => expect(calls.some((c) => c.url === "/v1/ask")).toBe(true));
  });

  it("disables the composer when nothing the member can read is indexed", async () => {
    mockFetch({ "GET /v1/sources": [200, { sources: [] }], "GET /v1/accounts": [200, ACCOUNTS_MAYA] });
    renderInWorkspace(<AskScreen />, { me: ME_LENA });
    expect(await screen.findByText("Lattice hasn't indexed any sources you can access yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/sources");
    expect(screen.getByRole("textbox", { name: "Question" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("keeps working when the scope can't be loaded", async () => {
    mockFetch({});
    renderInWorkspace(<AskScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Set account" }));
    expect(await screen.findByText(/Couldn't load your accounts/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Question" })).toBeEnabled();
  });
});
