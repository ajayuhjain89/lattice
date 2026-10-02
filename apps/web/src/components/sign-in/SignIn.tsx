"use client";

import { AlertCircle, ArrowRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useId, useState, type FormEvent } from "react";

import { useSignIn } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { Tag } from "@/components/primitives/Tag";
import { Wordmark } from "@/components/brand/Wordmark";
import type { DevOrganization } from "@/lib/dev-personas.server";

import styles from "./SignIn.module.css";

/** Only same-origin paths are followed after sign-in. */
export function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : "/ask";
}

/** The sign-in errors the API's OIDC routes redirect with (a closed set; others ignored). */
export const SSO_ERRORS: Record<string, string> = {
  sso_failed:
    "Sign-in didn't complete. Try again from your identity provider. If it keeps failing, ask your administrator whether your account has access to Lattice.",
  sso_unavailable: "Your identity provider couldn't be reached. Try again in a moment.",
  rate_limited: "Too many sign-in attempts from this browser. Wait a minute and try again.",
};

const PRINCIPLES = [
  ["Permission-aware", "Every search runs inside what you are allowed to read."],
  ["Cited claim by claim", "Each statement links to a verbatim, checked passage."],
  ["Disagreement shown", "Conflicting sources stay side by side. Nothing is crowned."],
] as const;

export function SignIn(props: { devEnabled: boolean; organizations: DevOrganization[] }) {
  return (
    <Suspense>
      <SignInInner {...props} />
    </Suspense>
  );
}

function SignInInner({ devEnabled, organizations }: { devEnabled: boolean; organizations: DevOrganization[] }) {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const ssoError = SSO_ERRORS[params.get("error") ?? ""] ?? null;
  const signIn = useSignIn();
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);
  const emailId = useId();
  const orgId = useId();
  const errorId = useId();

  const submit = (subject: string, organization?: string) => {
    setPendingEmail(subject);
    signIn.mutate(
      { subject, organization },
      { onSuccess: () => router.replace(next), onSettled: () => setPendingEmail(null) },
    );
  };

  const onForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const subject = String(data.get("subject") ?? "").trim();
    const organization = String(data.get("organization") ?? "").trim();
    if (subject) submit(subject, organization || undefined);
  };

  const error = signIn.error;
  const errorText = !error
    ? ssoError
    : isApiError(error) && error.status === 404
      ? "Development sign-in isn't available on this server."
      : isApiError(error)
        ? error.message
        : "Sign-in failed.";

  return (
    <div className={styles.page}>
      <section className={styles.editorial} aria-labelledby="sign-in-headline">
        <Wordmark />
        <div>
          <h1 id="sign-in-headline" className={styles.headline}>
            What your organization knows, <em>with the evidence.</em>
          </h1>
          <p className={styles.deck}>
            Lattice answers questions from your docs, tickets and conversations — only from sources you may read, and
            never without showing where each statement comes from.
          </p>
        </div>
        <ol className={styles.principles}>
          {PRINCIPLES.map(([title, body], i) => (
            <li key={title} className={styles.principle}>
              <span className={styles.principleIndex}>{String(i + 1).padStart(2, "0")}</span>
              <span>
                <span className={styles.principleTitle}>{title}</span>{" "}
                <span className={styles.principleBody}>{body}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      <main id="main" className={styles.panel}>
        <div className={styles.panelInner}>
          <header className={styles.panelHeader}>
            {devEnabled && (
              <div>
                <Tag tone="muted">Development sign-in</Tag>
              </div>
            )}
            <h2 className={styles.title}>Sign in</h2>
            <p className={styles.lede}>
              {devEnabled
                ? "Choose a seeded persona. What you can see depends on who you are — each persona has different access."
                : "Sign in with your organization's identity provider: open Lattice from its app dashboard, or use the sign-in link your administrator shared."}
            </p>
          </header>

          {errorText && (
            <div className={styles.error} role="alert" id={errorId}>
              <span className={styles.errorIcon}>
                <Icon as={AlertCircle} />
              </span>
              <span>{errorText}</span>
            </div>
          )}

          {devEnabled &&
            organizations.map((org) => (
              <section key={org.slug} className={styles.org} aria-labelledby={`org-${org.slug}`}>
                <SectionLabel as="h3" id={`org-${org.slug}`} meta={`${org.personas.length} personas`}>
                  {org.name}
                </SectionLabel>
                <ul className={styles.personaList}>
                  {org.personas.map((p) => (
                    <li key={p.email}>
                      <button
                        type="button"
                        className={styles.persona}
                        onClick={() => submit(p.email, org.slug)}
                        disabled={signIn.isPending}
                        aria-busy={pendingEmail === p.email || undefined}
                        aria-describedby={errorText ? errorId : undefined}
                      >
                        <span>
                          <span className={styles.personaName}>{p.displayName}</span>
                          {p.roleLabel && <span className={styles.personaRole}>{p.roleLabel}</span>}
                        </span>
                        <span className={styles.personaEmail}>
                          {pendingEmail === p.email ? "Signing in…" : p.email}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}

          {devEnabled && (
            <form className={styles.form} onSubmit={onForm} aria-label="Sign in with a seeded email">
              <SectionLabel as="h3">{organizations.length ? "Or use an email" : "Seeded email"}</SectionLabel>
              <div className={styles.formRow}>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor={emailId}>
                    Email
                  </label>
                  <input
                    id={emailId}
                    className={styles.input}
                    name="subject"
                    type="email"
                    autoComplete="off"
                    spellCheck={false}
                    required
                    placeholder="name@company.example"
                  />
                </div>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor={orgId}>
                    Organization
                  </label>
                  <input
                    id={orgId}
                    className={styles.input}
                    name="organization"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="optional"
                  />
                </div>
              </div>
              <div className={styles.actions}>
                <Button type="submit" variant="primary" iconEnd={<ArrowRight size={16} strokeWidth={1.5} />} loading={signIn.isPending && pendingEmail !== null && !organizations.some((o) => o.personas.some((p) => p.email === pendingEmail))}>
                  Sign in
                </Button>
              </div>
              <p className={styles.devNote}>
                Development only. The API refuses this sign-in outside development and test.
              </p>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
