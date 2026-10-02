import styles from "./Mark.module.css";

/**
 * The Lattice mark (design-direction §2): three-by-three crossing lines with the centre
 * cell lit in the provenance signal -- one followed relationship inside a structure.
 * Drawn on a 16-unit grid so it stays crisp at 16, 20 and 32 px. Always decorative: the
 * product name is carried by text or an accessible label beside it.
 */
export function Mark({ size = 18 }: { size?: 16 | 18 | 20 | 24 | 32 }) {
  return (
    <svg className={styles.mark} width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {/* Inner lines are thinner than the frame so the lit cell stays legible at 16 px. */}
      <rect className={styles.lit} x="5.75" y="5.75" width="4.5" height="4.5" />
      <path className={styles.inner} d="M5.75 1.5v13M10.25 1.5v13M1.5 5.75h13M1.5 10.25h13" />
      <rect className={styles.line} x="0.75" y="0.75" width="14.5" height="14.5" rx="1" />
    </svg>
  );
}
