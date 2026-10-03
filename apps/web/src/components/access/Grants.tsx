"use client";

import { FilePlus2 } from "lucide-react";
import { useState } from "react";

import { useExplanation, useGrant, useRevokeGrant } from "@/api/queries";
import type { DocumentRef, GrantOut, MemberAccess } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Field, TextArea } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { useToast } from "@/components/primitives/Toast";
import { OpsSection } from "@/components/ops/OpsPage";
import { formatDay, formatDayTime } from "@/lib/format";

import styles from "./Access.module.css";
import { MAX_REASON } from "./ChangeAccess";
import { decidingReasons, reasonPhrase } from "./copy";
import { DocumentLine, DocumentSearch } from "./DocumentSearch";
import { changeError } from "./errors";
import type { AppliedChange } from "./MemberScreen";

/** Direct grants on documents you can read; grants on others are acknowledged once, never listed. */
export function GrantsSection({ member, canChange, onApplied }: { member: MemberAccess; canChange: boolean; onApplied: (change: AppliedChange) => void }) {
  const [granting, setGranting] = useState(false);
  const [revoking, setRevoking] = useState<GrantOut | null>(null);
  const name = member.member.name;
  return (
    <OpsSection
      id="grants-heading"
      title="Direct grants"
      count={member.grants.length ? `${member.grants.length.toLocaleString("en-GB")} you can read` : undefined}
      aside={
        canChange ? (
          <Button size="sm" icon={<Icon as={FilePlus2} />} onClick={() => setGranting(true)}>
            Grant a document
          </Button>
        ) : undefined
      }
    >
      <p className={styles.note}>A grant shares one document with {name}, whatever the rules say. Restricted documents are reached only this way.</p>
      {member.grants.length === 0 ? (
        <p className={styles.secondary}>No direct grants on documents you can read.</p>
      ) : (
        <ul className={styles.grants}>
          {member.grants.map((g) => (
            <li key={g.id} className={styles.grant}>
              <DocumentLine doc={g.document} />
              <span className={styles.grantMeta}>
                {g.origin === "source" ? "From the source's permissions" : `Granted by ${g.granted_by ?? "an administrator"}`}
                {" · "}
                <time className={styles.when} dateTime={g.created_at} title={formatDayTime(g.created_at) ?? undefined}>
                  {formatDay(g.created_at)}
                </time>
              </span>
              {g.reason && <span className={styles.reason}>“{g.reason}”</span>}
              {g.revocable && canChange && (
                <span className={styles.grantAction}>
                  <Button size="sm" onClick={() => setRevoking(g)} aria-label={`Revoke grant: ${g.document.title}`}>
                    Revoke
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {member.has_other_grants && (
        <p className={styles.secondary}>Grants on documents you can&apos;t read aren&apos;t listed here; an administrator who can read them can review them.</p>
      )}
      <GrantDialog member={member} open={granting} onOpenChange={setGranting} onApplied={onApplied} />
      <RevokeDialog member={member} grant={revoking} onClose={() => setRevoking(null)} onApplied={onApplied} />
    </OpsSection>
  );
}

function GrantDialog({ member, open, onOpenChange, onApplied }: { member: MemberAccess; open: boolean; onOpenChange: (open: boolean) => void; onApplied: (change: AppliedChange) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Grant ${member.member.name} a document`}>
      {open && (
        <GrantForm
          member={member}
          onCancel={() => onOpenChange(false)}
          onDone={(change) => {
            onApplied(change);
            onOpenChange(false);
          }}
        />
      )}
    </Modal>
  );
}

function GrantForm({ member, onCancel, onDone }: { member: MemberAccess; onCancel: () => void; onDone: (change: AppliedChange) => void }) {
  const [doc, setDoc] = useState<DocumentRef | null>(null);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const grant = useGrant(member.member.id);
  const toast = useToast();
  const name = member.member.name;
  const missing = reason.trim().length === 0;
  const submit = () => {
    setTouched(true);
    if (!doc || missing || grant.isPending) return;
    grant.mutate(
      { documentId: doc.id, reason: reason.trim() },
      {
        onSuccess: () => {
          toast({ title: "Document granted", description: doc.title });
          onDone({ title: `Granted “${doc.title}” to ${name}`, at: new Date().toISOString() });
        },
      },
    );
  };
  return (
    <div className={styles.form}>
      <DocumentSearch label="Document to grant" selected={doc} onSelect={setDoc} />
      {doc && <Verdict memberId={member.member.id} name={name} doc={doc} />}
      <Field label="Reason" required help="Recorded in the audit log with your name." error={touched && missing ? "Give a reason for this grant." : undefined}>
        {(control) => (
          <TextArea
            {...control}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onBlur={() => setTouched(true)}
            maxLength={MAX_REASON}
            placeholder="Why does this person need this document?"
          />
        )}
      </Field>
      {grant.isError && (
        <Callout tone="fault" label="Not granted" role="alert">
          <p>{changeError(grant.error, name)}</p>
        </Callout>
      )}
      <div className={styles.dialogActions}>
        <Button onClick={onCancel} disabled={grant.isPending}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={grant.isPending} disabled={!doc}>
          Grant
        </Button>
      </div>
    </div>
  );
}

function RevokeDialog({ member, grant, onClose, onApplied }: { member: MemberAccess; grant: GrantOut | null; onClose: () => void; onApplied: (change: AppliedChange) => void }) {
  const revoke = useRevokeGrant(member.member.id);
  const toast = useToast();
  const [reason, setReason] = useState("");
  const name = member.member.name;
  const close = () => {
    setReason("");
    revoke.reset();
    onClose();
  };
  return (
    <Modal
      open={grant !== null}
      onOpenChange={(open) => !open && close()}
      title="Revoke this grant?"
      size="confirm"
      description={grant ? `${name} will immediately lose the direct grant to “${grant.document.title}”, and keeps any access the rules or other grants give.` : undefined}
    >
      <div className={styles.form}>
        <Field label="Reason" required help="Recorded in the audit log with your name.">
          {(control) => <TextArea {...control} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={MAX_REASON} placeholder="Why is this grant no longer needed?" />}
        </Field>
        {revoke.isError && (
          <Callout tone="fault" label="Not revoked" role="alert">
            <p>{changeError(revoke.error, name)}</p>
          </Callout>
        )}
        <div className={styles.dialogActions}>
          <Button onClick={close} disabled={revoke.isPending}>
            Cancel
          </Button>
          <Button
            variant="danger"
            loading={revoke.isPending}
            disabled={!reason.trim()}
            onClick={() =>
              grant &&
              revoke.mutate(
                { grantId: grant.id, reason: reason.trim() },
                {
                  onSuccess: () => {
                    toast({ title: "Grant revoked", description: grant.document.title });
                    onApplied({ title: `Revoked ${name}'s grant to “${grant.document.title}”`, at: new Date().toISOString() });
                    close();
                  },
                },
              )
            }
          >
            Revoke
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** Why the member can or can't read a document you can read (explain_for_reviewer). */
export function Verdict({ memberId, name, doc }: { memberId: string; name: string; doc: DocumentRef }) {
  const query = useExplanation(memberId, doc.id);
  if (query.isError) {
    return (
      <p className={styles.problem} role="alert">
        {changeError(query.error, name)}
      </p>
    );
  }
  if (!query.data) {
    return (
      <p className={styles.secondary} role="status">
        Checking {name}&apos;s access…
      </p>
    );
  }
  const e = query.data;
  const via = e.via.map((v) => v.name ?? "a private channel you aren't in");
  return (
    <div className={styles.verdict} role="status" data-readable={e.readable}>
      <p className={styles.verdictHead}>
        {name} {e.readable ? "can" : "can't"} read “{e.document.title}”
      </p>
      <p className={styles.secondary}>because:</p>
      <ul>
        {decidingReasons(e.reasons, e.readable).map((r) => (
          <li key={r}>{reasonPhrase(r, name)}</li>
        ))}
        {via.length > 0 && <li>shared with {via.join(", ")}</li>}
      </ul>
    </div>
  );
}
