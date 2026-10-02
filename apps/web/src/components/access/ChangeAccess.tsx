"use client";

import { AlertCircle } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";

import { useAccessDirectory, useChangeMember, usePreviewMember } from "@/api/queries";
import type { Directory, MemberAccess, MemberChange } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { levelLabel } from "@/lib/format";

import styles from "./Access.module.css";
import { previewSentence } from "./copy";
import { changeError } from "./errors";
import { ReasonField } from "./ReasonField";

interface Draft {
  clearance: "internal" | "confidential";
  account_scope: "all" | "assigned";
  accounts: Set<string>;
  departments: Set<string>;
  groups: Set<string>;
}

function draftOf(m: MemberAccess): Draft {
  return {
    clearance: m.member.clearance,
    account_scope: m.member.account_scope,
    accounts: new Set(m.accounts.map((a) => a.id)),
    departments: new Set(m.departments.map((d) => d.id)),
    groups: new Set(m.groups.filter((g) => g.origin === "admin").map((g) => g.id)),
  };
}

const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((x) => b.has(x));

/** Only what differs from the member's current access is sent. */
export function changeOf(m: MemberAccess, d: Draft): MemberChange {
  const base = draftOf(m);
  const change: MemberChange = {};
  if (d.clearance !== base.clearance) change.clearance = d.clearance;
  if (d.account_scope !== base.account_scope) change.account_scope = d.account_scope;
  if (!same(d.accounts, base.accounts)) change.accounts = [...d.accounts];
  if (!same(d.departments, base.departments)) change.departments = [...d.departments];
  if (!same(d.groups, base.groups)) change.groups = [...d.groups];
  return change;
}

/** Edit → review (preview, reason) → apply, for clearance, scope, accounts, departments, groups. */
export function ChangeAccessDialog({ member, open, onOpenChange }: { member: MemberAccess; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Change ${member.member.name}'s access`}>
      {/* Mounted per opening, so each starts from the member as last loaded. */}
      {open && <ChangeAccessForm member={member} onDone={() => onOpenChange(false)} />}
    </Modal>
  );
}

function ChangeAccessForm({ member, onDone }: { member: MemberAccess; onDone: () => void }) {
  const directory = useAccessDirectory(true);
  const [draft, setDraft] = useState<Draft>(() => draftOf(member));
  const [reviewing, setReviewing] = useState(false);
  const change = useMemo(() => changeOf(member, draft), [member, draft]);
  const empty = Object.keys(change).length === 0;

  if (reviewing) return <ReviewChange member={member} change={change} onBack={() => setReviewing(false)} onDone={onDone} />;
  return (
    <>
      {directory.isError ? (
        <p className={styles.problem} role="alert">
          <Icon as={AlertCircle} size={14} /> Couldn&apos;t load accounts, departments and groups. Close this and try again.
        </p>
      ) : !directory.data ? (
        <p className={styles.help} role="status">
          Loading accounts, departments and groups…
        </p>
      ) : (
        <EditFields member={member} directory={directory.data} draft={draft} onChange={setDraft} />
      )}
      <div className={styles.dialogActions}>
        <Button onClick={onDone}>Cancel</Button>
        <Button variant="primary" onClick={() => setReviewing(true)} disabled={empty || !directory.data}>
          Review change
        </Button>
      </div>
    </>
  );
}

function EditFields({ member, directory, draft, onChange }: { member: MemberAccess; directory: Directory; draft: Draft; onChange: (d: Draft) => void }) {
  const id = useId();
  const toggle = (key: "accounts" | "departments" | "groups", value: string) => {
    const next = new Set(draft[key]);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange({ ...draft, [key]: next });
  };
  const synced = member.groups.filter((g) => g.origin === "source");
  return (
    <>
      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Clearance</legend>
        {(["internal", "confidential"] as const).map((level) => (
          <label key={level} className={styles.option}>
            <input type="radio" name={`${id}-clearance`} checked={draft.clearance === level} onChange={() => onChange({ ...draft, clearance: level })} />
            <span>{levelLabel(level)}</span>
          </label>
        ))}
        <p className={styles.help}>Restricted content is never reached through clearance — only through an explicit grant.</p>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Account scope</legend>
        {(
          [
            ["assigned", "Assigned accounts only"],
            ["all", "All accounts"],
          ] as const
        ).map(([value, label]) => (
          <label key={value} className={styles.option}>
            <input type="radio" name={`${id}-scope`} checked={draft.account_scope === value} onChange={() => onChange({ ...draft, account_scope: value })} />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>

      <Checklist
        legend="Assigned accounts"
        help={draft.account_scope === "all" ? "Assignments are kept, but don't matter while the scope is all accounts." : undefined}
        items={directory.accounts.map((a) => ({ id: a.id, label: a.name }))}
        checked={draft.accounts}
        onToggle={(v) => toggle("accounts", v)}
        emptyText="No accounts exist yet."
      />
      <Checklist
        legend="Departments"
        items={directory.departments.map((d) => ({ id: d.id, label: d.name }))}
        checked={draft.departments}
        onToggle={(v) => toggle("departments", v)}
        emptyText="No departments exist yet."
      />
      <Checklist
        legend="Groups"
        help={
          synced.length > 0 || member.has_hidden_groups
            ? "Channels synced from a source follow the source and can't be changed here; this change leaves them as they are."
            : "Groups synced from a source follow the source and aren't listed here."
        }
        items={directory.groups.map((g) => ({ id: g.id, label: g.name }))}
        checked={draft.groups}
        onToggle={(v) => toggle("groups", v)}
        emptyText="No administrator-defined groups exist."
      />
    </>
  );
}

function Checklist({
  legend,
  help,
  items,
  checked,
  onToggle,
  emptyText,
}: {
  legend: string;
  help?: string;
  items: { id: string; label: string }[];
  checked: Set<string>;
  onToggle: (id: string) => void;
  emptyText: string;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>{legend}</legend>
      {items.length === 0 ? (
        <p className={styles.help}>{emptyText}</p>
      ) : (
        <div className={styles.checklist}>
          {items.map((item) => (
            <label key={item.id} className={styles.option}>
              <input type="checkbox" checked={checked.has(item.id)} onChange={() => onToggle(item.id)} />
              <span>{item.label}</span>
            </label>
          ))}
        </div>
      )}
      {help && <p className={styles.help}>{help}</p>}
    </fieldset>
  );
}

/** Suspend or reactivate: straight to the review step. */
export function StatusDialog({ member, open, onOpenChange }: { member: MemberAccess; open: boolean; onOpenChange: (open: boolean) => void }) {
  const suspending = member.member.status === "active";
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`${suspending ? "Suspend" : "Reactivate"} ${member.member.name}`} size="confirm">
      {open && (
        <ReviewChange
          member={member}
          change={{ status: suspending ? "suspended" : "active" }}
          onDone={() => onOpenChange(false)}
          applyLabel={suspending ? "Suspend" : "Reactivate"}
        />
      )}
    </Modal>
  );
}

/**
 * The preview (effects as rules; counts only among documents you can read), a required
 * reason, and the change applied against the version you reviewed.
 */
function ReviewChange({
  member,
  change,
  onBack,
  onDone,
  applyLabel = "Apply change",
}: {
  member: MemberAccess;
  change: MemberChange;
  onBack?: () => void;
  onDone: () => void;
  applyLabel?: string;
}) {
  const preview = usePreviewMember(member.member.id);
  const apply = useChangeMember(member.member.id);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const id = useId();
  const name = member.member.name;
  const { mutate } = preview;
  // The preview is asked for once per distinct change (callers may pass a new object).
  const key = JSON.stringify(change);
  useEffect(() => {
    mutate(JSON.parse(key) as MemberChange);
  }, [mutate, key]);

  const missing = reason.trim().length === 0;
  const submit = () => {
    setTouched(true);
    if (missing || !preview.data) return;
    apply.mutate({ ...change, version: member.version, reason: reason.trim() }, { onSuccess: onDone });
  };

  return (
    <>
      <section aria-labelledby={`${id}-effects`} className={styles.section}>
        <h3 id={`${id}-effects`} className={styles.legend}>
          What will change
        </h3>
        {preview.isError ? (
          <p className={styles.problem} role="alert">
            <Icon as={AlertCircle} size={14} /> {changeError(preview.error, name)}
          </p>
        ) : !preview.data ? (
          <p className={styles.help} role="status">
            Working out what this change does…
          </p>
        ) : (
          <>
            <ul className={styles.effects}>
              {preview.data.effects.map((e, i) => (
                <li key={`${e.kind}-${e.subject?.id ?? i}`}>{previewSentence(e, name)}</li>
              ))}
            </ul>
            <div className={styles.counts} aria-label="Among documents you can read">
              <p className={styles.count}>
                <span className={styles.countLabel}>Gains</span>
                <span className={styles.countValue}>{preview.data.documents_gained.toLocaleString("en-GB")}</span>
              </p>
              <p className={styles.count}>
                <span className={styles.countLabel}>Loses</span>
                <span className={styles.countValue}>{preview.data.documents_lost.toLocaleString("en-GB")}</span>
              </p>
            </div>
            <p className={styles.help}>
              Counted only among documents you can read. Effects on anything else are described by the rules above, without
              counts.
            </p>
          </>
        )}
      </section>
      <ReasonField
        value={reason}
        onChange={setReason}
        onBlur={() => setTouched(true)}
        invalid={touched && missing}
        placeholder="Why is this change needed?"
      />
      {apply.isError && (
        <p className={styles.problem} role="alert">
          <Icon as={AlertCircle} size={14} /> {changeError(apply.error, name)}
        </p>
      )}
      <div className={styles.dialogActions}>
        {onBack && (
          <Button onClick={onBack} disabled={apply.isPending}>
            Back
          </Button>
        )}
        <Button variant="primary" onClick={submit} loading={apply.isPending} disabled={!preview.data}>
          {applyLabel}
        </Button>
      </div>
    </>
  );
}
