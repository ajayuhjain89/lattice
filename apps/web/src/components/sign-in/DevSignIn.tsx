"use client";

import { ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { useSignIn } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { BoundaryBand } from "@/components/primitives/Boundary";
import { Button } from "@/components/primitives/Button";
import { Field, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Tag } from "@/components/primitives/Tag";
import { initials } from "@/components/shell/nav";
import type { DevOrganization, DevPersona } from "@/lib/dev-personas.server";

import { ContinuationLine, SignInFrame, SignInHeadline, SignInMessage } from "./SignInFrame";
import { SSO_ERRORS, type Continuation, type SsoError } from "./sign-in-model";
import styles from "./SignIn.module.css";

/**
 * Development sign-in: pick a persona from the seed manifests on this server. It is a
 * boundary, and drawn as one -- a hatched band says the path can't run in production
 * (the API refuses it outside development and test). Personas are grouped by
 * organization; each row sends that persona's exact seeded e-mail and organization. Only
 * the role label the seed gives is shown: nothing about access is derived here.
 */
export function DevSignIn({
  error,
  next,
  destination,
  organizations,
}: {
  error: SsoError | null;
  next: Continuation | null;
  /** Where to go after signing in: a safe path, already checked on the server. */
  destination: string;
  organizations: DevOrganization[];
}) {
  const router = useRouter();
  const signIn = useSignIn();
  const [pending, setPending] = useState<{ subject: string; name: string } | null>(null);
  const errorId = useId();

  const submit = (subject: string, name: string, organization?: string) => {
    if (signIn.isPending) return;
    setPending({ subject, name });
    signIn.mutate(
      { subject, organization },
      { onSuccess: () => router.replace(destination), onError: () => setPending(null) },
    );
  };

  const failure = signIn.error;
  const failureText = !failure
    ? null
    : isApiError(failure) && failure.status === 404
      ? "Development sign-in isn't available on this server."
      : isApiError(failure)
        ? failure.message
        : "Sign-in failed.";
  const failureTone = failure && isApiError(failure) && failure.isRateLimited ? "time" : "fault";

  return (
    <SignInFrame wide>
      <BoundaryBand className={styles.devBand}>
        <strong>Development sign-in.</strong> Signs in as a seeded persona, without an identity provider. The API refuses it outside
        development and test.
      </BoundaryBand>

      <div className={styles.intro}>
        <SignInHeadline>Sign in to Lattice</SignInHeadline>
        <p className={styles.deck}>
          Choose a seeded persona. Each one sees only what its seeded access allows, so the same question can give different answers.
        </p>
      </div>

      {next && <ContinuationLine next={next} />}

      {failureText ? (
        <SignInMessage tone={failureTone} id={errorId}>
          {failureText}
        </SignInMessage>
      ) : (
        error && (
          <SignInMessage tone={error === "rate_limited" ? "time" : "fault"} id={errorId}>
            {SSO_ERRORS[error]}
          </SignInMessage>
        )
      )}

      <p className="visually-hidden" role="status">
        {pending ? `Signing in as ${pending.name}…` : ""}
      </p>

      {organizations.length > 0 && (
        <div className={styles.orgs}>
          {organizations.map((org) => (
            <section key={org.slug} className={styles.org} aria-labelledby={`org-${org.slug}`}>
              <div className={styles.orgHead}>
                <h2 id={`org-${org.slug}`} className={styles.orgName}>
                  {org.name}
                </h2>
                <span className={styles.orgMeta}>
                  {org.synthetic && <Tag tone="quiet">Synthetic</Tag>}
                  <span className={styles.count}>
                    {org.personas.length} {org.personas.length === 1 ? "persona" : "personas"}
                  </span>
                </span>
              </div>
              <ul className={styles.personas}>
                {org.personas.map((p) => (
                  <li key={p.email}>
                    <PersonaButton
                      persona={p}
                      pending={pending?.subject === p.email}
                      busy={signIn.isPending}
                      describedBy={failureText || error ? errorId : undefined}
                      onChoose={() => submit(p.email, p.displayName, org.slug)}
                    />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <SeededEmail open={organizations.length === 0} busy={signIn.isPending} onSubmit={(subject, organization) => submit(subject, subject, organization)} />
    </SignInFrame>
  );
}

function PersonaButton({
  persona,
  pending,
  busy,
  describedBy,
  onChoose,
}: {
  persona: DevPersona;
  pending: boolean;
  busy: boolean;
  describedBy?: string;
  onChoose: () => void;
}) {
  return (
    <button
      type="button"
      className={styles.persona}
      onClick={onChoose}
      // Busy, not disabled: focus stays on the row while the request runs.
      aria-disabled={busy || undefined}
      aria-busy={pending || undefined}
      aria-describedby={describedBy}
      data-pending={pending || undefined}
    >
      <span className={styles.initials} aria-hidden="true">
        {initials(persona.displayName)}
      </span>
      <span className={styles.who}>
        <span className={styles.personaName}>{persona.displayName}</span>
        {persona.roleLabel && <span className={styles.personaRole}>{persona.roleLabel}</span>}
      </span>
      <span className={styles.personaEmail} title={persona.email}>
        {pending ? "Signing in…" : persona.email}
      </span>
      <span className={styles.go} aria-hidden="true">
        <Icon as={ArrowRight} />
      </span>
    </button>
  );
}

/** Any seeded member by e-mail (the dev-login contract: subject, optional organization). */
function SeededEmail({ open, busy, onSubmit }: { open: boolean; busy: boolean; onSubmit: (subject: string, organization?: string) => void }) {
  const onForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const subject = String(data.get("subject") ?? "").trim();
    const organization = String(data.get("organization") ?? "").trim();
    if (subject) onSubmit(subject, organization || undefined);
  };
  return (
    <details className={styles.other} open={open || undefined}>
      <summary className={styles.otherSummary}>Use another seeded e-mail</summary>
      {/* POST, so an e-mail typed before the page is interactive never lands in a URL. */}
      <form className={styles.form} method="post" onSubmit={onForm} aria-label="Sign in with a seeded e-mail">
        <div className={styles.formRow}>
          <Field label="E-mail" required>
            {(control) => <TextInput {...control} name="subject" type="email" autoComplete="off" spellCheck={false} placeholder="name@halden.example" />}
          </Field>
          <Field label="Organization" hint="optional">
            {(control) => <TextInput {...control} name="organization" autoComplete="off" spellCheck={false} placeholder="halden" />}
          </Field>
        </div>
        <div>
          <Button type="submit" variant="secondary" loading={busy} iconEnd={<Icon as={ArrowRight} />}>
            Sign in
          </Button>
        </div>
      </form>
    </details>
  );
}
