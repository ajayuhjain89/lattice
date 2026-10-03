import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ME_LENA, ME_MAYA, PROVIDERS } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { ProvidersScreen } from "./ProvidersScreen";

afterEach(() => vi.unstubAllGlobals());

const lattice = () => screen.findByRole("table", { name: "Which data levels each AI provider may receive" });

describe("ProvidersScreen", () => {
  it("is read-only: no inputs, switches or edit controls -- only the lattice's cells", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    const { container } = renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    expect(container.querySelectorAll("input, select, textarea, [role='switch']")).toHaveLength(0);
    // The only buttons are lattice cells (they explain a relationship); nothing edits or saves.
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(9);
    expect(buttons.every((b) => table.contains(b))).toBe(true);
    expect(screen.queryByRole("button", { name: /^(edit|save|change|enable|disable|configure)\b/i })).toBeNull();
    expect(screen.getByText("Read-only here.")).toBeInTheDocument();
    expect(screen.getByText("lattice provider")).toBeInTheDocument();
  });

  it("draws every provider × level as a permitted or a boundary cell, named in words", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      expect.arrayContaining(["Generation", "Embedding"]),
    );
    expect(within(table).getByRole("button", { name: "Confidential data may be sent to ollama · qwen3.5:4b: it runs inside the Lattice deployment, and it's approved up to Confidential." })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Restricted data isn't sent to ollama · qwen3.5:4b: it's approved only up to Confidential." })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Internal data isn't sent to gemini · gemini-3.5-flash-lite: the provider is disabled, so it isn't called." })).toBeInTheDocument();
    expect(table.querySelectorAll("[class*='filled']").length + table.querySelectorAll("[class*='hatched']").length).toBe(9);
    expect(screen.getByTestId("never-sent")).toHaveTextContent("Every level may reach at least one provider");
  });

  it("moves through the cells by keyboard and explains the one in focus", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    const first = within(table).getAllByRole("button")[0]!;
    expect(first).toHaveAttribute("tabindex", "0");
    act(() => first.focus());
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Internal data may be sent to ollama · qwen3.5:4b");
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Internal data isn't sent to gemini");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Restricted data isn't sent to gemini");
    await userEvent.keyboard("{End}");
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Restricted data may be sent to local · mxbai-embed-large-v1");
  });

  it("says which levels never leave Lattice", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, { ...PROVIDERS, providers: PROVIDERS.providers.slice(0, 2) }] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByTestId("never-sent")).toHaveTextContent("Restricted content is never sent to an AI provider: it stays in Lattice.");
  });

  it("shows masked credential state only", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Recorded AI providers (read-only)" });
    expect(table).toHaveTextContent("Active · doesn't resolve");
    expect(table).toHaveTextContent("env reference");
    expect(table.textContent).not.toMatch(/LATTICE_PROVIDER_SECRET|AIza|sk-/);
  });

  it("draws no lattice when nothing is configured", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, { ...PROVIDERS, providers: [] }] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "No AI providers are configured" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("members get the permission page and the API is never asked", async () => {
    const calls = mockFetch({});
    renderInWorkspace(<ProvidersScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: /You need administrator access/ })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(calls).toEqual([]);
  });

  it("a revoked capability gets the same page", async () => {
    mockFetch({ "GET /v1/admin/providers": [403, { error: { code: "permission_denied", message: "x", request_id: "r" } }] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: /You need administrator access/ })).toBeInTheDocument();
  });
});
