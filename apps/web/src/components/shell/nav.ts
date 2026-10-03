/**
 * The navigation model (design-direction §6.3). One list feeds every presentation -- the
 * masthead, the phone tab bar and its More sheet, and Jump -- so a destination is added
 * once and appears everywhere it should.
 *
 * Visibility follows the capabilities /v1/me reports and nothing else. That is a
 * presentation rule, not a permission: every endpoint behind these pages still enforces
 * its own authorization, and a page reached directly renders the same "not available"
 * state whether or not anything is behind it.
 */
import { Database, History, Settings2, ShieldCheck, TextSearch, type LucideIcon } from "lucide-react";

import type { Me } from "@/api/types";

export type Capability = Me["capabilities"][number];

export interface NavSection {
  id: string;
  /** Names the group for assistive tech and labels it where it is shown as a heading. */
  label: string;
  /** The word on a folded section's menu button, where `label` would be too long. */
  short?: string;
  /** The whole section is shown only with this capability. */
  requires?: Capability;
}

export interface Destination {
  href: string;
  label: string;
  icon: LucideIcon;
  section: NavSection["id"];
  requires?: Capability;
  /** On phones: a tab in the bottom bar, or a row in the More sheet. */
  phone: "tab" | "more";
  /** The pages this destination owns (deep pages included), for aria-current. */
  match: (pathname: string) => boolean;
}

/** A page under a destination that Jump can open directly (it has no tab of its own). */
export interface Subpage {
  href: string;
  label: string;
  parent: Destination["href"];
  /** Shown only with this capability (beyond what its parent requires). */
  requires?: Capability;
}

export const SECTIONS: readonly NavSection[] = [
  { id: "workspace", label: "Workspace" },
  { id: "admin", label: "Administration", short: "Admin", requires: "admin" },
];

export const DESTINATIONS: readonly Destination[] = [
  {
    href: "/ask",
    label: "Ask",
    icon: TextSearch,
    section: "workspace",
    phone: "tab",
    match: (p) => p === "/ask" || p.startsWith("/evidence/"),
  },
  {
    href: "/activity",
    label: "Activity",
    icon: History,
    section: "workspace",
    phone: "tab",
    match: (p) => p.startsWith("/activity") || p.startsWith("/traces/"),
  },
  {
    href: "/sources",
    label: "Sources",
    icon: Database,
    section: "workspace",
    phone: "tab",
    match: (p) => p.startsWith("/sources"),
  },
  {
    href: "/access",
    label: "Access",
    icon: ShieldCheck,
    section: "admin",
    phone: "more",
    match: (p) => p.startsWith("/access"),
  },
  {
    href: "/settings/providers",
    label: "AI providers",
    icon: Settings2,
    section: "admin",
    phone: "more",
    match: (p) => p.startsWith("/settings/providers"),
  },
];

export const SUBPAGES: readonly Subpage[] = [
  { href: "/activity/organization", label: "Organization runs", parent: "/activity", requires: "admin" },
  { href: "/access/audit", label: "Audit log", parent: "/access" },
];

/** At most this many tabs in the phone bar (plus More); the rest move to the sheet. */
export const PHONE_TABS = 4;

function allowed(me: Me, requires: Capability | undefined): boolean {
  return requires === undefined || me.capabilities.includes(requires);
}

export interface VisibleSection {
  section: NavSection;
  destinations: Destination[];
}

/** The sections and destinations this person sees, in order; empty sections are dropped. */
export function visibleNav(me: Me): VisibleSection[] {
  return SECTIONS.filter((s) => allowed(me, s.requires))
    .map((section) => ({
      section,
      destinations: DESTINATIONS.filter((d) => d.section === section.id && allowed(me, d.requires)),
    }))
    .filter((s) => s.destinations.length > 0);
}

export function visibleSubpages(me: Me): Subpage[] {
  const parents = new Set(visibleNav(me).flatMap((s) => s.destinations.map((d) => d.href)));
  return SUBPAGES.filter((p) => parents.has(p.parent) && allowed(me, p.requires));
}

/** The destination that owns this path, if any (the first match wins). */
export function currentDestination(destinations: readonly Destination[], pathname: string): Destination | undefined {
  return destinations.find((d) => d.match(pathname));
}

/** Phone split: the first tab-priority destinations become tabs, everything else is More. */
export function phoneSplit(sections: readonly VisibleSection[]): { tabs: Destination[]; more: VisibleSection[] } {
  const all = sections.flatMap((s) => s.destinations);
  const tabs = all.filter((d) => d.phone === "tab").slice(0, PHONE_TABS);
  const more = sections
    .map((s) => ({ section: s.section, destinations: s.destinations.filter((d) => !tabs.includes(d)) }))
    .filter((s) => s.destinations.length > 0);
  return { tabs, more };
}

/** Initials for the account button: first letters of the first and last words of the name. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const first = [...words[0]!][0] ?? "";
  const last = words.length > 1 ? ([...words.at(-1)!][0] ?? "") : "";
  return (first + last).toUpperCase();
}
