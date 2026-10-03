import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ACCOUNTS_MAYA, ACTIVITY_OWN, ME_LENA, SOURCES_MEMBER } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { AskScreen } from "./AskScreen";

afterEach(() => vi.unstubAllGlobals());

const ROUTES: Record<string, [number, unknown]> = { "GET /v1/sources": [200, SOURCES_MEMBER], "GET /v1/accounts": [200, ACCOUNTS_MAYA], "GET /v1/activity": [200, ACTIVITY_OWN] };

describe("Ask desk (GET /v1/sources, /v1/accounts, /v1/activity)", () => {
  it("shows the member's reach -- their sources and accounts, as the API lists them -- and their recent questions", async () => {
    mockFetch({ ...ROUTES });
    renderInWorkspace(<AskScreen />);
    const reach = await screen.findByTestId("your-reach");
    expect(await within(reach).findByText("Halden Drive")).toBeInTheDocument();
    expect(within(reach).getByText(/41 documents you can read · synced 28 Sep 2026/)).toBeInTheDocument();
    expect(await within(reach).findByText("Acme Corp, Northpeak Logistics")).toBeInTheDocument();
    const recent = await screen.findByTestId("recent-questions");
    expect(within(recent).getByRole("link", { name: "When will SCIM ship for Acme, and what did we tell them?" })).toHaveAttribute(
      "href",
      `/traces/${ACTIVITY_OWN.items[0]!.trace_id}`,
    );
  });

  it("puts a recent question back in the box, unsent and focused", async () => {
    const calls = mockFetch({ ...ROUTES });
    renderInWorkspace(<AskScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Ask again: What did Kestrel agree to in the renewal?" }));
    const box = screen.getByRole("textbox", { name: "Question" });
    expect(box).toHaveValue("What did Kestrel agree to in the renewal?");
    await vi.waitFor(() => expect(box).toHaveFocus());
    expect(calls.some((c) => c.url === "/v1/ask")).toBe(false);
  });

  it("reads the scope as a sentence; offers only the member's accounts and readable source types", async () => {
    const calls = mockFetch({ ...ROUTES });
    renderInWorkspace(<AskScreen />);
    await screen.findByText("Halden Drive");
    const scope = screen.getByRole("group", { name: "Search scope" });
    expect(within(scope).getAllByRole("button").map((b) => b.textContent)).toEqual(["all my sources", "any account", "any date", "default sensitivity"]);
    expect(scope).toHaveTextContent(/^Search.*as/);

    await userEvent.click(within(scope).getByRole("button", { name: "Account: any account. Change" }));
    const accounts = await screen.findByRole("group", { name: "Search only" });
    expect(within(accounts).getAllByRole("radio").map((r) => r.closest("div")?.textContent)).toEqual(["Acme Corp", "Northpeak Logistics"]);
    await userEvent.click(within(accounts).getByRole("radio", { name: "Acme Corp" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Account: Acme Corp. Change" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove account filter" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Sources: all my sources. Change" }));
    const types = await screen.findByRole("group", { name: "Search only" });
    // Drive and Zendesk only: no chat source is readable, so Chat isn't offered.
    expect(within(types).getAllByRole("checkbox").map((c) => c.closest("div")?.textContent)).toEqual(["Documents", "Tickets"]);
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
    await userEvent.click(screen.getByRole("button", { name: "Account: any account. Change" }));
    expect(await screen.findByText(/Couldn't load your accounts/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Question" })).toBeEnabled();
    expect(await screen.findByText(/Your sources couldn't be listed just now/)).toBeInTheDocument();
  });

  it("Shift+Enter adds a line; Enter asks", async () => {
    const calls = mockFetch({ ...ROUTES });
    renderInWorkspace(<AskScreen />);
    const box = screen.getByRole("textbox", { name: "Question" });
    await userEvent.type(box, "First line{Shift>}{Enter}{/Shift}second line");
    expect(box).toHaveValue("First line\nsecond line");
    expect(calls.some((c) => c.url === "/v1/ask")).toBe(false);
    await userEvent.type(box, "{Enter}");
    await vi.waitFor(() => expect(calls.some((c) => c.url === "/v1/ask")).toBe(true));
  });
});
