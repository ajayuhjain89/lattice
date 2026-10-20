"use client";

import { Copy, ExternalLink, RotateCw } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/api/errors";
import {
  useChangeConnection,
  useIssueScimToken,
  usePreviewConnectionStatus,
  usePreviewIdentityChange,
  usePreviewRevokeScimToken,
  useRevokeScimToken,
  useStartTestSignIn,
  type ConnectionChange,
} from "@/api/queries";
import type { IdentitySettings, OidcConnection, RevealedScimToken, ScimToken } from "@/api/types";
import { NowAfter, ReasonField } from "@/components/access/AccountDialogs";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { ChoiceGroup, Field, Radio, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { useToast } from "@/components/primitives/Toast";
import { formatCount, formatDayTime } from "@/lib/format";

import { CLAIM_HELP, COMMON_CLAIMS, identityError, issuerAccessLabel, lowerFirst, tokenLabel, tokenName } from "./identity-copy";
import styles from "./IdentityScreen.module.css";

/*
 * Identity administration (Phase 12, P12.4). Every change is the API's: it previews,
 * checks the version the page showed (409 when someone changed it meanwhile), asks for a
 * reason and records the change. Secrets are typed into masked fields that never start
 * filled and are sent once; a SCIM token is shown once, from the response that created
 * it, and dropped from memory when its dialog closes.
 */

export type IdentityDialog =
  | { kind: "add" }
  | { kind: "client_id" }
  | { kind: "secret" }
  | { kind: "remove_secret" }
  | { kind: "status"; enabled: boolean }
  | { kind: "identity"; issuer?: string; claim?: string }
  | { kind: "token_create" }
  | { kind: "token_rotate"; token: ScimToken }
  | { kind: "token_revoke"; token: ScimToken };

export interface Applied {
  title: string;
  detail: string;
  at: string;
}

const TITLES: Record<IdentityDialog["kind"], string> = {
  add: "Connect an identity provider",
  client_id: "Change the client ID",
  secret: "Set the client secret",
  remove_secret: "Remove the client secret",
  status: "Disable sign-in",
  identity: "Change the issuer or identifying claim",
  token_create: "Create a provisioning token",
  token_rotate: "Rotate a provisioning token",
  token_revoke: "Revoke a provisioning token",
};

function title(dialog: IdentityDialog, oidc: OidcConnection | null): string {
  if (dialog.kind === "status") return dialog.enabled ? "Enable sign-in" : "Disable sign-in";
  if (dialog.kind === "secret" && oidc && oidc.client_secret.state !== "none") return "Replace the client secret";
  return TITLES[dialog.kind];
}

export function IdentityDialogs({
  dialog,
  settings,
  onClose,
  onApplied,
}: {
  dialog: IdentityDialog | null;
  settings: IdentitySettings;
  onClose: () => void;
  onApplied: (applied: Applied) => void;
}) {
  const oidc = settings.oidc ?? null;
  // A revealed token must leave memory with its dialog: its form is unmounted on close.
  return (
    <Modal open={dialog !== null} onOpenChange={(open) => !open && onClose()} title={dialog ? title(dialog, oidc) : ""}>
      {dialog && <DialogBody dialog={dialog} settings={settings} onClose={onClose} onApplied={onApplied} />}
    </Modal>
  );
}

function DialogBody({
  dialog,
  settings,
  onClose,
  onApplied,
}: {
  dialog: IdentityDialog;
  settings: IdentitySettings;
  onClose: () => void;
  onApplied: (applied: Applied) => void;
}) {
  const oidc = settings.oidc ?? null;
  const applied = (title: string, detail: string) => {
    onApplied({ title, detail, at: new Date().toISOString() });
    onClose();
  };
  switch (dialog.kind) {
    case "add":
      return <AddConnectionForm onClose={onClose} onApplied={applied} />;
    case "client_id":
      return oidc && <ClientIdForm oidc={oidc} onClose={onClose} onApplied={applied} />;
    case "secret":
      return oidc && <SecretForm oidc={oidc} onClose={onClose} onApplied={applied} />;
    case "remove_secret":
      return oidc && <RemoveSecretForm oidc={oidc} onClose={onClose} onApplied={applied} />;
    case "status":
      return oidc && <StatusForm oidc={oidc} enabled={dialog.enabled} onClose={onClose} onApplied={applied} />;
    case "identity":
      return oidc && <IdentityChangeForm oidc={oidc} initial={dialog} onClose={onClose} onApplied={applied} />;
    case "token_create":
    case "token_rotate":
      return <TokenForm rotate={dialog.kind === "token_rotate" ? dialog.token : null} onClose={onClose} />;
    case "token_revoke":
      return <RevokeTokenForm token={dialog.token} onClose={onClose} onApplied={applied} />;
  }
}

/* --- shared ------------------------------------------------------------------------------- */

function useReason() {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const error = tried && !reason.trim() ? "Give a reason for this change." : undefined;
  return { reason: reason.trim(), value: reason, setReason, tried, setTried, error, ref };
}

function Refused({ error, onClose }: { error: unknown; onClose: () => void }) {
  const conflict = isApiError(error) && error.status === 409 && error.issues.length > 0 && !error.issues.includes("connection_exists") && !error.issues.includes("token_not_live");
  return (
    <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
      <p>{identityError(error)}</p>
      {conflict && (
        <p>
          <Button size="sm" icon={<Icon as={RotateCw} />} onClick={onClose}>
            Close and review the latest
          </Button>
        </p>
      )}
    </Callout>
  );
}

function Actions({ children }: { children: ReactNode }) {
  return <div className={styles.dialogActions}>{children}</div>;
}

/** Apply a connection change; on success the page shows the applied line. */
function useApply(onApplied: (title: string, detail: string) => void) {
  const change = useChangeConnection();
  const run = (body: ConnectionChange, title: string, detail: string) => change.mutate(body, { onSuccess: () => onApplied(title, detail) });
  return { change, run };
}

/* --- connect ------------------------------------------------------------------------------- */

function AddConnectionForm({ onClose, onApplied }: { onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const id = useId();
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [claim, setClaim] = useState("sub");
  const [type, setType] = useState<"confidential" | "public">("confidential");
  const [secret, setSecret] = useState("");
  const issuerError = reason.tried && !/^https:\/\/[^\s?#@]+$/.test(issuer.trim()) ? "Enter the issuer's https:// address, exactly as the provider names itself." : undefined;
  const clientError = reason.tried && !clientId.trim() ? "Enter the client ID your identity provider gave Lattice." : undefined;
  const claimError = reason.tried && !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(claim.trim()) ? "Enter a claim name (letters, digits and underscores)." : undefined;
  const secretError = reason.tried && type === "confidential" && !secret ? "Paste the client secret, or choose a public client." : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (issuerError || clientError || claimError || secretError || !reason.reason || !issuer.trim() || !clientId.trim()) return;
    run(
      {
        kind: "add",
        body: {
          issuer: issuer.trim(),
          client_id: clientId.trim(),
          subject_claim: claim.trim(),
          ...(type === "confidential" ? { client_secret: secret } : {}),
          reason: reason.reason,
        },
      },
      "Identity provider connected",
      "Register the redirect URI shown below with your identity provider, then run a test sign-in.",
    );
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-describedby={`${id}-trust`}>
      <p className={styles.note} id={`${id}-trust`}>
        Members sign in through this provider; Lattice never stores a password. The issuer must be a public https address, or a private one your Lattice operator has approved
        for this organization.
      </p>
      <Field label="Issuer" required error={issuerError} help="The identity provider's issuer URL, e.g. https://login.example.com/realms/acme.">
        {(control) => <TextInput {...control} value={issuer} onChange={(e) => setIssuer(e.target.value)} inputMode="url" autoComplete="off" spellCheck={false} />}
      </Field>
      <Field label="Client ID" required error={clientError}>
        {(control) => <TextInput {...control} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" spellCheck={false} />}
      </Field>
      <ClaimField value={claim} onChange={setClaim} error={claimError} />
      <ChoiceGroup legend="Client type">
        <Radio name={`${id}-type`} label="Confidential: Lattice presents a client secret" checked={type === "confidential"} onChange={() => setType("confidential")} />
        <Radio name={`${id}-type`} label="Public: PKCE only, no secret" checked={type === "public"} onChange={() => setType("public")} />
      </ChoiceGroup>
      {type === "confidential" && <SecretField value={secret} onChange={setSecret} error={secretError} label="Client secret" />}
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          Connect
        </Button>
      </Actions>
    </form>
  );
}

function ClaimField({ value, onChange, error }: { value: string; onChange: (value: string) => void; error?: string }) {
  const listId = useId();
  return (
    <Field label="Identifying claim" required error={error} help={`${CLAIM_HELP} Usually sub; e-mail only if your provider guarantees it never changes.`}>
      {(control) => (
        <>
          <TextInput {...control} value={value} onChange={(e) => onChange(e.target.value)} list={listId} autoComplete="off" spellCheck={false} />
          <datalist id={listId}>
            {COMMON_CLAIMS.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </>
      )}
    </Field>
  );
}

/** A masked field that never starts filled: the secret is typed or pasted, sent once. */
function SecretField({ value, onChange, error, label }: { value: string; onChange: (value: string) => void; error?: string; label: string }) {
  return (
    <Field label={label} required error={error} help="Encrypted as soon as it arrives and never shown again, to you or anyone else.">
      {(control) => (
        <TextInput
          {...control}
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          spellCheck={false}
          maxLength={4096}
          data-testid="client-secret-input"
        />
      )}
    </Field>
  );
}

/* --- client ID, secret ----------------------------------------------------------------------- */

function ClientIdForm({ oidc, onClose, onApplied }: { oidc: OidcConnection; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const [clientId, setClientId] = useState(oidc.client_id);
  const trimmed = clientId.trim();
  const error = reason.tried && !trimmed ? "Enter the client ID." : reason.tried && trimmed === oidc.client_id ? "The client ID is unchanged." : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!trimmed || trimmed === oidc.client_id || !reason.reason) return;
    run({ kind: "client_id", id: oidc.id, body: { client_id: trimmed, version: oidc.version, reason: reason.reason } }, "Client ID changed", "Sessions continue. Run a test sign-in to confirm the provider accepts it.");
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <Field label="Client ID" required error={error} help="Members stay signed in; the next sign-ins use the new client.">
        {(control) => <TextInput {...control} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" spellCheck={false} />}
      </Field>
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          Change client ID
        </Button>
      </Actions>
    </form>
  );
}

function SecretForm({ oidc, onClose, onApplied }: { oidc: OidcConnection; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const [secret, setSecret] = useState("");
  const replacing = oidc.client_secret.state !== "none";
  const error = reason.tried && !secret ? "Paste the new client secret." : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!secret || !reason.reason) return;
    run(
      { kind: "secret", id: oidc.id, body: { client_secret: secret, version: oidc.version, reason: reason.reason } },
      replacing ? "Client secret replaced" : "Client secret set",
      "Encrypted and stored. Sessions continue; the next sign-ins present the new secret.",
    );
    setSecret("");
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        {replacing
          ? "The new secret replaces the current one at once, and the current one is destroyed. Members stay signed in. Update it in your identity provider first, so sign-in keeps working."
          : "From now on Lattice presents this secret to your identity provider. Members stay signed in."}
      </p>
      <SecretField value={secret} onChange={setSecret} error={error} label={replacing ? "New client secret" : "Client secret"} />
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          {replacing ? "Replace secret" : "Set secret"}
        </Button>
      </Actions>
    </form>
  );
}

function RemoveSecretForm({ oidc, onClose, onApplied }: { oidc: OidcConnection; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason) return;
    run({ kind: "remove_secret", id: oidc.id, body: { version: oidc.version, reason: reason.reason } }, "Client secret removed", "Lattice signs in as a public client (PKCE only) from now on.");
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        Lattice will sign in as a public client, with PKCE and no secret. Do this only if your identity provider has the client set up as public: otherwise sign-in fails. The
        stored secret is destroyed.
      </p>
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="danger" loading={change.isPending}>
          Remove secret
        </Button>
      </Actions>
    </form>
  );
}

/* --- disable, enable --------------------------------------------------------------------------- */

function StatusForm({
  oidc,
  enabled,
  onClose,
  onApplied,
}: {
  oidc: OidcConnection;
  enabled: boolean;
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
}) {
  const preview = usePreviewConnectionStatus();
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const { mutate } = preview;
  useEffect(() => mutate({ id: oidc.id, enabled }), [mutate, oidc.id, enabled]);
  const shown = preview.data;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason || !shown) return;
    run(
      { kind: "status", id: oidc.id, body: { enabled, version: oidc.version, reason: reason.reason } },
      enabled ? "Sign-in enabled" : "Sign-in disabled",
      enabled ? "Members can sign in through the identity provider again." : `${formatCount(shown.sessions_ending, "session", "sessions")} ended. Nobody can sign in through it now.`,
    );
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      {preview.isError ? (
        <Refused error={preview.error} onClose={onClose} />
      ) : !shown ? (
        <RuleSkeleton variant="ledger" rows={2} label="Reviewing the change" />
      ) : (
        <>
          <NowAfter
            caption="Sign-in now and after this change"
            rows={[{ key: "status", label: "Sign-in", now: enabled ? "Disabled" : "Active", after: enabled ? "Active" : "Disabled" }]}
          />
          <ul className={styles.effects} data-testid="status-effects">
            {enabled ? (
              <>
                <li>The same connection and the same {formatCount(shown.members_bound, "member", "members")} sign in again.</li>
                <li>Sessions ended when it was disabled stay ended. Provisioning tokens of this connection work again.</li>
              </>
            ) : (
              <>
                <li>Nobody can sign in through the identity provider, from now on.</li>
                <li>
                  {shown.sessions_ending === 0 ? "No session is open through it." : `${formatCount(shown.sessions_ending, "session ends", "sessions end")} at once.`}
                  {shown.ends_own_session && <strong> Yours too: you&apos;ll be signed out.</strong>}
                </li>
                <li>Provisioning (SCIM) through its tokens stops until it&apos;s enabled again.</li>
              </>
            )}
          </ul>
          <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
        </>
      )}
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant={enabled ? "primary" : "danger"} loading={change.isPending} disabled={!shown}>
          {enabled ? "Enable sign-in" : "Disable sign-in"}
        </Button>
      </Actions>
    </form>
  );
}

/* --- issuer and identifying claim -------------------------------------------------------------- */

function IdentityChangeForm({
  oidc,
  initial,
  onClose,
  onApplied,
}: {
  oidc: OidcConnection;
  initial: { issuer?: string; claim?: string };
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
}) {
  const id = useId();
  const preview = usePreviewIdentityChange();
  const test = useStartTestSignIn();
  const { change, run } = useApply(onApplied);
  const reason = useReason();
  const [issuer, setIssuer] = useState(initial.issuer ?? oidc.issuer);
  const [claim, setClaim] = useState(initial.claim ?? oidc.subject_claim);
  const [confirmation, setConfirmation] = useState("");
  const [reviewed, setReviewed] = useState<string | null>(null);
  const draft = JSON.stringify([issuer.trim(), claim.trim()]);
  const shown = reviewed === draft ? preview.data : undefined;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (shown) heading.current?.focus();
  }, [shown]);
  // Opened from a matched test: review it straight away.
  const { mutate } = preview;
  const started = useRef(false);
  useEffect(() => {
    if (started.current || (initial.issuer === undefined && initial.claim === undefined)) return;
    started.current = true;
    setReviewed(JSON.stringify([(initial.issuer ?? oidc.issuer).trim(), (initial.claim ?? oidc.subject_claim).trim()]));
    mutate({ id: oidc.id, issuer: initial.issuer ?? oidc.issuer, subject_claim: initial.claim ?? oidc.subject_claim });
  }, [initial.issuer, initial.claim, mutate, oidc.id, oidc.issuer, oidc.subject_claim]);

  const review = () => {
    setReviewed(draft);
    preview.mutate({ id: oidc.id, issuer: issuer.trim(), subject_claim: claim.trim() });
  };
  const expected = shown ? (shown.issuer_changed ? shown.issuer : shown.subject_claim) : "";
  const confirmError = reason.tried && confirmation.trim() !== expected ? `Type ${expected} exactly.` : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!shown) return review();
    reason.setTried(true);
    if (!shown.proof || confirmation.trim() !== expected || !reason.reason) return;
    run(
      {
        kind: "identity",
        id: oidc.id,
        body: {
          issuer: shown.issuer,
          subject_claim: shown.subject_claim,
          proof_id: shown.proof.test_id,
          confirmation: confirmation.trim(),
          version: oidc.version,
          reason: reason.reason,
        },
      },
      "Issuer or identifying claim changed",
      `${formatCount(shown.sessions_ending, "session", "sessions")} ended; members sign in again with the new settings.`,
    );
  };
  const testWithValues = () =>
    test.mutate(
      { issuer: shown?.issuer ?? issuer.trim(), subject_claim: shown?.subject_claim ?? claim.trim() },
      { onSuccess: (started) => window.location.assign(started.authorization_url) },
    );
  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-describedby={`${id}-scope`}>
      <Callout tone="neutral" label="Only for the same identity provider" labelAs="p">
        <p id={`${id}-scope`}>
          Use this when your identity provider moved to a new address, or when another claim carries the same values. Moving to a different identity provider is not supported —
          contact your Lattice operator.
        </p>
      </Callout>
      <Field label="Issuer" required>
        {(control) => (
          <TextInput {...control} value={issuer} onChange={(e) => setIssuer(e.target.value)} inputMode="url" autoComplete="off" spellCheck={false} disabled={change.isPending} />
        )}
      </Field>
      <ClaimField value={claim} onChange={setClaim} />
      {!shown && (
        <Actions>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={preview.isPending}>
            Review change
          </Button>
        </Actions>
      )}
      {preview.isError && reviewed === draft && <Refused error={preview.error} onClose={onClose} />}
      {shown && (
        <>
          <h3 className={styles.reviewHeading} ref={heading} tabIndex={-1}>
            What will change
          </h3>
          <NowAfter
            caption="The connection's identity settings now and after this change"
            rows={[
              { key: "issuer", label: "Issuer", now: <span className={styles.ledger}>{oidc.issuer}</span>, after: <span className={styles.ledger}>{shown.issuer}</span> },
              { key: "claim", label: "Identifying claim", now: oidc.subject_claim, after: shown.subject_claim },
            ]}
          />
          <ul className={styles.effects} data-testid="identity-effects">
            <li>
              {shown.sessions_ending === 0 ? "No session is open through this connection." : `Every session of this connection ends at once: ${formatCount(shown.sessions_ending, "session", "sessions")}.`}
              {shown.ends_own_session && <strong> Yours too: you&apos;ll sign in again.</strong>}
            </li>
            <li>
              The {formatCount(shown.members_bound, "member", "members")} must send the same identifier under the new settings. Nobody is matched by e-mail or remapped.
            </li>
            {shown.issuer_changed && <li>New address: {lowerFirst(issuerAccessLabel(shown.issuer_access))}.</li>}
          </ul>
          {shown.proof ? (
            <p className={styles.proof} data-testid="identity-proof">
              Your test sign-in at {formatDayTime(shown.proof.completed_at)} matched your identity with these values. It can be used once, until {formatDayTime(shown.proof.expires_at)}.
            </p>
          ) : (
            <Callout tone="neutral" label="Needs your test sign-in with these values" labelAs="p">
              <p>
                Sign in as yourself through the identity provider with these values. If it returns the identifier your membership already signs in with, the change can be applied.
                You&apos;ll come back to this page.
              </p>
              <p>
                <Button type="button" size="sm" icon={<Icon as={ExternalLink} />} onClick={testWithValues} loading={test.isPending}>
                  Test sign-in with these values
                </Button>
              </p>
              {test.isError && <p role="alert">{identityError(test.error)}</p>}
            </Callout>
          )}
          {shown.proof && (
            <>
              <Field label={`Type ${expected} to confirm`} required error={confirmError}>
                {(control) => <TextInput {...control} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" spellCheck={false} />}
              </Field>
              <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
            </>
          )}
          {change.isError && <Refused error={change.error} onClose={onClose} />}
          <Actions>
            <Button type="button" onClick={onClose} disabled={change.isPending}>
              Cancel
            </Button>
            <Button type="button" onClick={review} disabled={change.isPending}>
              Review again
            </Button>
            <Button type="submit" variant="danger" loading={change.isPending} disabled={!shown.proof}>
              Apply change
            </Button>
          </Actions>
        </>
      )}
    </form>
  );
}

/* --- SCIM tokens --------------------------------------------------------------------------------- */

function TokenForm({ rotate, onClose }: { rotate: ScimToken | null; onClose: () => void }) {
  const issue = useIssueScimToken();
  const reason = useReason();
  const { reset } = issue;
  // The token leaves memory when the dialog closes (the form unmounts).
  useEffect(() => () => reset(), [reset]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason) return;
    issue.mutate(rotate ? { kind: "rotate", id: rotate.id, reason: reason.reason } : { kind: "create", reason: reason.reason });
  };
  if (issue.data) return <Revealed revealed={issue.data} rotated={rotate !== null} onDone={onClose} />;
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        {rotate
          ? `${tokenLabel(rotate)} is refused from the moment the new token is created. Paste the new one into your identity provider straight away. For no interruption, create a second token instead, switch the provider to it, then revoke this one.`
          : "Your identity provider uses this token to add, update and deactivate members (SCIM). It works until you revoke it. It is shown once."}
      </p>
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {issue.isError && <Refused error={issue.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={issue.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant={rotate ? "danger" : "primary"} loading={issue.isPending}>
          {rotate ? "Rotate token" : "Create token"}
        </Button>
      </Actions>
    </form>
  );
}

function Revealed({ revealed, rotated, onDone }: { revealed: RevealedScimToken; rotated: boolean; onDone: () => void }) {
  const toast = useToast();
  const id = useId();
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => field.current?.focus(), []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(revealed.revealed_token);
      toast({ title: "Token copied" });
    } catch {
      field.current?.select();
    }
  };
  return (
    <div className={styles.form} data-testid="revealed-token">
      <Callout tone="neutral" label="Shown only now" labelAs="p">
        <p>
          Copy it into your identity provider&apos;s SCIM settings. Lattice keeps only a fingerprint and its last four characters ({revealed.token.hint}): if it&apos;s lost, rotate
          it.
          {rotated && " The previous token is already refused."}
        </p>
      </Callout>
      <div className={styles.reveal}>
        <label htmlFor={`${id}-token`} className={styles.revealLabel}>
          Provisioning token
        </label>
        <input
          id={`${id}-token`}
          ref={field}
          className={styles.revealValue}
          readOnly
          value={revealed.revealed_token}
          onFocus={(e) => e.currentTarget.select()}
          autoComplete="off"
          spellCheck={false}
        />
        <Button type="button" size="sm" icon={<Icon as={Copy} />} onClick={copy}>
          Copy token
        </Button>
      </div>
      <Actions>
        <Button type="button" variant="primary" onClick={onDone}>
          I&apos;ve stored it
        </Button>
      </Actions>
    </div>
  );
}

function RevokeTokenForm({ token, onClose, onApplied }: { token: ScimToken; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const preview = usePreviewRevokeScimToken();
  const revoke = useRevokeScimToken();
  const reason = useReason();
  const { mutate } = preview;
  useEffect(() => mutate(token.id), [mutate, token.id]);
  const shown = preview.data;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason || !shown) return;
    revoke.mutate({ id: token.id, reason: reason.reason }, { onSuccess: () => onApplied(`${tokenLabel(token)} revoked`, "The identity provider's next request with it is refused.") });
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      {preview.isError ? (
        <Refused error={preview.error} onClose={onClose} />
      ) : !shown ? (
        <RuleSkeleton variant="ledger" rows={2} label="Reviewing the change" />
      ) : (
        <>
          <ul className={styles.effects} data-testid="revoke-effects">
            <li>The identity provider&apos;s next request with {tokenName(token)} is refused. A revoked token is never valid again.</li>
            {shown.last_live_token && (
              <li>
                <strong>It&apos;s the last live token:</strong> provisioning stops.{" "}
                {shown.provisioned_members > 0 &&
                  `The ${formatCount(shown.provisioned_members, "provisioned member keeps", "provisioned members keep")} their current access until a new token is in use.`}
              </li>
            )}
          </ul>
          <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
        </>
      )}
      {revoke.isError && <Refused error={revoke.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={revoke.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="danger" loading={revoke.isPending} disabled={!shown}>
          Revoke token
        </Button>
      </Actions>
    </form>
  );
}
