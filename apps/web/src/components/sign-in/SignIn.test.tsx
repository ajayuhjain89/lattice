import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DevOrganization } from "@/lib/dev-personas.server";
import { ME_MAYA } from "@/test/fixtures";

import { DevSignIn } from "./DevSignIn";
import { ProductionSignIn } from "./ProductionSignIn";
import { continuation, SSO_ERRORS, ssoError } from "./sign-in-model";

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/sign-in", useSearchParams: () => new URLSearchParams() }));

/** fetch answered per "METHOD /path"; returns the requests made, with their JSON bodies. */
function mockFetch(routes: Record<string, [number, unknown]>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      const path = new URL(request.url).pathname.replace(/^\/api/, "");
      calls.push({ url: path, method: request.method, body: request.method === "POST" ? await request.clone().json() : null });
      const [status, body] = routes[`${request.method} ${path}`] ?? [404, { error: { code: "not_found", message: "Not found.", request_id: "r" } }];
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const ORGS: DevOrganization[] = [
  {
    slug: "halden",
    name: "Halden",
    synthetic: true,
    personas: [
      { email: "maya.okafor@halden.example", displayName: "Maya Okafor", roleLabel: "Account Manager" },
      { email: "lena.brandt@halden.example", displayName: "Lena Brandt", roleLabel: "IT Administrator" },
    ],
  },
  {
    slug: "veridian",
    name: "Veridian Analytics",
    synthetic: true,
    personas: [{ email: "noor.haddad@veridian.example", displayName: "Noor Haddad", roleLabel: null }],
  },
];

describe("sign-in model", () => {
  it("accepts only the API's three errors", () => {
    expect(Object.keys(SSO_ERRORS)).toEqual(["sso_failed", "sso_unavailable", "rate_limited"]);
    for (const code of Object.keys(SSO_ERRORS)) expect(ssoError(code)).toBe(code);
    for (const code of [null, "", "inactive", "expired", "user_not_found", "toString", "__proto__", "<script>"]) expect(ssoError(code)).toBeNull();
  });

  it("names where the person was going from the path alone", () => {
    expect(continuation("/access/members/40000000-0000-4000-8000-000000000001")).toEqual({
      path: "/access/members/40000000-0000-4000-8000-000000000001",
      label: "Access",
    });
    expect(continuation("/access/audit")?.label).toBe("Audit log");
    expect(continuation("/traces/abc")?.label).toBe("Activity");
    expect(continuation("/evidence/abc")?.label).toBe("Ask");
    expect(continuation("//evil.example")).toBeNull();
    expect(continuation("/\t/evil.example")).toBeNull();
    expect(continuation("/sign-in")).toBeNull();
    expect(continuation(null)).toBeNull();
  });
});

describe("production sign-in", () => {
  it("points to the identity provider and offers nothing to choose or type", () => {
    render(<ProductionSignIn error={null} next={null} />);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in to Lattice" })).toBeInTheDocument();
    expect(screen.getByText(/through your organization's identity provider/)).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("continuation")).toBeNull();
    expect(screen.getByRole("link", { name: "What is Lattice?" })).toHaveAttribute("href", "/");
  });

  it.each(["sso_failed", "sso_unavailable"] as const)("shows %s as its fixed alert", (code) => {
    render(<ProductionSignIn error={code} next={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent(SSO_ERRORS[code]);
  });

  it("shows a rate limit as a wait, not a fault", () => {
    render(<ProductionSignIn error="rate_limited" next={null} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(SSO_ERRORS.rate_limited);
  });

  it("says where the person was going, by name and path", () => {
    render(<ProductionSignIn error={null} next={{ path: "/sources", label: "Sources" }} />);
    expect(screen.getByText("Continue to Sources", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByTestId("continuation")).toHaveTextContent("Where you were: /sources");
    expect(screen.getByRole("link", { name: "Continue to Sources" })).toHaveAttribute("href", "/sources");
  });
});

describe("development sign-in", () => {
  beforeEach(() => {
    router.replace.mockReset();
  });

  it("groups seeded personas by organization, with the seed's role label only", () => {
    renderWithClient(<DevSignIn error={null} next={null} destination="/ask" organizations={ORGS} />);
    expect(screen.getByText("Development sign-in.")).toBeInTheDocument();
    const halden = screen.getByRole("region", { name: "Halden" });
    expect(within(halden).getAllByRole("button")).toHaveLength(2);
    expect(within(halden).getByRole("button", { name: /Maya Okafor/ })).toHaveTextContent("Account Manager");
    expect(within(halden).getByText("Synthetic")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Veridian Analytics" })).toHaveTextContent("1 persona");
    // Nothing about access is shown: no clearance, accounts or departments.
    expect(document.body).not.toHaveTextContent(/clearance|Confidential|Northpeak/i);
  });

  it("sends the persona's exact seeded subject and organization, then continues", async () => {
    const calls = mockFetch({ "POST /v1/auth/dev-login": [200, ME_MAYA] });
    renderWithClient(<DevSignIn error={null} next={{ path: "/sources", label: "Sources" }} destination="/sources" organizations={ORGS} />);
    await userEvent.click(screen.getByRole("button", { name: /Lena Brandt/ }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/sources"));
    expect(calls).toEqual([{ method: "POST", url: "/v1/auth/dev-login", body: { subject: "lena.brandt@halden.example", organization: "halden" } }]);
  });

  it("shows the API's generic failure once and stays on the page", async () => {
    mockFetch({ "POST /v1/auth/dev-login": [401, { error: { code: "authentication_failed", message: "Sign-in failed.", request_id: "r" } }] });
    renderWithClient(<DevSignIn error={null} next={null} destination="/ask" organizations={ORGS} />);
    const maya = screen.getByRole("button", { name: /Maya Okafor/ });
    await userEvent.click(maya);
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in failed.");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(router.replace).not.toHaveBeenCalled();
    expect(maya).toHaveAttribute("aria-describedby", screen.getByRole("alert").id);
  });

  it("says plainly when the server has no development sign-in", async () => {
    mockFetch({});
    renderWithClient(<DevSignIn error={null} next={null} destination="/ask" organizations={ORGS} />);
    await userEvent.click(screen.getByRole("button", { name: /Noor Haddad/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Development sign-in isn't available on this server.");
  });

  it("opens the e-mail form when no manifest is configured", () => {
    renderWithClient(<DevSignIn error={null} next={null} destination="/ask" organizations={[]} />);
    expect(screen.getByRole("textbox", { name: "E-mail" })).toBeVisible();
    expect(screen.getByRole("form", { name: "Sign in with a seeded e-mail" })).toHaveAttribute("method", "post");
  });
});
