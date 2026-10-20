"use client";

import { RotateCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { isApiError, retryPhrase } from "@/api/errors";
import { useAddMember, useIdentityRequirement, usePreviewAdministrator, usePreviewReinstatement, useReinstate, useSetAdministrator } from "@/api/queries";
import type { IdentityRequirement, MemberAccess } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Field, TextArea, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { useToast } from "@/components/primitives/Toast";

import styles from "./Access.module.css";
import { Comparison, MAX_REASON, Preview } from "./ChangeAccess";
import type { ComparisonRow } from "./compare";
import { changeError } from "./errors";
import type { AppliedChange } from "./MemberScreen";

/*
 * Members and administrators (Phase 12, P12.2). Every rule shown here is the backend's:
 * the identifying claim comes from the active connection, refusals are the API's fixed
 * issue codes, and the member page offers only the actions `lifecycle` says apply.
 */

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;

function issuerHost(issuer: string | null): string {
  if (!issuer) return "your identity provider";
  try {
    return new URL(issuer).host;
  } catch {
    return "your identity provider";
  }
}

/* --- add a member by hand ------------------------------------------------------------- */

export function AddMemberDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Add a member"
      description="Nothing is sent: this is not an invitation. The person signs in through your organization's identity provider."
    >
      {open && <AddMemberForm onDone={() => onOpenChange(false)} />}
    </Modal>
  );
}

function AddMemberForm({ onDone }: { onDone: () => void }) {
  const requirement = useIdentityRequirement(true);
  if (requirement.isError) {
    return (
      <div className={styles.form}>
        <Callout tone="fault" label="Couldn't check your identity provider connection" role="alert">
          <p>Close this and try again.</p>
        </Callout>
        <div className={styles.dialogActions}>
          <Button onClick={onDone}>Close</Button>
        </div>
      </div>
    );
  }
  if (!requirement.data) {
    return (
      <p className={styles.secondary} role="status">
        Checking your identity provider connection…
      </p>
    );
  }
  if (!requirement.data.available) {
    return (
      <div className={styles.form}>
        <Callout tone="neutral" label="Connect an identity provider first" labelAs="h3">
          <p>People sign in to Lattice only through your organization&apos;s identity provider, and none is connected. Once it is, you can add members here.</p>
        </Callout>
        <div className={styles.dialogActions}>
          <Button onClick={onDone}>Close</Button>
        </div>
      </div>
    );
  }
  return <NewMemberFields requirement={requirement.data} onDone={onDone} />;
}

function NewMemberFields({ requirement, onDone }: { requirement: IdentityRequirement; onDone: () => void }) {
  const id = useId();
  const router = useRouter();
  const toast = useToast();
  const add = useAddMember();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const identifierRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const claim = requirement.subject_claim ?? "sub";
  const provider = issuerHost(requirement.issuer);

  const conflict = add.isError && isApiError(add.error) && add.error.status === 409 ? add.error : null;
  const taken = (field: string) => conflict?.fields.includes(field) ?? false;
  const problems = {
    name: !name.trim() ? "Enter their name." : undefined,
    email: !email.trim() ? "Enter their e-mail address." : !EMAIL_SHAPE.test(email.trim()) ? "Enter one e-mail address, like name@example.com." : undefined,
    identifier: !identifier.trim() ? `Enter the value your identity provider sends in the “${claim}” claim.` : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (key: keyof typeof problems) => (tried ? problems[key] : undefined);
  const identifierError = shown("identifier") ?? (taken("identifier") ? "A member of this organization already has this identifier." : undefined);
  const emailError = shown("email") ?? (taken("email") ? "A member of this organization already uses this e-mail address." : undefined);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    const first = (
      [
        [problems.name, nameRef],
        [problems.email, emailRef],
        [problems.identifier, identifierRef],
        [problems.reason, reasonRef],
      ] as const
    ).find(([problem]) => problem);
    if (first) {
      first[1].current?.focus();
      return;
    }
    if (add.isPending) return;
    add.mutate(
      {
        name: name.trim(),
        email: email.trim(),
        identifier: identifier.trim(),
        reason: reason.trim(),
      },
      {
        onSuccess: (member) => {
          toast({
            title: `${member.member.name} added`,
            description: "They start with least access. Give them access on their page.",
          });
          onDone();
          router.push(`/access/members/${member.member.id}`);
        },
        onError: (error) => {
          if (isApiError(error) && error.status === 409) {
            (error.fields.includes("identifier") ? identifierRef : emailRef).current?.focus();
          }
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-describedby={`${id}-trust`}>
      <div className={styles.trust} id={`${id}-trust`}>
        <p>
          They sign in at <strong>{provider}</strong>, which must send exactly the identifier below for them. Lattice never matches people by e-mail.
        </p>
        <p>
          They start with least access: Internal clearance, no accounts, departments, groups or documents, and not an administrator. You give them access on
          their page afterwards.
        </p>
      </div>
      <Field label="Name" required error={shown("name")}>
        {(control) => <TextInput {...control} ref={nameRef} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoComplete="off" />}
      </Field>
      <Field label="E-mail" required error={emailError} help="Shown with their name and used to contact them. It is not how they sign in.">
        {(control) => (
          <TextInput
            {...control}
            ref={emailRef}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            maxLength={255}
            autoComplete="off"
            spellCheck={false}
          />
        )}
      </Field>
      <Field
        label="Identifier from your identity provider"
        required
        error={identifierError}
        help={
          <>
            The exact value {provider} sends in the <code className={styles.claim}>{claim}</code> claim for this person, including capital letters.
            {claim === "email" && " Even though this claim holds an e-mail address, it's compared exactly, and it can differ from the e-mail above."} If it
            doesn&apos;t match, they can&apos;t sign in.
          </>
        }
      >
        {(control) => (
          <TextInput
            {...control}
            ref={identifierRef}
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            maxLength={255}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className={styles.identifierInput}
          />
        )}
      </Field>
      <Field label="Reason" required error={shown("reason")} help="Recorded in the audit log with your name.">
        {(control) => <TextArea {...control} ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={MAX_REASON} />}
      </Field>
      {add.isError && !conflict && (
        <Callout tone="fault" label="Not added" role="alert">
          <p>{addError(add.error)}</p>
        </Callout>
      )}
      {conflict && (
        <p className="visually-hidden" role="alert">
          Not added: {conflict.fields.includes("identifier") ? "the identifier" : "the e-mail address"} already belongs to a member.
        </p>
      )}
      <div className={styles.dialogActions}>
        <Button type="button" onClick={onDone} disabled={add.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={add.isPending}>
          Add member
        </Button>
      </div>
    </form>
  );
}

function addError(error: unknown): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 403) return "You no longer have administrator access.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  if (error.issues.includes("no_active_connection")) {
    return "Your organization's identity provider connection isn't active, so nobody can be added. Your Lattice operator can help.";
  }
  if (error.status === 422) return "Lattice couldn't add this person. Check each field and try again.";
  return error.message;
}

/* --- shared review footer -------------------------------------------------------------- */

interface DialogProps {
  member: MemberAccess;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}

function ReasonAndApply({
  name,
  pending,
  ready,
  error,
  applyLabel,
  onCancel,
  onApply,
}: {
  name: string;
  pending: boolean;
  ready: boolean;
  error: unknown;
  applyLabel: string;
  onCancel: () => void;
  onApply: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const missing = reason.trim().length === 0;
  const conflict = isApiError(error) && error.status === 409 && !error.issues.includes("taken");
  const submit = () => {
    setTouched(true);
    if (missing) {
      reasonRef.current?.focus();
      return;
    }
    if (!ready || pending) return;
    onApply(reason.trim());
  };
  return (
    <>
      <Field
        label="Reason"
        required
        help="Recorded in the audit log with your name. Every administrator can read it."
        error={touched && missing ? "Give a reason for this change." : undefined}
      >
        {(control) => (
          <TextArea
            {...control}
            ref={reasonRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onBlur={() => setTouched(true)}
            maxLength={MAX_REASON}
            placeholder="Why is this change needed?"
          />
        )}
      </Field>
      {error != null && (
        <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
          <p>{changeError(error, name)}</p>
          {conflict && (
            <p>
              <Button size="sm" icon={<Icon as={RotateCw} />} onClick={onCancel}>
                Close and review the latest
              </Button>
            </p>
          )}
        </Callout>
      )}
      <div className={styles.dialogActions}>
        <Button onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={pending} disabled={!ready || conflict}>
          {applyLabel}
        </Button>
      </div>
    </>
  );
}

function PreviewSkeleton({ label }: { label: string }) {
  return (
    <div className={styles.previewSkeleton} aria-busy="true">
      <span className="visually-hidden" role="status">
        {label}
      </span>
      <span aria-hidden="true" />
      <span aria-hidden="true" />
    </div>
  );
}

/* --- the administrator capability ------------------------------------------------------ */

export function AdministratorDialog({ member, granting, open, onOpenChange, onApplied }: DialogProps & { granting: boolean }) {
  const name = member.member.name;
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={granting ? `Make ${name} an administrator` : `Remove ${name}'s administrator capability`}>
      {open && (
        <AdministratorReview
          member={member}
          granting={granting}
          onCancel={() => onOpenChange(false)}
          onApplied={(change) => {
            onApplied(change);
            onOpenChange(false);
          }}
        />
      )}
    </Modal>
  );
}

function AdministratorReview({
  member,
  granting,
  onCancel,
  onApplied,
}: {
  member: MemberAccess;
  granting: boolean;
  onCancel: () => void;
  onApplied: (change: AppliedChange) => void;
}) {
  const preview = usePreviewAdministrator(member.member.id);
  const apply = useSetAdministrator(member.member.id);
  const toast = useToast();
  const name = member.member.name;
  const { mutate } = preview;
  useEffect(() => {
    mutate(granting);
  }, [mutate, granting]);
  const label = (on: boolean) => (on ? "Administrator" : "Not an administrator");
  const rows: ComparisonRow[] = preview.data
    ? [
        {
          key: "administrator",
          rule: "Administrator",
          now: [{ name: label(preview.data.now) }],
          after: [{ name: label(preview.data.after) }],
          direction: preview.data.after ? "gain" : "loss",
        },
      ]
    : [];

  return (
    <div className={styles.form}>
      <h3 className={styles.reviewHeading}>What will change</h3>
      {preview.isError ? (
        <Callout tone="fault" label="Couldn't preview this change" role="alert">
          <p>{changeError(preview.error, name)}</p>
        </Callout>
      ) : !preview.data ? (
        <PreviewSkeleton label="Working out what this change does…" />
      ) : (
        <>
          <Comparison rows={rows} />
          <ul className={styles.effects}>
            <li>
              {granting
                ? `${name} will be able to manage members, their access and the organization's setup, and read the audit log.`
                : `${name} will no longer be able to manage members, access or the organization's setup.`}
            </li>
          </ul>
          <p className={styles.contentUnchanged} data-testid="content-unchanged">
            <strong>Content access doesn&apos;t change.</strong> Being an administrator gives no access to content: what {name} can read stays exactly as
            described on this page.
          </p>
        </>
      )}
      <ReasonAndApply
        name={name}
        pending={apply.isPending}
        ready={Boolean(preview.data)}
        error={apply.isError ? apply.error : null}
        applyLabel={granting ? "Make administrator" : "Remove capability"}
        onCancel={onCancel}
        onApply={(reason) =>
          apply.mutate(
            { administrator: granting, version: member.version, reason },
            {
              onSuccess: () => {
                const title = granting ? `${name} is an administrator` : `${name} is no longer an administrator`;
                toast({ title, description: "Content access unchanged" });
                onApplied({
                  title,
                  detail: "Content access unchanged.",
                  at: new Date().toISOString(),
                });
              },
            },
          )
        }
      />
    </div>
  );
}

/* --- reinstating a removed member ------------------------------------------------------ */

export function ReinstateDialog({ member, open, onOpenChange, onApplied }: DialogProps) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Reinstate ${member.member.name}`}>
      {open && (
        <ReinstateReview
          member={member}
          onCancel={() => onOpenChange(false)}
          onApplied={(change) => {
            onApplied(change);
            onOpenChange(false);
          }}
        />
      )}
    </Modal>
  );
}

function ReinstateReview({ member, onCancel, onApplied }: { member: MemberAccess; onCancel: () => void; onApplied: (change: AppliedChange) => void }) {
  const preview = usePreviewReinstatement(member.member.id);
  const apply = useReinstate(member.member.id);
  const toast = useToast();
  const name = member.member.name;
  const { mutate } = preview;
  useEffect(() => {
    mutate();
  }, [mutate]);

  return (
    <div className={styles.form}>
      <div className={styles.trust}>
        <p>
          {name} becomes active again on the same sign-in identity
          {member.lifecycle.sign_in_identifier ? (
            <>
              {" "}
              (<code className={styles.claim}>{member.lifecycle.sign_in_identifier}</code>)
            </>
          ) : null}
          . They start again from least access: what they had before removal is shown below and is <strong>not</strong> restored. Give them access afterwards,
          as for anyone new. Their earlier sessions stay ended.
        </p>
      </div>
      <h3 className={styles.reviewHeading}>What will change</h3>
      {preview.isError ? (
        <Callout tone="fault" label="Couldn't preview this change" role="alert">
          <p>{changeError(preview.error, name)}</p>
        </Callout>
      ) : !preview.data ? (
        <PreviewSkeleton label="Working out what reinstating does…" />
      ) : (
        <Preview member={member} preview={preview.data} />
      )}
      <ReasonAndApply
        name={name}
        pending={apply.isPending}
        ready={Boolean(preview.data)}
        error={apply.isError ? apply.error : null}
        applyLabel="Reinstate"
        onCancel={onCancel}
        onApply={(reason) =>
          apply.mutate(
            { version: member.version, reason },
            {
              onSuccess: () => {
                const title = `${name} reinstated`;
                toast({ title, description: "Least access: nothing from before was restored" });
                onApplied({
                  title,
                  detail: "Started from least access; nothing from before removal was restored.",
                  at: new Date().toISOString(),
                });
              },
            },
          )
        }
      />
    </div>
  );
}
