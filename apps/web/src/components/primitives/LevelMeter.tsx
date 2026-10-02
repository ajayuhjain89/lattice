import styles from "./LevelMeter.module.css";

export const LEVELS = ["internal", "confidential", "restricted"] as const;
export type Level = (typeof LEVELS)[number];

const LABEL: Record<Level, string> = { internal: "Internal", confidential: "Confidential", restricted: "Restricted" };

/**
 * A sensitivity level as a three-step meter (Internal · Confidential · Restricted). The
 * level's name is always written beside it; the squares only show where it sits on the
 * scale. Monochrome, like every sensitivity mark.
 */
export function LevelMeter({ level, showLabel = true }: { level: Level; showLabel?: boolean }) {
  const at = LEVELS.indexOf(level);
  return (
    <span className={styles.meter}>
      <span className={styles.steps} aria-hidden="true">
        {LEVELS.map((l, i) => (
          <span key={l} className={i <= at ? styles.on : styles.off} />
        ))}
      </span>
      <span className={showLabel ? styles.label : "visually-hidden"}>{LABEL[level]}</span>
    </span>
  );
}
