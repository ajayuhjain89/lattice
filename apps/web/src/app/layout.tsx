import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { preload } from "react-dom";

import { FONT_PRELOADS } from "@/lib/fonts.gen";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: { default: "Lattice", template: "%s · Lattice" },
  description: "Permission-aware answers from your organization's sources, with every claim cited.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  // --surface-paper (tokens.css); metadata can't read a custom property.
  themeColor: "#f4f3ef",
  width: "device-width",
  initialScale: 1,
  // Draw edge to edge so env(safe-area-inset-*) is real: the shell's bars and the page
  // gutters keep content clear of notches and the home indicator themselves.
  viewportFit: "cover",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Render per request: Next.js puts the CSP nonce from src/proxy.ts on its scripts,
  // which a page rendered at build time could not carry.
  await connection();
  // The three voices are self-hosted (scripts/build-fonts.py); their Latin files are
  // preloaded, the rest (weight 500 ledger, Latin Extended) load when a page needs them.
  for (const href of FONT_PRELOADS) preload(href, { as: "font", type: "font/woff2", crossOrigin: "anonymous" });
  return (
    <html lang="en">
      {/* Client providers (queries, toasts, tooltips) wrap only the pages that use them --
          the workspace, development sign-in and the specimen -- so the static public pages
          ship none of that script. */}
      <body>{children}</body>
    </html>
  );
}
