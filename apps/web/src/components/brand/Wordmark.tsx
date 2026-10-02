import { Mark } from "./Mark";
import styles from "./Wordmark.module.css";

/** The mark and the product name. With `showName={false}` the name is still announced. */
export function Wordmark({ showName = true, size = 18 }: { showName?: boolean; size?: 16 | 18 | 20 | 24 }) {
  return (
    <span className={styles.wordmark}>
      <Mark size={size} />
      {showName ? "Lattice" : <span className="visually-hidden">Lattice</span>}
    </span>
  );
}
