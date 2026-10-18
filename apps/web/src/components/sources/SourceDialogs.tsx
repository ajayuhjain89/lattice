"use client";

import { AlertTriangle, Plus, Trash2, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/api/errors";
import { useCreateSource, useDisconnectPreview, useDisconnectSource, usePreviewSourceChange, useSourceLifecycle, useUpdateSource } from "@/api/queries";
import type { ConnectorType, SettingChange, SourceChangePreview, SourceChangeRequest, SourceDetail } from "@/api/types";
import { keyFromName, NowAfter, ReasonField } from "@/components/access/AccountDialogs";
import type { AppliedChange } from "@/components/access/MemberScreen";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Checkbox, ChoiceGroup, Field, Radio, Select, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { useToast } from "@/components/primitives/Toast";
import { ErrorState } from "@/components/states/ErrorState";
import { authorityLabel, formatCount, levelLabel } from "@/lib/format";

import { EXPORT_FORMATS, exportFormat, sourceError } from "./source-copy";
import styles from "./SourceRecord.module.css";

/*
 * Source administration (Phase 12, P12.6). Every rule shown here is the backend's: keys and
 * names are checked there, a settings change is previewed by the server (which says whether
 * it lets more people read records), refusals are its fixed issue codes, and a change
 * applies only with the version the page was showing and a reason. None of these dialogs
 * shows or needs any content of the source.
 */

const KEY_SHAPE = /^[a-z0-9][a-z0-9-]{0,62}$/;
const CHANNEL_SHAPE = /^[A-Z0-9]{2,32}$/;
const LEVELS = ["internal", "confidential"] as const;
const AUTHORITIES = ["system_of_record", "working", "conversational"] as const;
type DefaultLevel = (typeof LEVELS)[number];
type Authority = (typeof AUTHORITIES)[number];

/** Department keys as typed, comma- or line-separated; the API checks each exists. */
export function parseDepartments(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[\n,]/)
        .map((d) => d.trim())
        .filter(Boolean),
    ),
  ];
}

export function scopingLabel(accountScoped: boolean): string {
  return accountScoped ? "Every record must belong to an account" : "Records belong to an account only when they name one";
}

function Refused({ error, name, onClose }: { error: unknown; name?: string; onClose: () => void }) {
  const conflict = isApiError(error) && error.status === 409 && !error.fields.includes("key") && error.issues.includes("source_changed");
  return (
    <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
      <p>{sourceError(error, name)}</p>
      {conflict && (
        <p>
          <Button size="sm" onClick={onClose}>
            Close and review the latest
          </Button>
        </p>
      )}
    </Callout>
  );
}

function DialogActions({ children }: { children: ReactNode }) {
  return <div className={styles.dialogActions}>{children}</div>;
}

/* --- add a source ----------------------------------------------------------------------- */

export function AddSourceDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Add a source"
      description="An export-based source: you upload its exports to Lattice. Creating it gives nobody access to anything — each record keeps the access its export declares."
    >
      {open && <AddSourceForm onDone={() => onOpenChange(false)} />}
    </Modal>
  );
}

function AddSourceForm({ onDone }: { onDone: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const create = useCreateSource();
  const [type, setType] = useState<ConnectorType>("document_repository");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [sensitivity, setSensitivity] = useState<DefaultLevel>("internal");
  const [authority, setAuthority] = useState<Authority>(exportFormat("document_repository").authority);
  const [scoped, setScoped] = useState(exportFormat("document_repository").accountScoped);
  const [departments, setDepartments] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const taken = create.isError && isApiError(create.error) && create.error.status === 409 && create.error.fields.includes("key");
  const problems = {
    name: !name.trim() ? "Enter a name for this source." : undefined,
    key: !key.trim() ? "Enter a key." : !KEY_SHAPE.test(key.trim()) ? "Use lowercase letters, digits and hyphens, starting with a letter or digit." : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (field: keyof typeof problems) => (tried ? problems[field] : undefined);

  const choose = (next: ConnectorType) => {
    setType(next);
    const format = exportFormat(next);
    setAuthority(format.authority);
    setScoped(format.accountScoped);
  };

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
    if (first) return first[1].current?.focus();
    if (create.isPending) return;
    create.mutate(
      {
        connector_type: type,
        key: key.trim(),
        name: name.trim(),
        default_sensitivity: sensitivity,
        default_departments: parseDepartments(departments),
        authority,
        account_scoped: scoped,
        reason: reason.trim(),
      },
      {
        onSuccess: (source) => {
          toast({ title: `${source.settings.name} added`, description: "Upload an export to give it something to sync" });
          onDone();
          router.push(`/sources/${source.id}`);
        },
        onError: (error) => {
          if (isApiError(error) && error.status === 409) keyRef.current?.focus();
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <ChoiceGroup legend="Export format" help="Lattice reads these export formats only; it doesn't connect to the services themselves.">
        {EXPORT_FORMATS.map((f) => (
          <Radio key={f.type} name="source-type" value={f.type} checked={type === f.type} onChange={() => choose(f.type)} label={f.title} help={f.summary} />
        ))}
      </ChoiceGroup>
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
        error={shown("key") ?? (taken ? "Another source already uses this key." : undefined)}
        help="Identifies the source in records and the audit log, e.g. support-tickets. It can't be changed later."
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
      <Field label="Default sensitivity" help="For records whose export doesn't declare one. A record's own declaration always wins.">
        {(control) => (
          <Select {...control} value={sensitivity} onChange={(e) => setSensitivity(e.target.value as DefaultLevel)}>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {levelLabel(l)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Default departments" hint="optional" help="Department keys, separated by commas: records that name none are limited to these departments.">
        {(control) => (
          <TextInput {...control} value={departments} onChange={(e) => setDepartments(e.target.value)} autoComplete="off" spellCheck={false} className={styles.identifierInput} />
        )}
      </Field>
      <Field label="Authority" help="How answers weigh this source against others when they disagree.">
        {(control) => (
          <Select {...control} value={authority} onChange={(e) => setAuthority(e.target.value as Authority)}>
            {AUTHORITIES.map((a) => (
              <option key={a} value={a}>
                {authorityLabel(a)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Checkbox
        checked={scoped}
        onChange={(e) => setScoped(e.target.checked)}
        label="Every record must belong to an account"
        help="Records without a resolvable account are held back, never indexed without one."
      />
      <ReasonField value={reason} onChange={setReason} error={shown("reason")} inputRef={reasonRef} />
      {create.isError && !taken && <Refused error={create.error} onClose={onDone} />}
      {taken && (
        <p className="visually-hidden" role="alert">
          Not added: another source already uses this key.
        </p>
      )}
      <DialogActions>
        <Button type="button" onClick={onDone} disabled={create.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Add source
        </Button>
      </DialogActions>
    </form>
  );
}

/* --- edit settings (preview first) ------------------------------------------------------ */

interface ChannelRow {
  id: string;
  level: DefaultLevel;
}

export function EditSourceDialog({
  source,
  open,
  onOpenChange,
  onApplied,
}: {
  source: SourceDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={`Edit ${source.settings.name}`}
      description="Changes apply at the next sync. The key and the export format can't change."
    >
      {open && (
        <EditSourceForm
          source={source}
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

const EFFECT_WORD: Record<SettingChange["effect"], string> = { narrows: "Fewer people can read", widens: "More people can read", neutral: "Who can read is unchanged" };

function fieldLabel(field: string): string {
  return (
    {
      name: "Name",
      default_sensitivity: "Default sensitivity",
      default_departments: "Default departments",
      authority: "Authority",
      account_scoped: "Account scoping",
      channel_sensitivities: "Channel sensitivities",
    }[field] ?? field
  );
}

/** A previewed value in words (the server sends codes, "yes"/"no" and comma lists). */
export function settingValue(field: string, value: string | null): string {
  if (value === null) return "—";
  if (field === "account_scoped") return value === "yes" ? "Every record belongs to an account" : "Records name their account when they have one";
  if (field === "default_sensitivity") return levelLabel(value);
  if (field === "authority") return authorityLabel(value);
  if (value === "none") return "None";
  return value;
}

function EditSourceForm({ source, onDone, onApplied }: { source: SourceDetail; onDone: () => void; onApplied: (change: AppliedChange) => void }) {
  const toast = useToast();
  const preview = usePreviewSourceChange(source.id);
  const update = useUpdateSource(source.id);
  const settings = source.settings;
  const chat = source.connector_type === "chat_export";
  const initialChannels: ChannelRow[] = Object.entries(settings.channel_sensitivities).map(([id, level]) => ({ id, level: level as DefaultLevel }));
  const [name, setName] = useState(settings.name);
  const [sensitivity, setSensitivity] = useState(settings.default_sensitivity as DefaultLevel);
  const [departments, setDepartments] = useState(settings.default_departments.map((d) => d.key).join(", "));
  const [authority, setAuthority] = useState(settings.authority as Authority);
  const [scoped, setScoped] = useState(settings.account_scoped);
  const [channels, setChannels] = useState<ChannelRow[]>(initialChannels);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [reviewed, setReviewed] = useState<{ body: SourceChangeRequest; result: SourceChangePreview } | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const reviewRef = useRef<HTMLHeadingElement>(null);

  const nextDepartments = parseDepartments(departments);
  const sameDepartments = nextDepartments.join(",") === settings.default_departments.map((d) => d.key).join(",");
  const channelMap = Object.fromEntries(channels.filter((c) => c.id.trim()).map((c) => [c.id.trim(), c.level]));
  const sameChannels = JSON.stringify(channelMap) === JSON.stringify(settings.channel_sensitivities);
  const badChannel = channels.some((c) => c.id.trim() && !CHANNEL_SHAPE.test(c.id.trim()));
  const body: SourceChangeRequest = {
    version: source.version,
    ...(name.trim() !== settings.name ? { name: name.trim() } : {}),
    ...(sensitivity !== settings.default_sensitivity ? { default_sensitivity: sensitivity } : {}),
    ...(!sameDepartments ? { default_departments: nextDepartments } : {}),
    ...(authority !== settings.authority ? { authority } : {}),
    ...(scoped !== settings.account_scoped ? { account_scoped: scoped } : {}),
    ...(chat && !sameChannels ? { channel_sensitivities: channelMap } : {}),
    reason: reason.trim(),
  };
  const unchanged = Object.keys(body).length === 2;
  const problems = {
    name: !name.trim() ? "Enter a name for this source." : undefined,
    channels: badChannel ? "Channel IDs are uppercase letters and digits, as the export names them (e.g. C04ABCD12)." : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (field: keyof typeof problems) => (tried ? problems[field] : undefined);

  const review = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (problems.name) return nameRef.current?.focus();
    if (problems.channels) return;
    if (problems.reason) return reasonRef.current?.focus();
    if (unchanged || preview.isPending) return;
    preview.mutate(body, {
      onSuccess: (result) => {
        setAcknowledged(false);
        setReviewed({ body, result });
        requestAnimationFrame(() => reviewRef.current?.focus());
      },
    });
  };

  const apply = () => {
    if (!reviewed || update.isPending) return;
    if (reviewed.result.widens && !acknowledged) return;
    update.mutate(
      { ...reviewed.body, acknowledge_widening: reviewed.result.widens },
      {
        onSuccess: (after) => {
          toast({ title: `${after.settings.name} updated`, description: "Applies at the next sync" });
          onApplied({
            title: `${after.settings.name} updated`,
            detail: reviewed.result.widens
              ? "At the next sync, more people can read some of its records — exactly as the review showed."
              : "Applies at the next sync. Nothing changed for anyone yet.",
            at: new Date().toISOString(),
          });
        },
      },
    );
  };

  if (reviewed) {
    const { result } = reviewed;
    return (
      <div className={styles.form}>
        <h3 className={styles.reviewHeading} ref={reviewRef} tabIndex={-1}>
          Review the change
        </h3>
        <NowAfter
          caption="This source's settings now and after the change"
          rows={result.changes.map((c) => ({
            key: c.field,
            label: fieldLabel(c.field),
            now: settingValue(c.field, c.before),
            after: (
              <span className={styles.stack}>
                <span>{settingValue(c.field, c.after)}</span>
                <span className={c.effect === "widens" ? styles.widens : styles.secondary}>{EFFECT_WORD[c.effect]}</span>
              </span>
            ),
          }))}
        />
        {result.widens ? (
          <Callout tone="boundary" label="More people can read records after this" icon={<Icon as={AlertTriangle} />}>
            <p>
              At the next sync, some of this source&apos;s records become readable to people who can&apos;t read them now. Records keep any stricter access their
              export declares.
            </p>
            <Checkbox
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              label="I've reviewed who gains access, and this is intended"
            />
          </Callout>
        ) : (
          <p className={styles.contentUnchanged}>
            <strong>Nobody gains access.</strong> Changes apply at the next sync.
          </p>
        )}
        {update.isError && <Refused error={update.error} name={settings.name} onClose={onDone} />}
        <DialogActions>
          <Button type="button" onClick={() => setReviewed(null)} disabled={update.isPending}>
            Back
          </Button>
          <Button type="button" variant="primary" loading={update.isPending} aria-disabled={(result.widens && !acknowledged) || undefined} onClick={apply}>
            Apply change
          </Button>
        </DialogActions>
      </div>
    );
  }

  return (
    <form className={styles.form} onSubmit={review} noValidate>
      <p className={styles.secondary}>
        Key <code className={styles.identifier}>{source.key}</code> · {exportFormat(source.connector_type).title}
      </p>
      <Field label="Name" required error={shown("name")}>
        {(control) => <TextInput {...control} ref={nameRef} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoComplete="off" />}
      </Field>
      <Field label="Default sensitivity" help="For records whose export doesn't declare one.">
        {(control) => (
          <Select {...control} value={sensitivity} onChange={(e) => setSensitivity(e.target.value as DefaultLevel)}>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {levelLabel(l)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Default departments" hint="optional" help="Department keys, separated by commas.">
        {(control) => (
          <TextInput {...control} value={departments} onChange={(e) => setDepartments(e.target.value)} autoComplete="off" spellCheck={false} className={styles.identifierInput} />
        )}
      </Field>
      <Field label="Authority">
        {(control) => (
          <Select {...control} value={authority} onChange={(e) => setAuthority(e.target.value as Authority)}>
            {AUTHORITIES.map((a) => (
              <option key={a} value={a}>
                {authorityLabel(a)}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Checkbox
        checked={scoped}
        onChange={(e) => setScoped(e.target.checked)}
        label="Every record must belong to an account"
        help="Records without a resolvable account are held back, never indexed without one."
      />
      {chat && (
        <ChoiceGroup
          legend="Channel sensitivities"
          help="By channel ID, as the export names it. A channel listed here is read at that level; others use the default."
          error={shown("channels")}
        >
          <ul className={styles.channels}>
            {channels.map((c, i) => (
              <li key={i} className={styles.channel}>
                <Field label={`Channel ID ${i + 1}`} hideLabel>
                  {(control) => (
                    <TextInput
                      {...control}
                      value={c.id}
                      onChange={(e) => setChannels(channels.map((x, j) => (j === i ? { ...x, id: e.target.value.toUpperCase() } : x)))}
                      maxLength={32}
                      autoComplete="off"
                      spellCheck={false}
                      className={styles.identifierInput}
                      placeholder="C04ABCD12"
                    />
                  )}
                </Field>
                <Field label={`Sensitivity of channel ${i + 1}`} hideLabel>
                  {(control) => (
                    <Select {...control} value={c.level} onChange={(e) => setChannels(channels.map((x, j) => (j === i ? { ...x, level: e.target.value as DefaultLevel } : x)))}>
                      {LEVELS.map((l) => (
                        <option key={l} value={l}>
                          {levelLabel(l)}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                <Button
                  type="button"
                  size="sm"
                  variant="quiet"
                  icon={<Icon as={X} />}
                  aria-label={`Remove channel ${c.id || i + 1}`}
                  onClick={() => setChannels(channels.filter((_, j) => j !== i))}
                />
              </li>
            ))}
          </ul>
          <p>
            <Button type="button" size="sm" icon={<Icon as={Plus} />} onClick={() => setChannels([...channels, { id: "", level: "confidential" }])}>
              Add a channel
            </Button>
          </p>
        </ChoiceGroup>
      )}
      <ReasonField value={reason} onChange={setReason} error={shown("reason")} inputRef={reasonRef} />
      {tried && unchanged && !problems.name && (
        <p className={styles.secondary} role="status">
          Nothing has changed yet.
        </p>
      )}
      {preview.isError && <Refused error={preview.error} name={settings.name} onClose={onDone} />}
      <DialogActions>
        <Button type="button" onClick={onDone} disabled={preview.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={preview.isPending} disabled={unchanged}>
          Review change
        </Button>
      </DialogActions>
    </form>
  );
}

/* --- pause / resume ------------------------------------------------------------------- */

export function LifecycleDialog({
  source,
  pause,
  open,
  onOpenChange,
  onApplied,
}: {
  source: SourceDetail;
  pause: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (change: AppliedChange) => void;
}) {
  const name = source.settings.name;
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={pause ? `Pause ${name}` : `Resume ${name}`}>
      {open && (
        <LifecycleForm
          source={source}
          pause={pause}
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

function LifecycleForm({ source, pause, onDone, onApplied }: { source: SourceDetail; pause: boolean; onDone: () => void; onApplied: (change: AppliedChange) => void }) {
  const toast = useToast();
  const change = useSourceLifecycle(source.id, pause);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const missing = !reason.trim();
  const name = source.settings.name;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (missing) return reasonRef.current?.focus();
    if (change.isPending) return;
    change.mutate(
      { version: source.version, reason: reason.trim() },
      {
        onSuccess: () => {
          const title = pause ? `${name} paused` : `${name} resumed`;
          toast({ title, description: "Its documents are unchanged" });
          onApplied({
            title,
            detail: pause ? "Nothing syncs until it's resumed. Its documents stay searchable as before." : "Syncs can be requested again. Resuming didn't start one.",
            at: new Date().toISOString(),
          });
        },
      },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <NowAfter caption="The source's state now and after this change" rows={[{ key: "state", label: "State", now: pause ? "Active" : "Paused", after: pause ? "Paused" : "Active" }]} />
      <ul className={styles.effects} data-testid="lifecycle-effects">
        {pause ? (
          <>
            <li>No sync can be requested, and a sync already queued won&apos;t run.</li>
            <li>Its documents stay searchable, for exactly the people who can read them now.</li>
            <li>Exports can still be uploaded and checked; nothing from them is indexed until it&apos;s resumed and synced.</li>
          </>
        ) : (
          <>
            <li>Syncs can be requested again.</li>
            <li>Resuming doesn&apos;t start a sync: start one when you&apos;re ready.</li>
          </>
        )}
      </ul>
      <ReasonField value={reason} onChange={setReason} error={tried && missing ? "Give a reason for this change." : undefined} inputRef={reasonRef} />
      {change.isError && <Refused error={change.error} name={name} onClose={onDone} />}
      <DialogActions>
        <Button type="button" onClick={onDone} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          {pause ? "Pause" : "Resume"}
        </Button>
      </DialogActions>
    </form>
  );
}

/* --- disconnect (preview, typed key, reason) ---------------------------------------------- */

export function DisconnectDialog({ source, open, onOpenChange, onDisconnected }: { source: SourceDetail; open: boolean; onOpenChange: (open: boolean) => void; onDisconnected: () => void }) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Disconnect ${source.settings.name}`} description="Disconnecting deletes this source and everything Lattice holds from it. It can't be undone.">
      {open && <DisconnectForm source={source} onDone={() => onOpenChange(false)} onDisconnected={onDisconnected} />}
    </Modal>
  );
}

function DisconnectForm({ source, onDone, onDisconnected }: { source: SourceDetail; onDone: () => void; onDisconnected: () => void }) {
  const toast = useToast();
  const preview = useDisconnectPreview(source.id, true);
  const disconnect = useDisconnectSource(source.id);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const keyRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const name = source.settings.name;
  const problems = {
    key: typed.trim() !== source.key ? `Type ${source.key} exactly to confirm.` : undefined,
    reason: !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined,
  };
  const shown = (field: keyof typeof problems) => (tried ? problems[field] : undefined);
  const running = preview.data?.sync_running ?? false;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (problems.key) return keyRef.current?.focus();
    if (problems.reason) return reasonRef.current?.focus();
    if (running || disconnect.isPending) return;
    disconnect.mutate(
      { version: source.version, confirm_key: typed.trim(), reason: reason.trim() },
      {
        onSuccess: (result) => {
          toast({ title: `${name} disconnected`, description: `${formatCount(result.documents, "document", "documents")} deleted` });
          onDisconnected();
        },
      },
    );
  };

  if (preview.isError) {
    return <ErrorState error={preview.error} title="Couldn't load what disconnecting would delete" onRetry={() => preview.refetch()} retrying={preview.isFetching} headingLevel="h3" />;
  }
  if (!preview.data) return <RuleSkeleton variant="ledger" rows={4} label="Loading what disconnecting would delete" />;
  const p = preview.data;

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <div className={styles.consequences} data-testid="disconnect-preview">
        <p className={styles.countsLabel}>Deleted from Lattice</p>
        <ul className={styles.effects}>
          <li>
            <strong>{formatCount(p.documents, "document", "documents")}</strong> stop being readable at once, then are deleted with their{" "}
            {formatCount(p.chunks, "indexed passage", "indexed passages")}.
          </li>
          <li>
            <strong>{formatCount(p.account_mappings, "account reference mapping", "account reference mappings")}</strong> of this source{" "}
            {p.account_mappings === 1 ? "is" : "are"} removed. The accounts themselves stay.
          </li>
          <li>
            <strong>{formatCount(p.imports, "uploaded export", "uploaded exports")}</strong> {p.imports === 1 ? "is" : "are"} deleted.
          </li>
          <li>
            <strong>{formatCount(p.queued_jobs, "queued check or sync", "queued checks and syncs")}</strong> {p.queued_jobs === 1 ? "is" : "are"} cancelled.
          </li>
        </ul>
        <p className={styles.countsLabel}>Kept</p>
        <ul className={styles.effects}>
          <li>Accounts, members, other sources and the audit log.</li>
          <li>Past answers&apos; traces: they are re-checked on every read, so nobody sees this source&apos;s content through them.</li>
        </ul>
      </div>
      {p.operator_managed && (
        <p className={styles.contentUnchanged}>
          <strong>An operator&apos;s sources file configures this source.</strong> Running that file again connects it again, as a new source.
        </p>
      )}
      {running && (
        <Callout tone="time" label="A sync is running" role="status">
          <p>Disconnect when it finishes. Queued work is cancelled when you disconnect.</p>
        </Callout>
      )}
      <Field label={`Type ${source.key} to confirm`} required error={shown("key")}>
        {(control) => (
          <TextInput
            {...control}
            ref={keyRef}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className={styles.identifierInput}
          />
        )}
      </Field>
      <ReasonField value={reason} onChange={setReason} error={shown("reason")} inputRef={reasonRef} />
      {disconnect.isError && <Refused error={disconnect.error} name={name} onClose={onDone} />}
      <DialogActions>
        <Button type="button" onClick={onDone} disabled={disconnect.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="danger" icon={<Icon as={Trash2} />} loading={disconnect.isPending} aria-disabled={running || undefined}>
          Disconnect and delete
        </Button>
      </DialogActions>
    </form>
  );
}
