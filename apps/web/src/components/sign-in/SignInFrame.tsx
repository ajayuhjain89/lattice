import { AlertCircle, Clock } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { PublicMasthead } from "@/components/public/PublicMasthead";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";

import type { Continuation } from "./sign-in-model";
import styles from "./SignIn.module.css";

/**
 * What both sign-in pages share: the public masthead (the mark goes home, "What is
 * Lattice?" goes to the landing page), the record headline and the page's measure. Their
 * compositions differ because their jobs do: production explains where sign-in happens;
 * development picks a seeded persona.
 */
export function SignInFrame({ wide = false, children }: { wide?: boolean; children: ReactNode }) {
  return (
    <div className={styles.page}>
      <PublicMasthead
        action={
          <Link href="/" className={styles.mastheadLink} prefetch={false}>
            What is Lattice?
          </Link>
        }
      />
      <main id="main" tabIndex={-1} className={styles.main}>
        <div className={wide ? styles.columnWide : styles.column}>{children}</div>
      </main>
    </div>
  );
}

export function SignInHeadline({ children }: { children: ReactNode }) {
  return (
    <h1 id="sign-in-title" className={styles.title}>
      {children}
    </h1>
  );
}

/**
 * One sign-in message, from a closed set of fixed sentences: a fault is an alert with the
 * fault edge; a limit is a wait (time signal, a status), as everywhere in the product.
 */
export function SignInMessage({ tone, children, id }: { tone: "fault" | "time"; children: ReactNode; id?: string }) {
  return (
    <Callout tone={tone} icon={<Icon as={tone === "fault" ? AlertCircle : Clock} />} role={tone === "fault" ? "alert" : "status"} id={id}>
      {children}
    </Callout>
  );
}

/** Where the person was going: the destination's name and the path, the viewer's own. */
export function ContinuationLine({ next, lead = "After signing in, you'll continue to" }: { next: Continuation; lead?: string }) {
  return (
    <p className={styles.continuation} data-testid="continuation">
      <span>
        {lead} <strong>{next.label}</strong>
      </span>
      <code className={styles.path}>{next.path}</code>
    </p>
  );
}
