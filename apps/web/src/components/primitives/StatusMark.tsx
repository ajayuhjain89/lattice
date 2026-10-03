import { CheckCircle2, CircleDashed, Clock, GitFork, XCircle, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Icon } from "./icons";
import styles from "./StatusMark.module.css";

/**
 * A state word with its icon: always both, never colour alone. Tones map to the signals
 * (design-direction §4.2); `neutral` is a state with no signal (never synced, skipped).
 */
export type StatusTone = "healthy" | "time" | "fault" | "disagreement" | "neutral";

const ICON: Record<StatusTone, LucideIcon> = {
  healthy: CheckCircle2,
  time: Clock,
  fault: XCircle,
  disagreement: GitFork,
  neutral: CircleDashed,
};

export function StatusMark({
  tone,
  children,
  icon,
  keyword = false,
}: {
  tone: StatusTone;
  children: ReactNode;
  /** Override the tone's icon (e.g. a warning triangle for a partial sync); null for none
   *  (an answered brief carries no status icon -- only non-answered states do). */
  icon?: LucideIcon | null;
  /** The tracked uppercase ledger form, used only by the brief's status line. */
  keyword?: boolean;
}) {
  return (
    <span className={[styles.mark, styles[tone], keyword && styles.keyword].filter(Boolean).join(" ")}>
      {icon !== null && <Icon as={icon ?? ICON[tone]} size={keyword ? 12 : 14} />}
      {children}
    </span>
  );
}
