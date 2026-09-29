import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/* is proxied to FastAPI, so the
// session cookie stays first-party and no CORS is configured (architecture §2).
const API_URL = process.env.LATTICE_API_URL ?? "http://127.0.0.1:8000";
const production = process.env.NODE_ENV === "production";

const csp = [
  "default-src 'self'",
  // Next.js inlines its bootstrap scripts; development also needs eval for HMR.
  `script-src 'self' 'unsafe-inline'${production ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

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
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default config;
