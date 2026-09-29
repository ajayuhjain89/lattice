import { Settings2, TextSearch, type LucideIcon } from "lucide-react";

export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Shown only when /v1/me reports the capability; the API enforces it regardless. */
  admin?: boolean;
  match: (pathname: string) => boolean;
}

export const NAV: readonly NavEntry[] = [
  {
    href: "/ask",
    label: "Ask",
    icon: TextSearch,
    match: (p) => p === "/ask" || p.startsWith("/evidence/"),
  },
  {
    href: "/settings/providers",
    label: "AI providers",
    icon: Settings2,
    admin: true,
    match: (p) => p.startsWith("/settings/providers"),
  },
];
