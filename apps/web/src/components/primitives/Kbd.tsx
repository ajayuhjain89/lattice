import type { ReactNode } from "react";

import styles from "./Kbd.module.css";

/** A key the person can press. Decorative hints are aria-hidden by their container. */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={styles.kbd}>{children}</kbd>;
}
