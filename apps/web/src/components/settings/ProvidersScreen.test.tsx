import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ME_LENA, ME_MAYA, PROVIDERS } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { ProvidersScreen } from "./ProvidersScreen";

afterEach(() => vi.unstubAllGlobals());

describe("ProvidersScreen", () => {
  it("is read-only: no inputs, switches or change buttons", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    const { container } = renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await screen.findByRole("table");
    expect(container.querySelectorAll("input, select, textarea, [role='switch']")).toHaveLength(0);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText("Read-only")).toBeInTheDocument();
  });

  it("shows masked credential state only", async () => {
    mockFetch({ "GET /v1/admin/providers": [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table");
    expect(table).toHaveTextContent("Active · doesn't resolve");
    expect(table).toHaveTextContent("env reference");
    expect(table.textContent).not.toMatch(/LATTICE_PROVIDER_SECRET|AIza|sk-/);
  });

  it("members get the permission page", async () => {
    mockFetch({ "GET /v1/admin/providers": [403, { error: { code: "permission_denied", message: "x", request_id: "r" } }] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: /You need administrator access/ })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
