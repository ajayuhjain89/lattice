import { SkeletonLines } from "@/components/primitives/Skeleton";
import { Wordmark } from "@/components/brand/Wordmark";

import styles from "./ShellSkeleton.module.css";

/** The shell's geometry while /v1/me loads, so nothing shifts when it arrives. */
export function ShellSkeleton() {
  return (
    <div className={styles.shell} aria-busy="true">
      <div className={styles.nav}>
        <div className={styles.navHeader}>
          <Wordmark />
        </div>
        <div className={styles.navBody}>
          <SkeletonLines widths={["70%", "50%"]} />
        </div>
      </div>
      <div>
        <div className={styles.pageHeader}>
          <SkeletonLines widths={["96px"]} />
        </div>
        <span className="visually-hidden" role="status">
          Loading your workspace
        </span>
      </div>
    </div>
  );
}
