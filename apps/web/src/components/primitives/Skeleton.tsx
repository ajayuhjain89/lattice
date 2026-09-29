import styles from "./Skeleton.module.css";

/**
 * Static placeholder geometry (no shimmer: motion is reserved for real progress). It
 * occupies the same space as the content it stands for, so nothing jumps on arrival.
 */
export function SkeletonLines({ widths }: { widths: readonly string[] }) {
  return (
    <span className={styles.stack} aria-hidden="true">
      {widths.map((w, i) => (
        <span key={i} className={`${styles.block} ${styles.line}`} style={{ width: w }} />
      ))}
    </span>
  );
}

export function SkeletonBlock({ height, width = "100%" }: { height: number; width?: string }) {
  return <span className={styles.block} style={{ height, width }} aria-hidden="true" />;
}
