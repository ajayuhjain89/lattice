"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { SkeletonLines } from "@/components/primitives/Skeleton";
import { PageHeader, type Crumb } from "@/components/shell/PageHeader";
import { useWorkspace } from "@/components/shell/workspace-context";
import { NotAvailable } from "@/components/states/NotAvailable";

import styles from "./Access.module.css";

/**
 * The Access pages' frame: breadcrumb, the administrator-only state, and the two views
 * (members, audit log). The API enforces the capability on every call; a person without
 * it sees the same page whether or not what they asked for exists.
 */
export function AccessFrame({ trail, forbidden, children }: { trail: readonly Crumb[]; forbidden: boolean; children: ReactNode }) {
  const { isAdmin } = useWorkspace();
  return (
    <>
      <PageHeader trail={[{ label: "Admin" }, { label: "Access", href: "/access" }, ...trail]} />
      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.column}>
          {!isAdmin || forbidden ? (
            <NotAvailable
              label="Administrators only"
              title="You need administrator access to view this page"
              body="Access management is available to your organization's administrators. Being an administrator never gives access to content."
              action={{ href: "/ask", label: "Back to Ask" }}
            />
          ) : (
            children
          )}
        </div>
      </main>
    </>
  );
}

export function AccessTabs() {
  const pathname = usePathname();
  const tabs = [
    { href: "/access", label: "Members", current: !pathname.startsWith("/access/audit") },
    { href: "/access/audit", label: "Audit log", current: pathname.startsWith("/access/audit") },
  ];
  return (
    <nav aria-label="Access" className={styles.tabs}>
      {tabs.map((t) => (
        <Link key={t.href} href={t.href} className={styles.tab} aria-current={t.current ? "page" : undefined}>
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

export function Loading({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div aria-busy="true">
      <span className="visually-hidden" role="status">
        {label}
      </span>
      <SkeletonLines widths={Array.from({ length: lines }, (_, i) => (i === lines - 1 ? "60%" : "100%"))} />
    </div>
  );
}
