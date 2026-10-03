"use client";

import { useEffect, useState } from "react";

import { useSources } from "@/api/queries";
import { record } from "@/components/record/RecordPage";
import { formatCount } from "@/lib/format";

import styles from "./AskScreen.module.css";

/**
 * The API answers in one response (no streaming; D-6 is still open), so progress is
 * honest: what Lattice is doing and for how long -- no stages, no percentages. The
 * counter ticks once a second (not an animation). Rules and margin markers hold the
 * brief's geometry, so the answer arrives into the same shape.
 */
export function AskProgress({ startedAt }: { startedAt: number }) {
  const sources = useSources();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const count = sources.data?.sources.length;
  const where = count ? `the ${formatCount(count, "source", "sources")} you can read` : "the sources you can read";
  return (
    <>
    <section className={`${record.text} ${styles.progress}`} aria-labelledby="progress-label" aria-busy="true" data-testid="ask-progress">
      <div className={styles.progressLine}>
        <span className={styles.progressKeyword} id="progress-label">
          Asking
        </span>
        <span className={styles.elapsed} aria-hidden="true">
          {seconds} s
        </span>
      </div>
      <p className={styles.progressNote} role="status">
        Searching {where}, then drafting and checking every quote against its source.
      </p>
      {seconds >= 20 && <p className={styles.progressNote}>Still working.</p>}
    </section>
      <div className={styles.skeleton} aria-hidden="true">
        {["lead", "a", "b"].map((k) => (
          <div key={k} className={`${record.row} ${styles.skeletonRow}`} data-row={k}>
            <span className={styles.skeletonMarker} />
            <span className={styles.skeletonRules}>
              <span />
              <span />
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
