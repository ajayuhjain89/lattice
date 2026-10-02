import { NextResponse, type NextRequest } from "next/server";

/**
 * Content-Security-Policy with a fresh nonce per page request (Phase 10 headers review).
 *
 * Scripts run only with this request's nonce ('strict-dynamic' lets them load the
 * chunks they need); no 'unsafe-inline' for scripts, so injected markup cannot run.
 * Next.js reads the nonce from the request's CSP header and puts it on its own scripts,
 * which is why every page renders dynamically (the root layout opts in). Styles keep
 * 'unsafe-inline': components set inline style attributes, which a nonce cannot cover.
 * `/api/*` is FastAPI's (its own `default-src 'none'` policy) and static assets need none.
 */
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const development = process.env.NODE_ENV === "development";
  const policy = [
    "default-src 'self'",
    // Development only: React rebuilds error stacks with eval.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|fonts/|favicon.ico|icon.svg).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
