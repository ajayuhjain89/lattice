import Link from "next/link";
import type { ReactNode } from "react";

import { Wordmark } from "@/components/brand/Wordmark";

import styles from "./Public.module.css";

export interface PublicLink {
  href: string;
  label: string;
}

/**
 * The masthead of the public pages (landing, sign-in, not found): the product's own
 * 52 px masthead rule and vocabulary, without a session. The mark goes home to the landing
 * page; `links` are the landing's in-page sections; `action` is the one thing to do here
 * (Sign in, or "What is Lattice?").
 *
 * Under 768 px the links leave the bar for a row of their own beneath it, which wraps
 * rather than hides -- three anchors don't need a menu, and the row works without script.
 */
export function PublicMasthead({ links, action, label = "On this page" }: { links?: readonly PublicLink[]; action?: ReactNode; label?: string }) {
  return (
    <>
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <header className={styles.masthead}>
        <div className={styles.bar}>
          <Link href="/" className={styles.brand} aria-label="Lattice, home" prefetch={false}>
            <Wordmark />
          </Link>
          {links && links.length > 0 && (
            <nav className={styles.links} aria-label={label}>
              <ul>
                {links.map((l) => (
                  <li key={l.href}>
                    <a href={l.href} className={styles.link}>
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          )}
          {action && <div className={styles.action}>{action}</div>}
        </div>
      </header>
    </>
  );
}
