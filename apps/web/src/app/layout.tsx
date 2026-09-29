import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@/styles/globals.css";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "Lattice", template: "%s · Lattice" },
  description: "Permission-aware answers from your organization's sources, with every claim cited.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#fffcf4",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
