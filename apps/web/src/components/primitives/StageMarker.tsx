import { X } from "lucide-react";

import styles from "./StageMarker.module.css";

export type StageState = "done" | "skipped" | "failed";

/** The numbered-marker language of traces (design-system §B12 "Stage marker"). */
export function StageMarker({ n, state }: { n: number; state: StageState }) {
  return (
    <span className={[styles.marker, state !== "done" && styles[state]].filter(Boolean).join(" ")} aria-hidden="true">
      {state === "failed" ? <X size={12} strokeWidth={2} /> : n}
    </span>
  );
}
