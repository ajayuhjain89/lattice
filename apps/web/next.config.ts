import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/* reaches FastAPI there, so the
// session cookie stays first-party and no CORS is configured (architecture §2).
//
// Who routes /api/* (docs/deployment.md §4, ADR-016):
// - deployment: the ingress sends /api/* straight to the API; this server never sees it
//   and has no /api route, so it is not on the API's trust or timeout path;
// - `next dev` only: the rewrite below proxies /api/* to LATTICE_API_URL, with a proxy
//   timeout above the API's Ask deadline (LATTICE_GENERATION_DEADLINE_SECONDS, 120 s) so a
//   long Ask is not cut at Next's 30-second default.
// Rewrites are read when the server builds its routes, so `next build` (NODE_ENV
// production) produces no proxy at all.
const API_URL = process.env.LATTICE_API_URL ?? "http://127.0.0.1:8000";
const production = process.env.NODE_ENV === "production";
const devApiProxy = process.env.NODE_ENV === "development";
const DEV_PROXY_TIMEOUT_MS = 150_000;

// The Content-Security-Policy (with a per-request script nonce) is set in src/proxy.ts.
// These apply to every response, including static assets.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  // Browsers honour HSTS over HTTPS only; production is served over HTTPS.
  ...(production ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }] : []),
];

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The production image runs the self-contained server (`node server.js`); local
  // `next start` (the Playwright servers) keeps the regular build.
  ...(process.env.LATTICE_WEB_STANDALONE === "1" ? { output: "standalone" as const } : {}),
  experimental: { proxyTimeout: DEV_PROXY_TIMEOUT_MS },
  async rewrites() {
    return devApiProxy ? [{ source: "/api/:path*", destination: `${API_URL}/:path*` }] : [];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
      {
        // Self-hosted fonts carry a content hash in their names (scripts/build-fonts.py).
        source: "/fonts/:file*.woff2",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default config;
