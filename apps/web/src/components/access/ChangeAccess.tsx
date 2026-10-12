"use client";

import { ArrowRight, Minus, Plus, RotateCw } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccessDirectory, useChangeMember, usePreviewMember } from "@/api/queries";
import type { AccessPreview, Directory, MemberAccess, MemberChange } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Checkbox, ChoiceGroup, Field, Radio, SearchInput, TextArea } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { useToast } from "@/components/primitives/Toast";
import { levelLabel } from "@/lib/format";

import styles from "./Access.module.css";
import { comparisonRows, DIRECTION_LABEL, type ComparisonRow, type Item } from "./compare";
import { auditPhrase, previewSentence } from "./copy";
import { changeError } from "./errors";
import type { AppliedChange } from "./MemberScreen";

export const MAX_REASON = 500;

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

interface DialogProps {
  member: MemberAccess;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}

/** Edit → review (Now → After from the preview, a reason) → apply, against the version loaded. */
export function ChangeAccessDialog({ member, open, onOpenChange, onApplied }: DialogProps) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Change ${member.member.name}'s access`}>
      {/* Mounted per opening, so each starts from the member as last loaded. */}
      {open && (
        <ChangeAccessForm
          member={member}
          onDone={() => onOpenChange(false)}
          onApplied={(change) => {
            onApplied(change);
            onOpenChange(false);
          }}
        />
      )}
    </Modal>
  );
}

function ChangeAccessForm({ member, onDone, onApplied }: { member: MemberAccess; onDone: () => void; onApplied: (change: AppliedChange) => void }) {
  const directory = useAccessDirectory(true);
  const [draft, setDraft] = useState<Draft>(() => draftOf(member));
  const [reviewing, setReviewing] = useState(false);
  const change = useMemo(() => changeOf(member, draft), [member, draft]);
  const changed = Object.keys(change).length;
  const reviewButton = useRef<HTMLButtonElement>(null);
  const backed = useRef(false);

  // Back from the review lands on the control that opened it.
  useEffect(() => {
    if (!reviewing && backed.current) reviewButton.current?.focus();
  }, [reviewing]);

  if (reviewing) {
    return (
      <ReviewChange
        member={member}
        change={change}
        onBack={() => {
          backed.current = true;
          setReviewing(false);
        }}
        onCancel={onDone}
        onApplied={onApplied}
      />
    );
  }
  return (
    <div className={styles.form}>
      <p className={styles.step}>Step 1 of 2 · Edit the rules</p>
      {directory.isError ? (
        <Callout tone="fault" label="Couldn't load accounts, departments and groups" role="alert">
          <p>Close this and try again.</p>
        </Callout>
      ) : !directory.data ? (
        <p className={styles.secondary} role="status">
          Loading accounts, departments and groups…
        </p>
      ) : (
        <EditFields member={member} directory={directory.data} draft={draft} onChange={setDraft} />
      )}
      <div className={styles.dialogActions}>
        <p className={styles.changed} role="status">
          {changed === 0 ? "Nothing changed yet" : `${changed} ${changed === 1 ? "rule" : "rules"} changed`}
        </p>
        <Button onClick={onDone}>Cancel</Button>
        <Button ref={reviewButton} variant="primary" icon={<Icon as={ArrowRight} />} onClick={() => setReviewing(true)} disabled={changed === 0 || !directory.data}>
          Review change
        </Button>
      </div>
    </div>
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
      <ChoiceGroup legend="Clearance" help="Restricted content is never reached through clearance — only through an explicit grant.">
        {(["internal", "confidential"] as const).map((level) => (
          <Radio
            key={level}
            name={`${id}-clearance`}
            label={levelLabel(level)}
            checked={draft.clearance === level}
            onChange={() => onChange({ ...draft, clearance: level })}
          />
        ))}
      </ChoiceGroup>
      <ChoiceGroup legend="Account scope">
        <Radio
          name={`${id}-scope`}
          label="Assigned accounts only"
          help="Content for the accounts below, and content that isn't tied to an account."
          checked={draft.account_scope === "assigned"}
          onChange={() => onChange({ ...draft, account_scope: "assigned" })}
        />
        <Radio name={`${id}-scope`} label="All accounts" help="Content for every account." checked={draft.account_scope === "all"} onChange={() => onChange({ ...draft, account_scope: "all" })} />
      </ChoiceGroup>
      <Checklist
        legend="Assigned accounts"
        help={draft.account_scope === "all" ? "Assignments are kept, but don't matter while the scope is all accounts." : undefined}
        items={directory.accounts.map((a) => ({ id: a.id, label: a.archived ? `${a.name} (archived)` : a.name }))}
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

/** Beyond this many choices a list gets a filter (it filters what is already loaded). */
const FILTER_AFTER = 8;

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
  const [filter, setFilter] = useState("");
  const shown = filter ? items.filter((i) => i.label.toLowerCase().includes(filter.trim().toLowerCase())) : items;
  return (
    <ChoiceGroup legend={legend} help={items.length === 0 ? emptyText : help}>
      {items.length > FILTER_AFTER && (
        <Field label={`Filter ${legend.toLowerCase()}`} hideLabel>
          {(control) => <SearchInput {...control} size="sm" value={filter} onValueChange={setFilter} placeholder={`Filter ${legend.toLowerCase()}`} />}
        </Field>
      )}
      <div className={styles.checklist}>
        {shown.map((item) => (
          <Checkbox key={item.id} label={item.label} checked={checked.has(item.id)} onChange={() => onToggle(item.id)} />
        ))}
      </div>
    </ChoiceGroup>
  );
}

/** Suspend or reactivate: straight to the review. */
export function StatusDialog({ member, open, onOpenChange, onApplied }: DialogProps) {
  const suspending = member.member.status === "active";
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`${suspending ? "Suspend" : "Reactivate"} ${member.member.name}`}>
      {open && (
        <ReviewChange
          member={member}
          change={{ status: suspending ? "suspended" : "active" }}
          onCancel={() => onOpenChange(false)}
          onApplied={(change) => {
            onApplied(change);
            onOpenChange(false);
          }}
          applyLabel={suspending ? "Suspend" : "Reactivate"}
          danger={suspending}
        />
      )}
    </Modal>
  );
}

/**
 * The review: what the server says this change does (Now → After, rule by rule, and the
 * effect sentences), the counts it returns -- only among documents you can read -- a
 * required reason, and the change applied against the version you reviewed.
 */
function ReviewChange({
  member,
  change,
  onBack,
  onCancel,
  onApplied,
  applyLabel = "Apply change",
  danger = false,
}: {
  member: MemberAccess;
  change: MemberChange;
  onBack?: () => void;
  onCancel: () => void;
  onApplied: (change: AppliedChange) => void;
  applyLabel?: string;
  danger?: boolean;
}) {
  const preview = usePreviewMember(member.member.id);
  const apply = useChangeMember(member.member.id);
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const name = member.member.name;
  const { mutate } = preview;
  // The preview is asked for once per distinct change (callers may pass a new object).
  const key = JSON.stringify(change);
  useEffect(() => {
    mutate(JSON.parse(key) as MemberChange);
  }, [mutate, key]);
  // Arriving from the edit step, focus lands on the review (the button that led here is gone).
  useEffect(() => {
    if (onBack) heading.current?.focus();
  }, [onBack]);

  const missing = reason.trim().length === 0;
  const conflict = apply.isError && isApiError(apply.error) && apply.error.status === 409;
  const submit = () => {
    setTouched(true);
    if (missing) {
      reasonRef.current?.focus();
      return;
    }
    if (!preview.data || apply.isPending) return;
    apply.mutate(
      { ...change, version: member.version, reason: reason.trim() },
      {
        onSuccess: () => {
          const title = change.status === "suspended" ? `${name} suspended` : change.status === "active" ? `${name} reactivated` : `${name}'s access changed`;
          const detail = preview.data?.effects.map(auditPhrase).join(" · ");
          toast({ title: change.status ? title : "Change applied", description: change.status ? undefined : name });
          onApplied({ title, detail, at: new Date().toISOString() });
        },
      },
    );
  };

  return (
    <div className={styles.form}>
      <h3 ref={heading} tabIndex={-1} id={`${id}-heading`} className={styles.reviewHeading}>
        {onBack && <span className={styles.step}>Step 2 of 2 · </span>}
        What will change
      </h3>
      {preview.isError ? (
        <Callout tone="fault" label="Couldn't preview this change" role="alert">
          <p>{changeError(preview.error, name)}</p>
        </Callout>
      ) : !preview.data ? (
        <div className={styles.previewSkeleton} aria-busy="true">
          <span className="visually-hidden" role="status">
            Working out what this change does…
          </span>
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </div>
      ) : (
        <Preview member={member} preview={preview.data} />
      )}
      <Field
        label="Reason"
        required
        help="Recorded in the audit log with your name. Every administrator can read it; don't paste document content."
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
      {apply.isError && (
        <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
          <p>{changeError(apply.error, name)}</p>
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
        {onBack ? (
          <Button onClick={onBack} disabled={apply.isPending}>
            Back
          </Button>
        ) : (
          <Button onClick={onCancel} disabled={apply.isPending}>
            Cancel
          </Button>
        )}
        <Button variant={danger ? "danger" : "primary"} onClick={submit} loading={apply.isPending} disabled={!preview.data || conflict}>
          {applyLabel}
        </Button>
      </div>
    </div>
  );
}

export function Preview({ member, preview }: { member: MemberAccess; preview: AccessPreview }) {
  const rows = comparisonRows(member, preview.effects);
  const name = member.member.name;
  return (
    <>
      {rows.length > 0 && <Comparison rows={rows} />}
      <ul className={styles.effects}>
        {preview.effects.map((e, i) => (
          <li key={`${e.kind}-${e.subject?.id ?? i}`}>{previewSentence(e, name)}</li>
        ))}
      </ul>
      <div className={styles.counts}>
        <p className={styles.countsLabel}>Among documents you can read</p>
        <p className={styles.count}>
          <span className={styles.countLabel}>Gains</span>
          <span className={styles.countValue}>{preview.documents_gained.toLocaleString("en-GB")}</span>
        </p>
        <p className={styles.count}>
          <span className={styles.countLabel}>Loses</span>
          <span className={styles.countValue}>{preview.documents_lost.toLocaleString("en-GB")}</span>
        </p>
      </div>
      <p className={styles.secondary}>
        Counted only among documents you can read. Effects on anything else are described by the rules above, without counts.
      </p>
    </>
  );
}

/** Now → After, one rule per row; a changed row carries its direction in a margin bar and in words. */
export function Comparison({ rows }: { rows: ComparisonRow[] }) {
  return (
    <table className={styles.compare}>
      <caption className="visually-hidden">Now and after this change, rule by rule</caption>
      <thead>
        <tr>
          <th scope="col">Rule</th>
          <th scope="col">Now</th>
          <th scope="col">
            <span className="visually-hidden">changes to</span>
          </th>
          <th scope="col">After</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className={styles.compareRow} data-direction={r.direction}>
            <th scope="row" className={styles.compareRule}>
              {r.rule}
              <span className={styles.direction}>{DIRECTION_LABEL[r.direction]}</span>
            </th>
            <td className={styles.compareNow}>
              <Items items={r.now} />
            </td>
            <td className={styles.compareArrow} aria-hidden="true">
              <Icon as={ArrowRight} size={14} />
            </td>
            <td className={styles.compareAfter}>
              <Items items={r.after} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Items({ items }: { items: Item[] }) {
  if (items.length === 0) return <span className={styles.secondary}>None</span>;
  return (
    <ul className={styles.items}>
      {items.map((item, i) => (
        <li key={`${item.name}-${i}`} className={item.change ? styles[item.change] : undefined}>
          {item.change === "added" && <Icon as={Plus} size={12} />}
          {item.change === "removed" && <Icon as={Minus} size={12} />}
          <span>{item.name}</span>
          {item.change && <span className="visually-hidden"> ({item.change})</span>}
        </li>
      ))}
    </ul>
  );
}
