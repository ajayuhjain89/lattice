import styles from "./AskPage.module.css";

const PRINCIPLES = [
  ["Only what you can read", "Every search runs inside your access. Answers never draw on anything else."],
  ["Cited claim by claim", "Each statement carries a verbatim quote, checked against its source."],
  ["Disagreement kept", "When sources conflict, both sides are shown. Lattice doesn't pick a winner."],
] as const;

/** First visit: what Ask does and why to trust it. No generated example questions. */
export function AskIntro({ organization }: { organization: string }) {
  return (
    <section className={styles.intro} aria-labelledby="ask-intro">
      <h1 id="ask-intro" className={styles.display}>
        Ask what {organization} knows.
      </h1>
      <p className={styles.deck}>
        Answers are drafted from the docs, tickets and conversations you&apos;re allowed to read — each statement
        dated, cited and verified, or not shown at all.
      </p>
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
