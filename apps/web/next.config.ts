import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/* is proxied to FastAPI, so the
// session cookie stays first-party and no CORS is configured (architecture §2).
const API_URL = process.env.LATTICE_API_URL ?? "http://127.0.0.1:8000";
const production = process.env.NODE_ENV === "production";

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
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
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
