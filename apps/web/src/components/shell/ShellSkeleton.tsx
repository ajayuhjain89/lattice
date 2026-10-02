import { Wordmark } from "@/components/brand/Wordmark";

import styles from "./ShellSkeleton.module.css";

/**
 * The shell's geometry while /v1/me loads: the masthead rule (and on phones the tab bar's
 * band), with no destinations -- they depend on capabilities not yet known -- so nothing
 * shifts when the real shell arrives. No spinner (§6.10).
 */
export function ShellSkeleton() {
  return (
    <div className={styles.shell} aria-busy="true">
      <div className={styles.masthead}>
        <Wordmark />
      </div>
      <span className="visually-hidden" role="status">
        Loading your workspace
      </span>
      <div className={styles.bottom} />
    </div>
  );
}
