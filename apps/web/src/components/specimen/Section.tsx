import type { ReactNode } from "react";

import styles from "./Specimen.module.css";

/** One specimen section: number, title and note on the left, the specimen on the right. */
export function Section({ n, title, note, children }: { n: string; title: string; note: string; children: ReactNode }) {
  return (
    <section className={styles.section} aria-labelledby={`specimen-${n}`} data-testid={`specimen-${n}`}>
      <div className={styles.sectionHead}>
        <span className={styles.sectionNumber}>{n}</span>
        <h2 id={`specimen-${n}`} className={styles.sectionTitle}>
          {title}
        </h2>
        <p className={styles.sectionNote}>{note}</p>
      </div>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}
