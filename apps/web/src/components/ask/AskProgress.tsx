"use client";

import { useEffect, useState } from "react";

import { SkeletonLines } from "@/components/primitives/Skeleton";

import styles from "./AskPage.module.css";

/**
 * The API answers in one response (no streaming, D-6 not built), so progress is honest:
 * what Lattice is doing, and for how long -- no invented stages. The skeleton holds the
 * brief's geometry so the answer replaces it without a jump.
 */
export function AskProgress({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <section className={styles.progress} aria-labelledby="progress-label" aria-busy="true">
      <div className={styles.progressLine}>
        <span className={styles.progressStatus} role="status" id="progress-label">
          <span className={styles.pulse} aria-hidden="true" />
          Searching, drafting, verifying citations
        </span>
        <span className={styles.elapsed} aria-hidden="true">
          {seconds} s
        </span>
      </div>
      <p className={styles.progressNote}>
        Lattice searches only sources you can read, drafts from them, and checks every quote before showing anything.
      </p>
      <SkeletonLines widths={["96%", "90%", "72%"]} />
      <div className={styles.skeletonRows}>
        <SkeletonLines widths={["40%", "86%"]} />
        <SkeletonLines widths={["34%", "80%"]} />
      </div>
    </section>
  );
}
