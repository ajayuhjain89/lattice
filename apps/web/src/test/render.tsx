import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import type { Me } from "@/api/types";
import { TooltipProvider } from "@/components/primitives/Tooltip";
import { WorkspaceProvider } from "@/components/shell/workspace-context";
import { createWorkspaceState, type WorkspaceState } from "@/lib/workspace-state";

import { ME_MAYA } from "./fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/ask",
  useSearchParams: () => new URLSearchParams(),
}));

export function renderInWorkspace(ui: ReactElement, { me = ME_MAYA, state = createWorkspaceState() }: { me?: Me; state?: WorkspaceState } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <WorkspaceProvider value={{ me, state, isAdmin: me.capabilities.includes("admin") }}>{ui}</WorkspaceProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { ...result, state, client };
}

/** Replace fetch with a router over path → [status, body]. Returns the calls made. */
export function mockFetch(routes: Record<string, [number, unknown]>) {
  const calls: { url: string; method: string; headers: Headers; body: string }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = new URL(request.url).pathname.replace(/^\/api/, "");
    const sent = request.method === "GET" ? "" : await request.clone().text();
    calls.push({ url: path, method: request.method, headers: request.headers, body: sent });
    const [status, body] = routes[`${request.method} ${path}`] ?? [404, { error: { code: "not_found", message: "Not found.", request_id: "r" } }];
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
