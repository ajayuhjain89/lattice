import type { ReactNode } from "react";

import { Page } from "@/components/layout/Layout";

import styles from "./Record.module.css";

/** The page container of a record surface: Ask, a trace, an evidence page. */
export function RecordPage({ children, className, "aria-labelledby": labelledBy }: { children: ReactNode; className?: string; "aria-labelledby"?: string }) {
  return (
    <Page width="full" className={[styles.page, className].filter(Boolean).join(" ")} aria-labelledby={labelledBy}>
      {children}
    </Page>
  );
}

export { styles as record };
