import Link from "next/link";

import styles from "./AskPage.module.css";

const PRINCIPLES = [
  ["Only what you can read", "Every search runs inside your access. Answers never draw on anything else."],
  ["Cited claim by claim", "Each statement carries a verbatim quote, checked against its source."],
  ["Disagreement kept", "When sources conflict, both sides are shown. Lattice doesn't pick a winner."],
] as const;

/**
 * First visit: what Ask does and why to trust it, and the person's own scope as the API
 * reports it (design-system B13). No generated example questions. With nothing indexed
 * that they can read, the empty-knowledge-base state replaces the scope line.
 */
export function AskIntro({
  organization,
  scope,
  empty,
  isAdmin,
}: {
  organization: string;
  /** "You can search 3 sources · Acme Corp, …"; null while unknown. */
  scope: string | null;
  empty: boolean;
  isAdmin: boolean;
}) {
  return (
    <section className={styles.intro} aria-labelledby="ask-intro">
      <h1 id="ask-intro" className={styles.display}>
        Ask what {organization} knows.
      </h1>
      <p className={styles.deck}>
        Answers are drafted from the docs, tickets and conversations you&apos;re allowed to read — each statement
        dated, cited and verified, or not shown at all.
      </p>
      {empty ? (
        <div className={styles.emptyScope} role="status">
          <p className={styles.emptyTitle}>Lattice hasn&apos;t indexed any sources you can access yet.</p>
          <p>
            {isAdmin ? (
              <>
                Check which sources have synced on the <Link href="/sources">Sources</Link> page.
              </>
            ) : (
              "Once an administrator connects a source holding content you can read, you can ask about it here."
            )}
          </p>
        </div>
      ) : (
        <p className={styles.scopeLine} data-testid="scope-line" aria-busy={scope === null || undefined}>
          {scope ?? "\u00a0"}
        </p>
      )}
      <ul className={styles.principles}>
        {PRINCIPLES.map(([label, body]) => (
          <li key={label} className={styles.principle}>
            <span className={styles.principleLabel}>{label}</span>
            <span className={styles.principleBody}>{body}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
