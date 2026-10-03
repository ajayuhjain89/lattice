"use client";

/**
 * The frame of the operational surfaces (design-direction §6.6–§6.9): Sources, Activity,
 * Access, the audit log and AI providers. It owns only what they share -- the admin
 * width, a title with one sentence under it, the back trail of a deep page, a view switch
 * between sibling pages, and the administrator gate. What sits under the head is each
 * page's own structure (a register, a chronology, a ledger, a lattice).
 */
import Link from "next/link";
import type { ReactNode } from "react";

import { Page } from "@/components/layout/Layout";
import { PageTrail, type Crumb } from "@/components/shell/PageTrail";
import { useWorkspace } from "@/components/shell/workspace-context";
import { NotAvailable } from "@/components/states/NotAvailable";

import styles from "./OpsPage.module.css";

export function OpsPage({ children, trail, "aria-labelledby": labelledBy }: { children: ReactNode; trail?: readonly Crumb[]; "aria-labelledby"?: string }) {
  return (
    <Page width="admin" className={styles.page} aria-labelledby={labelledBy}>
      {trail && trail.length > 0 && <PageTrail trail={trail} />}
      {children}
    </Page>
  );
}

export function OpsHead({
  title,
  deck,
  id = "page-title",
  children,
}: {
  title: ReactNode;
  deck?: ReactNode;
  id?: string;
  /** Under the deck: a summary sentence, a view switch, a boundary band. */
  children?: ReactNode;
}) {
  return (
    <header className={styles.head}>
      <h1 className={styles.title} id={id}>
        {title}
      </h1>
      {deck && <p className={styles.deck}>{deck}</p>}
      {children}
    </header>
  );
}

export interface View {
  href: string;
  label: string;
  current: boolean;
}

/** Sibling pages of one destination (Members · Audit log), as links with aria-current. */
export function ViewTabs({ label, views }: { label: string; views: readonly View[] }) {
  return (
    <nav aria-label={label} className={styles.views}>
      <ul className={styles.viewList}>
        {views.map((v) => (
          <li key={v.href}>
            <Link href={v.href} className={styles.view} aria-current={v.current ? "page" : undefined}>
              {v.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * Administrator pages: without the capability (or once the API refuses it), the same
 * not-available page whatever was asked for. The API enforces the capability on every
 * call; this only avoids asking.
 */
export function AdminGate({ forbidden = false, children, body }: { forbidden?: boolean; children: ReactNode; body?: string }) {
  const { isAdmin } = useWorkspace();
  if (!isAdmin || forbidden) {
    return (
      <NotAvailable
        label="Administrators only"
        title="You need administrator access to view this page"
        body={body ?? "This page is available to your organization's administrators. Being an administrator never gives access to content."}
        action={{ href: "/ask", label: "Back to Ask" }}
      />
    );
  }
  return <>{children}</>;
}

/** A section of an operational page: a sans heading over a hairline, with an optional count. */
export function OpsSection({
  id,
  title,
  count,
  aside,
  children,
  className,
}: {
  id: string;
  title: ReactNode;
  count?: ReactNode;
  /** Right-aligned beside the heading (a page note, a control). */
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={[styles.section, className].filter(Boolean).join(" ")} aria-labelledby={id}>
      <div className={styles.sectionHead}>
        <h2 className={styles.sectionTitle} id={id}>
          {title}
          {count !== undefined && <span className={styles.count}>{count}</span>}
        </h2>
        {aside && <div className={styles.aside}>{aside}</div>}
      </div>
      {children}
    </section>
  );
}
