"use client";

import { Plus } from "lucide-react";
import { useDeferredValue, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { useAccountsList, useAdminSources, useChangeMapping, usePreviewMapping, type MappingChange } from "@/api/queries";
import type { AccountRef, MappingPreview, RefKind, SourceRef } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { ChoiceGroup, Field, Radio, SearchInput, Select, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { useToast } from "@/components/primitives/Toast";
import { connectorLabel } from "@/lib/format";

import styles from "./Access.module.css";
import { NowAfter, ReasonField, Refused } from "./AccountDialogs";
import { accountError, accountLabel, mappingEffect, refKindFor, refKindLabel } from "./accountsCopy";

/*
 * Mapping one external reference to a canonical account (Phase 12, P12.3). Map, remap and
 * unmap are separate operations; each is previewed by the API as Now → After before a
 * reason is asked for, and each applies at the source's next sync -- nothing indexed
 * changes before. No account is ever suggested: the administrator picks one, or creates
 * one explicitly. A reference is shown exactly as the source sent it (an ID, never a
 * channel name).
 */

export interface Reference {
  source: SourceRef;
  ref_kind: RefKind;
  external_ref: string;
}

export type MappingTarget =
  | { mode: "map"; reference: Reference; account?: AccountRef }
  | { mode: "add"; account: AccountRef }
  | { mode: "remap"; reference: Reference; current: AccountRef }
  | { mode: "unmap"; reference: Reference; current: AccountRef };

export interface AppliedMapping {
  title: string;
  detail: string;
  at: string;
  source: SourceRef;
}

const TITLES: Record<MappingTarget["mode"], string> = {
  map: "Map a reference to an account",
  add: "Add a reference",
  remap: "Move a reference to another account",
  unmap: "Remove a reference's mapping",
};

export function MappingDialog({
  target,
  onOpenChange,
  onApplied,
  onCreateAccount,
}: {
  target: MappingTarget | null;
  onOpenChange: (open: boolean) => void;
  onApplied: (applied: AppliedMapping) => void;
  /** "map" only: create a new account instead (this dialog closes). */
  onCreateAccount?: () => void;
}) {
  return (
    <Modal
      open={target !== null}
      onOpenChange={onOpenChange}
      title={target ? TITLES[target.mode] : ""}
      description="Takes effect at the source's next sync. Until then, indexed records keep their account."
    >
      {target && (
        <MappingForm
          target={target}
          onDone={() => onOpenChange(false)}
          onApplied={(applied) => {
            onApplied(applied);
            onOpenChange(false);
          }}
          onCreateAccount={onCreateAccount}
        />
      )}
    </Modal>
  );
}

function MappingForm({
  target,
  onDone,
  onApplied,
  onCreateAccount,
}: {
  target: MappingTarget;
  onDone: () => void;
  onApplied: (applied: AppliedMapping) => void;
  onCreateAccount?: () => void;
}) {
  const toast = useToast();
  const preview = usePreviewMapping();
  const apply = useChangeMapping();
  const fixed = target.mode === "add" ? null : target.reference;
  const [sourceId, setSourceId] = useState("");
  const [externalRef, setExternalRef] = useState("");
  const [account, setAccount] = useState<AccountRef | null>(target.mode === "map" ? (target.account ?? null) : null);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [reviewed, setReviewed] = useState<string | null>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const refInput = useRef<HTMLInputElement>(null);
  const sources = useAdminSources(target.mode === "add");
  const mappable = (sources.data?.sources ?? []).filter((s) => refKindFor(s.connector_type) !== null && s.status !== "deleting");
  const chosenSource = mappable.find((s) => s.id === sourceId);

  // What the change is, from the inputs: the reference, and the account after it.
  const reference: Reference | null = fixed
    ? fixed
    : chosenSource && externalRef.trim()
      ? {
          source: { id: chosenSource.id, name: chosenSource.name, connector_type: chosenSource.connector_type },
          ref_kind: refKindFor(chosenSource.connector_type) as RefKind,
          external_ref: externalRef.trim(),
        }
      : null;
  const after: AccountRef | null = target.mode === "add" ? target.account : target.mode === "unmap" ? null : account;
  const ready = reference !== null && (target.mode === "unmap" || after !== null);
  const draft = ready ? JSON.stringify([reference.source.id, reference.ref_kind, reference.external_ref, after?.id ?? null]) : null;
  const shown: MappingPreview | undefined = draft !== null && draft === reviewed ? preview.data : undefined;
  // The review replaces the "Review change" button: move focus to it, not to the page.
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (shown) heading.current?.focus();
  }, [shown]);

  const review = () => {
    if (!ready || !reference) return;
    setReviewed(draft);
    preview.mutate({ source_id: reference.source.id, ref_kind: reference.ref_kind, external_ref: reference.external_ref, account_id: after?.id ?? null });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (!shown || !reference) return review();
    if (!reason.trim()) return reasonRef.current?.focus();
    if (apply.isPending) return;
    const key = { source_id: reference.source.id, ref_kind: reference.ref_kind, external_ref: reference.external_ref };
    const change: MappingChange =
      shown.operation === "map"
        ? { operation: "map", body: { ...key, account_id: shown.after!.id, reason: reason.trim() } }
        : shown.operation === "remap"
          ? { operation: "remap", body: { ...key, current_account_id: shown.now!.id, account_id: shown.after!.id, reason: reason.trim() } }
          : { operation: "unmap", body: { ...key, current_account_id: shown.now!.id, reason: reason.trim() } };
    apply.mutate(change, {
      onSuccess: () => {
        const title =
          shown.operation === "unmap"
            ? `${refKindLabel(reference.ref_kind)} ${reference.external_ref} unmapped`
            : `${refKindLabel(reference.ref_kind)} ${reference.external_ref} → ${shown.after!.name}`;
        toast({ title, description: `Applies at the next sync of ${reference.source.name}` });
        onApplied({ title, detail: mappingEffect(shown), at: new Date().toISOString(), source: reference.source });
      },
    });
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      {fixed ? (
        <ReferenceFacts reference={fixed} current={target.mode === "remap" || target.mode === "unmap" ? target.current : null} />
      ) : (
        <>
          {target.mode === "add" && (
            <p className={styles.trust}>
              <span>
                Records from this reference will belong to <strong>{target.account.name}</strong>. Enter it exactly as the source sends it — Lattice matches it
                character for character and never guesses.
              </span>
            </p>
          )}
          {sources.isError ? (
            <Callout tone="fault" label="Couldn't load your sources" role="alert">
              <p>Close this and try again.</p>
            </Callout>
          ) : (
            <Field label="Source" required help="Only ticketing and chat exports send account references. Documents name their account themselves.">
              {(control) => (
                <Select {...control} value={sourceId} onChange={(e) => setSourceId(e.target.value)} disabled={!sources.data}>
                  <option value="">{sources.data ? "Choose a source" : "Loading sources…"}</option>
                  {mappable.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} — {connectorLabel(s.connector_type)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          <Field
            label={chosenSource ? refKindLabel(refKindFor(chosenSource.connector_type) as RefKind) : "Reference"}
            required
            help={
              chosenSource?.connector_type === "ticketing"
                ? "The organization ID your ticketing tool puts on each ticket: digits only."
                : chosenSource?.connector_type === "chat_export"
                  ? "The channel's ID from the export, e.g. C0123ABCD — not its name."
                  : "Choose a source first."
            }
          >
            {(control) => (
              <TextInput
                {...control}
                ref={refInput}
                value={externalRef}
                onChange={(e) => setExternalRef(e.target.value)}
                maxLength={256}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                disabled={!chosenSource}
                className={styles.identifierInput}
              />
            )}
          </Field>
        </>
      )}

      {(target.mode === "map" || target.mode === "remap") && (
        <AccountPicker
          label={target.mode === "remap" ? "Move to" : "Account"}
          selected={account}
          exclude={target.mode === "remap" ? target.current.id : undefined}
          onSelect={setAccount}
          onCreate={target.mode === "map" ? onCreateAccount : undefined}
        />
      )}

      {!shown && (
        <div className={styles.actions}>
          <Button type="button" variant={ready ? "primary" : "secondary"} onClick={review} disabled={!ready} loading={preview.isPending}>
            Review change
          </Button>
          {!ready && tried && <p className={styles.note}>Complete the fields above first.</p>}
        </div>
      )}
      {preview.isError && draft === reviewed && (
        <Callout tone="fault" label="Couldn't preview this change" role="alert">
          <p>{accountError(preview.error)}</p>
        </Callout>
      )}

      {shown && reference && (
        <>
          <h3 ref={heading} tabIndex={-1} className={styles.reviewHeading}>
            What will change
          </h3>
          <NowAfter
            caption="The reference's account now and after this change"
            rows={[
              {
                key: "account",
                label: `${refKindLabel(shown.ref_kind)} ${shown.external_ref}`,
                now: shown.now ? accountLabel(shown.now) : <span className={styles.secondary}>Not mapped</span>,
                after: shown.after ? accountLabel(shown.after) : <span className={styles.secondary}>Not mapped</span>,
              },
            ]}
          />
          <ul className={styles.effects} data-testid="mapping-effect">
            <li>
              <strong>At the next sync of {shown.source.name}:</strong> {mappingEffect(shown)}
            </li>
            <li className={styles.secondary}>Nothing indexed changes before that sync. You can start it from Sources, or right after applying.</li>
          </ul>
          {shown.effect === "quarantined" && (
            <Callout tone="neutral" label="These records stay unreadable until mapped again" labelAs="p">
              <p>
                {shown.source.name} keeps {refKindLabel(shown.ref_kind).toLowerCase()} {shown.external_ref} as an unresolved reference. Its records are quarantined, not given to a
                wider audience; they appear under Unmapped references after the sync, and mapping the reference again restores them.
              </p>
            </Callout>
          )}
          <ReasonField value={reason} onChange={setReason} error={tried && !reason.trim() ? "Give a reason for this change." : undefined} inputRef={reasonRef} />
        </>
      )}
      {apply.isError && <Refused error={apply.error} onClose={onDone} />}
      <div className={styles.dialogActions}>
        <Button type="button" onClick={onDone} disabled={apply.isPending}>
          Cancel
        </Button>
        {shown && (
          <Button type="submit" variant="primary" loading={apply.isPending}>
            {shown.operation === "map" ? "Map reference" : shown.operation === "remap" ? "Move reference" : "Remove mapping"}
          </Button>
        )}
      </div>
    </form>
  );
}

function ReferenceFacts({ reference, current }: { reference: Reference; current: AccountRef | null }) {
  return (
    <dl className={styles.reach} data-testid="reference-facts">
      <div className={styles.reachRow}>
        <dt className={styles.rule}>Source</dt>
        <dd className={styles.value}>
          {reference.source.name}
          <span className={styles.secondary}> · {connectorLabel(reference.source.connector_type)}</span>
        </dd>
      </div>
      <div className={styles.reachRow}>
        <dt className={styles.rule}>{refKindLabel(reference.ref_kind)}</dt>
        <dd className={styles.value}>
          <code className={styles.claim}>{reference.external_ref}</code>
        </dd>
      </div>
      {current && (
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Mapped to</dt>
          <dd className={styles.value}>{accountLabel(current)}</dd>
        </div>
      )}
    </dl>
  );
}

/** Pick an active account by name, key or other name (the API searches; archived
 *  accounts take no new references and are not offered). */
function AccountPicker({
  label,
  selected,
  exclude,
  onSelect,
  onCreate,
}: {
  label: string;
  selected: AccountRef | null;
  exclude?: string;
  onSelect: (account: AccountRef) => void;
  onCreate?: () => void;
}) {
  const [text, setText] = useState("");
  const q = useDeferredValue(text.trim());
  const query = useAccountsList(q, "active", true);
  const id = useId();
  const items = (query.data?.pages.flatMap((p) => p.items) ?? []).filter((a) => a.id !== exclude).slice(0, 8);
  return (
    <div className={styles.docSearch}>
      <Field label={label} required help="Find it by name, key or another name. Archived accounts take no new references.">
        {(control) => <SearchInput {...control} value={text} onValueChange={setText} placeholder="Name or key" maxLength={100} clearLabel="Clear account search" />}
      </Field>
      {query.isError ? (
        <p className={styles.problem} role="alert">
          Couldn&apos;t search accounts. Try again.
        </p>
      ) : !query.data ? (
        <p className={styles.secondary} role="status">
          Loading accounts…
        </p>
      ) : items.length === 0 ? (
        <p className={styles.secondary} role="status">
          {q ? `No active account matches “${q}”.` : "No other active account."}
        </p>
      ) : (
        <ChoiceGroup legend={label} hideLegend>
          <div className={styles.results}>
            {items.map((a) => (
              <Radio
                key={a.id}
                name={`${id}-account`}
                checked={selected?.id === a.id}
                onChange={() => onSelect({ id: a.id, key: a.key, name: a.name, lifecycle: a.lifecycle })}
                label={
                  <span className={styles.doc}>
                    <span className={styles.docTitle}>{a.name}</span>
                    <span className={styles.docMeta}>
                      <span className={styles.ref}>{a.key}</span>
                      {a.aliases.length > 0 && <span>Also {a.aliases.join(", ")}</span>}
                    </span>
                  </span>
                }
              />
            ))}
          </div>
        </ChoiceGroup>
      )}
      {selected && !items.some((a) => a.id === selected.id) && (
        <p className={styles.secondary}>
          Selected: <strong>{selected.name}</strong>
        </p>
      )}
      {onCreate && (
        <p>
          <Button type="button" size="sm" variant="quiet" icon={<Icon as={Plus} />} onClick={onCreate}>
            Create a new account instead
          </Button>
        </p>
      )}
    </div>
  );
}
