"use client";

import { AlertCircle, FilePlus2 } from "lucide-react";
import { useState } from "react";

import { useExplanation, useGrant, useRevokeGrant } from "@/api/queries";
import type { DocumentRef, GrantOut, MemberAccess } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { formatDay, formatDayTime } from "@/lib/format";

import styles from "./Access.module.css";
import { decidingReasons, reasonPhrase } from "./copy";
import { DocumentLine, DocumentSearch } from "./DocumentSearch";
import { changeError } from "./errors";
import { ReasonField } from "./ReasonField";

/** Direct grants on documents you can read; others are only acknowledged, never listed. */
export function GrantsSection({ member, canChange }: { member: MemberAccess; canChange: boolean }) {
  const [granting, setGranting] = useState(false);
  const [revoking, setRevoking] = useState<GrantOut | null>(null);
  const name = member.member.name;
  return (
    <section className={styles.section} aria-labelledby="grants-label">
      <SectionLabel id="grants-label" meta={member.grants.length ? `${member.grants.length} you can read` : undefined}>
        Direct grants
      </SectionLabel>
      <p className={styles.note}>
        A grant shares one document with {name}, whatever the rules above say. Restricted documents are reached only this
        way.
      </p>
      {member.grants.length === 0 ? (
        <p className={styles.off}>No direct grants on documents you can read.</p>
      ) : (
        <ul className={styles.list}>
          {member.grants.map((g) => (
            <li key={g.id} className={styles.row}>
              <span className={styles.rowBody}>
                <DocumentLine doc={g.document} />
                <span className={styles.secondary}>
                  {g.origin === "source" ? "From the source's permissions" : `Granted by ${g.granted_by ?? "an administrator"}`}
                  {" · "}
                  <time dateTime={g.created_at} title={formatDayTime(g.created_at) ?? undefined}>
                    {formatDay(g.created_at)}
                  </time>
                </span>
                {g.reason && <span className={styles.reason}>“{g.reason}”</span>}
              </span>
              <span className={styles.rowAction}>
                {g.revocable && canChange && (
                  <Button size="sm" onClick={() => setRevoking(g)} aria-label={`Revoke grant: ${g.document.title}`}>
                    Revoke
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {member.has_other_grants && (
        <p className={styles.note}>
          Grants on documents you can&apos;t read aren&apos;t listed here. An administrator who can read them can review
          them.
        </p>
      )}
      {canChange && (
        <div className={styles.actions}>
          <Button icon={<Icon as={FilePlus2} />} onClick={() => setGranting(true)}>
            Grant a document
          </Button>
        </div>
      )}
      <GrantDialog member={member} open={granting} onOpenChange={setGranting} />
      <RevokeDialog member={member} grant={revoking} onClose={() => setRevoking(null)} />
    </section>
  );
}

function GrantDialog({ member, open, onOpenChange }: { member: MemberAccess; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Grant ${member.member.name} a document`}>
      {open && <GrantForm member={member} onDone={() => onOpenChange(false)} />}
    </Modal>
  );
}

function GrantForm({ member, onDone }: { member: MemberAccess; onDone: () => void }) {
  const [doc, setDoc] = useState<DocumentRef | null>(null);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const grant = useGrant(member.member.id);
  const name = member.member.name;
  const missing = reason.trim().length === 0;
  const submit = () => {
    setTouched(true);
    if (!doc || missing) return;
    grant.mutate({ documentId: doc.id, reason: reason.trim() }, { onSuccess: onDone });
  };
  return (
    <>
      <DocumentSearch label="Document to grant" selected={doc} onSelect={setDoc} />
      {doc && <Verdict memberId={member.member.id} name={name} doc={doc} />}
      <ReasonField
        value={reason}
        onChange={setReason}
        onBlur={() => setTouched(true)}
        invalid={touched && missing}
        placeholder="Why does this person need this document?"
        help="Recorded in the audit log with your name."
        error="Give a reason for this grant."
      />
      {grant.isError && (
        <p className={styles.problem} role="alert">
          <Icon as={AlertCircle} size={14} /> {changeError(grant.error, name)}
        </p>
      )}
      <div className={styles.dialogActions}>
        <Button onClick={onDone} disabled={grant.isPending}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={grant.isPending} disabled={!doc}>
          Grant
        </Button>
      </div>
    </>
  );
}

function RevokeDialog({ member, grant, onClose }: { member: MemberAccess; grant: GrantOut | null; onClose: () => void }) {
  const revoke = useRevokeGrant(member.member.id);
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
      <ReasonField value={reason} onChange={setReason} invalid={false} placeholder="Why is this grant no longer needed?" help="Recorded in the audit log with your name." />
      {revoke.isError && (
        <p className={styles.problem} role="alert">
          <Icon as={AlertCircle} size={14} /> {changeError(revoke.error, name)}
        </p>
      )}
      <div className={styles.dialogActions}>
        <Button onClick={close} disabled={revoke.isPending}>
          Cancel
        </Button>
        <Button
          variant="primary"
          loading={revoke.isPending}
          disabled={!reason.trim()}
          onClick={() => grant && revoke.mutate({ grantId: grant.id, reason: reason.trim() }, { onSuccess: close })}
        >
          Revoke
        </Button>
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
        <Icon as={AlertCircle} size={14} /> {changeError(query.error, name)}
      </p>
    );
  }
  if (!query.data) {
    return (
      <p className={styles.help} role="status">
        Checking {name}&apos;s access…
      </p>
    );
  }
  const e = query.data;
  const via = e.via.map((v) => v.name ?? `a private channel (${v.reference ?? "synced"})`);
  return (
    <div className={styles.verdict} role="status">
      <p>
        <strong>
          {name} {e.readable ? "can" : "can't"} read “{e.document.title}”
        </strong>{" "}
        because:
      </p>
      <ul>
        {decidingReasons(e.reasons, e.readable).map((r) => (
          <li key={r}>{reasonPhrase(r, name)}</li>
        ))}
        {via.length > 0 && <li>shared with {via.join(", ")}</li>}
      </ul>
    </div>
  );
}
