import styles from "./RuleSkeleton.module.css";

/**
 * Loading drawn as the page's own rules (design-direction §6.10, the Wait family): no grey
 * bars, nothing that pretends to be content.
 *
 *   prose   hairlines at reading rhythm, the last one shorter (a passage, a trace)
 *   ledger  rows of a register or ledger, the first under the ink rule of its header
 *
 * One status names what is loading; the rules themselves are hidden from assistive tech.
 */
export function RuleSkeleton({ label, variant = "prose", rows = 3 }: { label: string; variant?: "prose" | "ledger"; rows?: number }) {
  return (
    <div className={`${styles.skeleton} ${styles[variant]}`} aria-busy="true">
      <span className="visually-hidden" role="status">
        {label}
      </span>
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className={styles.rule} aria-hidden="true" />
      ))}
    </div>
  );
}
