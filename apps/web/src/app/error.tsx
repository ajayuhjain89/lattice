"use client";

import { PublicMasthead } from "@/components/public/PublicMasthead";
import styles from "@/components/public/Public.module.css";
import { PageFault } from "@/components/states/PageFault";

/**
 * A page outside the workspace failed (the landing, sign-in), or the workspace's own frame
 * did: the public masthead, which assumes no session, over the fault state.
 */
export default function RootError({ error }: { error: Error & { digest?: string } }) {
  return (
    <>
      <PublicMasthead />
      <main id="main" tabIndex={-1} className={styles.notFound}>
        <PageFault
          digest={error.digest}
          home={{ href: "/", label: "Go to the start page", place: "the start page" }}
          onRetry={() => window.location.reload()}
        />
      </main>
    </>
  );
}
