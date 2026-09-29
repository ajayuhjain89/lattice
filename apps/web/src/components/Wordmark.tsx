import styles from "./Wordmark.module.css";

/** The product mark: a four-cell lattice, one cell lit, and the name. */
export function Wordmark({ showName = true }: { showName?: boolean }) {
  return (
    <span className={styles.wordmark}>
      <span className={styles.mark} aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </span>
      {showName ? "Lattice" : <span className="visually-hidden">Lattice</span>}
    </span>
  );
}
