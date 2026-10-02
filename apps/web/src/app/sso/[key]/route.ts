import type { NextRequest } from "next/server";

/**
 * `/sso/<login key>`: the friendly sign-in link for an organization's identity provider.
 * It only hands over to the API, which owns the OIDC flow (state, nonce, PKCE, the
 * browser binding). The key is opaque; a malformed one fails like an unknown one would.
 * Relative redirects keep the browser on this origin.
 */
const KEY = /^[A-Za-z0-9_-]{22,64}$/;

function safeNext(value: string | null): string | null {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") && value.length <= 512
    ? value
    : null;
}

export async function GET(request: NextRequest, { params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  if (!KEY.test(key)) return redirect("/sign-in?error=sso_failed");
  const next = safeNext(request.nextUrl.searchParams.get("next"));
  const query = next ? `?${new URLSearchParams({ next }).toString()}` : "";
  return redirect(`/api/v1/auth/oidc/${key}/start${query}`);
}

function redirect(location: string): Response {
  return new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "no-store" } });
}
