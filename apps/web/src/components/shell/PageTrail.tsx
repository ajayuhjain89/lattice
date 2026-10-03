import Link from "next/link";

import styles from "./PageTrail.module.css";

export interface Crumb {
  label: string;
  href?: string;
}

/**
 * The inline back trail of a deep page (design-direction §6.3 "page anatomy"): where this
 * page sits under a destination, above the page's own title. Top-level pages have none --
 * the masthead already marks where you are.
 */
export function PageTrail({ trail }: { trail: readonly Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className={styles.nav}>
      <ol className={styles.trail}>
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          return (
            <li key={`${c.label}-${i}`} className={last ? styles.current : styles.crumb} aria-current={last ? "page" : undefined}>
              {c.href && !last ? <Link href={c.href}>{c.label}</Link> : c.label}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
