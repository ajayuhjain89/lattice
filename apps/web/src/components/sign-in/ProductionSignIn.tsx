import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";

import { SignInFrame, SignInHeadline, SignInMessage } from "./SignInFrame";
import { SSO_ERRORS, type Continuation, type SsoError } from "./sign-in-model";
import styles from "./SignIn.module.css";

/**
 * Production sign-in: the organization's identity provider (OIDC) and nothing to choose.
 * Sign-in starts at the identity provider -- its app dashboard, or the `/sso/<key>` link an
 * administrator shares -- so this page has no organization lookup, e-mail field or
 * provider button: none of them could exist without saying which organizations or
 * connections exist. Server-rendered; no script is needed to read or follow it.
 *
 * States: the first visit; a `next` destination (the route's last stop names it, with its
 * path, and a link back to it for someone who signed in from the identity provider in
 * another tab); and the API's three errors, each its fixed sentence.
 * Redirects to and from the identity provider happen in the API and render no page here.
 */
export function ProductionSignIn({ error, next }: { error: SsoError | null; next: Continuation | null }) {
  return (
    <SignInFrame>
      <div className={styles.intro}>
        <SignInHeadline>Sign in to Lattice</SignInHeadline>
        <p className={styles.deck}>
          Lattice signs you in through your organization&apos;s identity provider. There is no separate Lattice password.
        </p>
      </div>

      {error && <SignInMessage tone={error === "rate_limited" ? "time" : "fault"}>{SSO_ERRORS[error]}</SignInMessage>}

      <ol className={styles.route} aria-label="How signing in works">
        <li className={styles.step}>
          <span className={styles.stepMark}>
            <ExhibitMarker n={1} size="lg" />
          </span>
          <span className={styles.stepName}>Start from your identity provider</span>
          <span className={styles.stepBody}>Open Lattice from its app dashboard, or use the sign-in link your administrator shared.</span>
        </li>
        <li className={styles.step}>
          <span className={styles.stepMark}>
            <ExhibitMarker n={2} size="lg" />
          </span>
          <span className={styles.stepName}>Lattice opens with your access</span>
          <span className={styles.stepBody}>You see what your organization lets you read — nothing more.</span>
        </li>
        <li className={styles.step}>
          <span className={styles.stepMark}>
            <ExhibitMarker n={3} size="lg" />
          </span>
          <span className={styles.stepName}>{next ? `Continue to ${next.label}` : "Ask a question"}</span>
          {next ? (
            <span className={styles.stepBody} data-testid="continuation">
              Where you were: <code className={styles.path}>{next.path}</code>
            </span>
          ) : (
            <span className={styles.stepBody}>Answers come with the evidence for every statement.</span>
          )}
        </li>
      </ol>

      {next && (
        <p className={styles.help}>
          Signed in from your identity provider in another tab?{" "}
          <a href={next.path} className={styles.continueLink}>
            Continue to {next.label}
          </a>
        </p>
      )}

      <p className={styles.help}>Can&apos;t sign in? Your administrator manages who has access to Lattice.</p>
    </SignInFrame>
  );
}
