import Link from "next/link";
import type { ReactNode } from "react";

import styles from "./PageHeader.module.css";

export interface Crumb {
  label: string;
  href?: string;
}

/**
 * The 56 px page header. Its bottom rule sits on the same line as the sidebar header's
 * and the inspector header's (design-system §B6 shared axes).
 */
export function PageHeader({ trail, actions }: { trail: readonly Crumb[]; actions?: ReactNode }) {
  return (
    <header className={`${styles.header} ${trail.length === 1 && !actions ? styles.desktopOnly : ""}`}>
      <nav aria-label="Breadcrumb">
        <ol className={styles.trail}>
          {trail.map((c, i) => {
            const last = i === trail.length - 1;
            return (
              <li key={`${c.label}-${i}`} className={last ? styles.current : styles.crumb} aria-current={last ? "page" : undefined}>
                {c.href && !last ? <Link href={c.href}>{c.label}</Link> : c.label}
                {!last && (
                  <span className={styles.sep} aria-hidden="true">
                    {" "}
                    /
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
