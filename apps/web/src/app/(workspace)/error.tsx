"use client";

import { PageFault } from "@/components/states/PageFault";
import styles from "@/components/states/PageFault.module.css";

/** A workspace page failed to render: the fault state inside the shell, so navigation stays. */
export default function WorkspaceError({ error }: { error: Error & { digest?: string } }) {
  return (
    <div className={styles.inShell}>
      <PageFault
        digest={error.digest}
        home={{ href: "/ask", label: "Go to Ask", place: "Ask" }}
        onRetry={() => window.location.reload()}
      />
    </div>
  );
}
