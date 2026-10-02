import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SignIn, SSO_ERRORS } from "./SignIn";

const params = { value: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => params.value,
}));

function renderSignIn() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <SignIn devEnabled={false} organizations={[]} />
    </QueryClientProvider>,
  );
}

describe("SignIn (identity provider)", () => {
  beforeEach(() => {
    params.value = new URLSearchParams();
  });

  it("explains SSO sign-in without listing any organization or connection", () => {
    renderSignIn();
    expect(screen.getByText(/Sign in with your organization's identity provider/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it.each(Object.entries(SSO_ERRORS))("shows the fixed message for %s", (code, message) => {
    params.value = new URLSearchParams({ error: code });
    renderSignIn();
    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });

  it("ignores an error it doesn't know, never echoing it", () => {
    params.value = new URLSearchParams({ error: "<script>alert(1)</script>" });
    renderSignIn();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.body).not.toHaveTextContent("alert(1)");
  });
});
