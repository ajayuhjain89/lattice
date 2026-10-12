"use client";

import { ArrowRight, RotateCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/api/errors";
import { useAccountLifecycle, useCreateAccount, useUpdateAccount } from "@/api/queries";
import type { AccountDetail, BusinessStatus } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Field, Select, TextArea, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { useToast } from "@/components/primitives/Toast";

import styles from "./Access.module.css";
import { accountError, BUSINESS_STATUSES, businessStatusLabel } from "./accountsCopy";
import { MAX_REASON } from "./ChangeAccess";
import type { AppliedChange } from "./MemberScreen";

/*
 * Account administration (Phase 12, P12.3). Every rule shown here is the backend's: keys,
 * names and aliases are normalised and checked there, refusals are its fixed issue codes,
 * and a change applies only with the version the page was showing and a reason.
 */

const KEY_SHAPE = /^[a-z0-9][a-z0-9-]{0,62}$/;

/** A suggested key from a name: lowercase letters, digits and single hyphens. */
export function keyFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
}

/** Aliases as typed, one per line or comma-separated; the API normalises and de-duplicates. */
export function parseAliases(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((a) => a.trim())
    .filter(Boolean);
}

/* --- shared ------------------------------------------------------------------------------- */

export function ReasonField({
  value,
  onChange,
  error,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  error?: string;
  inputRef: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <Field label="Reason" required error={error} help="Recorded in the audit log with your name. Every administrator can read it.">
      {(control) => (
        <TextArea
          {...control}
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          maxLength={MAX_REASON}
          placeholder="Why is this change needed?"
        />
      )}
    </Field>
  );
}

export function Refused({ error, name, onClose }: { error: unknown; name?: string; onClose: () => void }) {
  const conflict = isApiError(error) && error.status === 409 && !error.fields.includes("key");
  return (
    <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
      <p>{accountError(error, name)}</p>
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

/** Now → After, row by row, for an account or a reference. */
export function NowAfter({ caption, rows }: { caption: string; rows: { key: string; label: string; now: ReactNode; after: ReactNode }[] }) {
  return (
    <table className={styles.compare} data-testid="now-after">
      <caption className="visually-hidden">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">What</th>
          <th scope="col">Now</th>
          <th scope="col">
            <span className="visually-hidden">changes to</span>
          </th>
          <th scope="col">After</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className={styles.compareRow} data-direction="mixed">
            <th scope="row" className={styles.compareRule}>
              {r.label}
            </th>
            <td className={styles.compareNow}>{r.now}</td>
            <td className={styles.compareArrow} aria-hidden="true">
              <Icon as={ArrowRight} size={14} />
            </td>
            <td className={styles.compareAfter}>{r.after}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* --- create --------------------------------------------------------------------------------- */

export function CreateAccountDialog({
  open,
  onOpenChange,
  initialName = "",
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialName?: string;
  /** Instead of opening the new account's page (e.g. mapping a reference to it next). */
  onCreated?: (account: AccountDetail) => void;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Create an account"
      description="A customer account in Lattice. Creating it gives nobody access: assign it to members on their Access page."
    >
      {open && <CreateAccountForm initialName={initialName} onDone={() => onOpenChange(false)} onCreated={onCreated} />}
    </Modal>
  );
}

function CreateAccountForm({ initialName, onDone, onCreated }: { initialName: string; onDone: () => void; onCreated?: (account: AccountDetail) => void }) {
  const router = useRouter();
  const toast = useToast();
  const create = useCreateAccount();
  const [name, setName] = useState(initialName);
  const [key, setKey] = useState(keyFromName(initialName));
  const [keyEdited, setKeyEdited] = useState(false);
  const [aliases, setAliases] = useState("");
  const [status, setStatus] = useState<BusinessStatus>("active");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const taken = create.isError && isApiError(create.error) && create.error.status === 409 && create.error.fields.includes("key");
  const problems = {
    name: !name.trim() ? "Enter the account's name." : undefined,
    key: !key.trim()
      ? "Enter a key."
      : !KEY_SHAPE.test(key.trim())
        ? "Use lowercase letters, digits and hyphens, starting with a letter or digit."
        : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (field: keyof typeof problems) => (tried ? problems[field] : undefined);
  const keyError = shown("key") ?? (taken ? "Another account already uses this key." : undefined);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    const first = (
      [
        [problems.name, nameRef],
        [problems.key, keyRef],
        [problems.reason, reasonRef],
      ] as const
    ).find(([problem]) => problem);
    if (first) {
      first[1].current?.focus();
      return;
    }
    if (create.isPending) return;
    create.mutate(
      { key: key.trim(), name: name.trim(), aliases: parseAliases(aliases), status, reason: reason.trim() },
      {
        onSuccess: (account) => {
          toast({ title: `${account.name} created`, description: "Nobody is assigned it yet" });
          onDone();
          if (onCreated) onCreated(account);
          else router.push(`/access/accounts/${account.id}`);
        },
        onError: (error) => {
          if (isApiError(error) && error.status === 409) keyRef.current?.focus();
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <Field label="Name" required error={shown("name")}>
        {(control) => (
          <TextInput
            {...control}
            ref={nameRef}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!keyEdited) setKey(keyFromName(e.target.value));
            }}
            maxLength={200}
            autoComplete="off"
          />
        )}
      </Field>
      <Field
        label="Key"
        required
        error={keyError}
        help="How documents and Ask scopes name this account, e.g. acme-corp. It can't be changed later."
      >
        {(control) => (
          <TextInput
            {...control}
            ref={keyRef}
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setKeyEdited(true);
            }}
            maxLength={63}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className={styles.identifierInput}
          />
        )}
      </Field>
      <Field
        label="Other names"
        hint="optional"
        help="One per line or separated by commas. Questions that use one of these names are understood to mean this account — only by people who can see it."
      >
        {(control) => <TextArea {...control} value={aliases} onChange={(e) => setAliases(e.target.value)} rows={2} />}
      </Field>
      <Field label="Business status" help="A label for your records. It changes nothing about access.">
        {(control) => (
          <Select {...control} value={status} onChange={(e) => setStatus(e.target.value as BusinessStatus)}>
            {BUSINESS_STATUSES.map((s) => (
              <option key={s} value={s}>
                {businessStatusLabel(s)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <ReasonField value={reason} onChange={setReason} error={shown("reason")} inputRef={reasonRef} />
      {create.isError && !taken && <Refused error={create.error} onClose={onDone} />}
      {taken && (
        <p className="visually-hidden" role="alert">
          Not created: another account already uses this key.
        </p>
      )}
      <div className={styles.dialogActions}>
        <Button type="button" onClick={onDone} disabled={create.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create account
        </Button>
      </div>
    </form>
  );
}

/* --- edit ------------------------------------------------------------------------------------- */

export function EditAccountDialog({
  account,
  open,
  onOpenChange,
  onApplied,
}: {
  account: AccountDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Edit ${account.name}`} description="The key can't change: documents and Ask scopes name it.">
      {open && (
        <EditAccountForm
          account={account}
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

function EditAccountForm({ account, onDone, onApplied }: { account: AccountDetail; onDone: () => void; onApplied: (change: AppliedChange) => void }) {
  const toast = useToast();
  const update = useUpdateAccount(account.id);
  const [name, setName] = useState(account.name);
  const [aliases, setAliases] = useState(account.aliases.join("\n"));
  const [status, setStatus] = useState<BusinessStatus>(account.status);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const nextAliases = parseAliases(aliases);
  const sameAliases = nextAliases.join("\n") === account.aliases.join("\n");
  const unchanged = name.trim() === account.name && sameAliases && status === account.status;
  const problems = {
    name: !name.trim() ? "Enter the account's name." : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (field: keyof typeof problems) => (tried ? problems[field] : undefined);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (problems.name) return nameRef.current?.focus();
    if (problems.reason) return reasonRef.current?.focus();
    if (unchanged || update.isPending) return;
    update.mutate(
      {
        version: account.version,
        ...(name.trim() !== account.name ? { name: name.trim() } : {}),
        ...(!sameAliases ? { aliases: nextAliases } : {}),
        ...(status !== account.status ? { status } : {}),
        reason: reason.trim(),
      },
      {
        onSuccess: (after) => {
          toast({ title: `${after.name} updated` });
          onApplied({ title: `${after.name} updated`, detail: "Access is unchanged.", at: new Date().toISOString() });
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.secondary}>
        Key <code className={styles.claim}>{account.key}</code>
      </p>
      <Field label="Name" required error={shown("name")}>
        {(control) => <TextInput {...control} ref={nameRef} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoComplete="off" />}
      </Field>
      <Field label="Other names" hint="optional" help="One per line or separated by commas. Used only to understand questions from people who can see this account.">
        {(control) => <TextArea {...control} value={aliases} onChange={(e) => setAliases(e.target.value)} rows={3} />}
      </Field>
      <Field label="Business status" help="A label for your records. It changes nothing about access.">
        {(control) => (
          <Select {...control} value={status} onChange={(e) => setStatus(e.target.value as BusinessStatus)}>
            {BUSINESS_STATUSES.map((s) => (
              <option key={s} value={s}>
                {businessStatusLabel(s)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <ReasonField value={reason} onChange={setReason} error={shown("reason")} inputRef={reasonRef} />
      {tried && unchanged && !problems.name && (
        <p className={styles.secondary} role="status">
          Nothing has changed yet.
        </p>
      )}
      {update.isError && <Refused error={update.error} name={account.name} onClose={onDone} />}
      <div className={styles.dialogActions}>
        <Button type="button" onClick={onDone} disabled={update.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={update.isPending} disabled={unchanged}>
          Save changes
        </Button>
      </div>
    </form>
  );
}

/* --- archive / reactivate -------------------------------------------------------------------- */

export function LifecycleDialog({
  account,
  archive,
  open,
  onOpenChange,
  onApplied,
}: {
  account: AccountDetail;
  archive: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={archive ? `Archive ${account.name}` : `Reactivate ${account.name}`}>
      {open && (
        <LifecycleForm
          account={account}
          archive={archive}
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

function LifecycleForm({
  account,
  archive,
  onDone,
  onApplied,
}: {
  account: AccountDetail;
  archive: boolean;
  onDone: () => void;
  onApplied: (change: AppliedChange) => void;
}) {
  const toast = useToast();
  const change = useAccountLifecycle(account.id, archive);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const missing = !reason.trim();
  const references = account.references;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (missing) return reasonRef.current?.focus();
    if (change.isPending) return;
    change.mutate(
      { version: account.version, reason: reason.trim() },
      {
        onSuccess: () => {
          const title = archive ? `${account.name} archived` : `${account.name} reactivated`;
          toast({ title, description: "Documents and access unchanged" });
          onApplied({
            title,
            detail: archive ? "Nothing was deleted or hidden. It takes no new references." : "It takes new references again.",
            at: new Date().toISOString(),
          });
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <NowAfter
        caption="The account's lifecycle now and after this change"
        rows={[{ key: "lifecycle", label: "Lifecycle", now: archive ? "Active" : "Archived", after: archive ? "Archived" : "Active" }]}
      />
      <ul className={styles.effects} data-testid="lifecycle-effects">
        {archive ? (
          <>
            <li>Its documents keep their account, and everyone who can read them now still can.</li>
            <li>
              {references === 0
                ? "No reference maps to it."
                : `Its ${references === 1 ? "reference keeps" : `${references} references keep`} working: new records from ${references === 1 ? "it" : "them"} still arrive under this account.`}
            </li>
            <li>No new reference can be mapped to it, and its details can&apos;t be edited, until it&apos;s reactivated.</li>
          </>
        ) : (
          <>
            <li>The same account, with its history, members and references.</li>
            <li>New references can be mapped to it again, and its details edited.</li>
          </>
        )}
      </ul>
      <p className={styles.contentUnchanged}>
        <strong>Nobody&apos;s access changes.</strong> Archiving is for keeping your account list current — it never deletes or hides content.
      </p>
      <ReasonField value={reason} onChange={setReason} error={tried && missing ? "Give a reason for this change." : undefined} inputRef={reasonRef} />
      {change.isError && <Refused error={change.error} name={account.name} onClose={onDone} />}
      <div className={styles.dialogActions}>
        <Button type="button" onClick={onDone} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          {archive ? "Archive" : "Reactivate"}
        </Button>
      </div>
    </form>
  );
}
