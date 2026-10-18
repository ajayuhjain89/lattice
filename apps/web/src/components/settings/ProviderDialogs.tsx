"use client";

import { ArrowUpRight, RotateCw, ShieldAlert } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/api/errors";
import { useChangeProviders, usePreviewEgressChange, useStartStepUp, useStepUp, type ProviderMutation } from "@/api/queries";
import type { ChangePreview, EgressChange, ProviderOut, ProviderSettings, StepUp } from "@/api/types";
import { NowAfter, ReasonField } from "@/components/access/AccountDialogs";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { ChoiceGroup, Field, Radio, Select, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { LEVELS } from "@/components/primitives/LevelMeter";
import { Modal } from "@/components/primitives/Modal";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StatusMark } from "@/components/primitives/StatusMark";
import { formatTime, levelLabel } from "@/lib/format";

import { ceilingWords, providerError, providerName, remainingSentence, STEP_UP_OUTCOME, transitionSentence, trustClassName } from "./provider-copy";
import styles from "./ProvidersScreen.module.css";

/*
 * Provider and egress changes (Phase 12, P12.5). Every policy change is previewed by the
 * API -- Now → After, and whether it raises what may leave Lattice. A change that only
 * lowers applies with a reason. A raise needs a reason, the organization's name typed,
 * and a fresh sign-in at the identity provider: the browser leaves for it and comes back
 * to this page with `?step_up=<id>`; the change, its reason and the policy version it was
 * previewed on wait on the server, never in the browser. API keys are typed into masked
 * fields that never start filled, sent once, and cleared from the form when sent.
 */

export type ProviderDialog =
  | { kind: "policy" }
  | { kind: "add" }
  | { kind: "approval"; provider: ProviderOut }
  | { kind: "enabled"; provider: ProviderOut; enabled: boolean }
  | { kind: "data_terms"; provider: ProviderOut }
  | { kind: "endpoint"; provider: ProviderOut }
  | { kind: "credential"; provider: ProviderOut }
  | { kind: "revoke"; provider: ProviderOut };

export interface Applied {
  title: string;
  detail: string;
  at: string;
}

function title(dialog: ProviderDialog): string {
  switch (dialog.kind) {
    case "policy":
      return "Change the AI data policy";
    case "add":
      return "Add a generation provider";
    case "approval":
      return `Approval of ${providerName(dialog.provider)}`;
    case "enabled":
      return `${dialog.enabled ? "Enable" : "Disable"} ${providerName(dialog.provider)}`;
    case "data_terms":
      return `Data terms of ${providerName(dialog.provider)}`;
    case "endpoint":
      return `Move ${providerName(dialog.provider)} to another server`;
    case "credential":
      return dialog.provider.credential.state === "active" ? `Replace the API key of ${dialog.provider.provider}` : `Add an API key for ${dialog.provider.provider}`;
    case "revoke":
      return `Revoke the API key of ${dialog.provider.provider}`;
  }
}

export function ProviderDialogs({
  dialog,
  settings,
  onClose,
  onApplied,
}: {
  dialog: ProviderDialog | null;
  settings: ProviderSettings;
  onClose: () => void;
  onApplied: (applied: Applied) => void;
}) {
  // A typed key leaves memory with its dialog: the form is unmounted on close.
  return (
    <Modal open={dialog !== null} onOpenChange={(open) => !open && onClose()} title={dialog ? title(dialog) : ""}>
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
  dialog: ProviderDialog;
  settings: ProviderSettings;
  onClose: () => void;
  onApplied: (applied: Applied) => void;
}) {
  const applied = (title: string, detail: string) => {
    onApplied({ title, detail, at: new Date().toISOString() });
    onClose();
  };
  switch (dialog.kind) {
    case "policy":
      return <PolicyForm settings={settings} onClose={onClose} onApplied={applied} />;
    case "add":
      return <AddProviderForm settings={settings} onClose={onClose} onApplied={applied} />;
    case "approval":
      return <ApprovalForm provider={dialog.provider} settings={settings} onClose={onClose} onApplied={applied} />;
    case "enabled":
      return (
        <EgressReview
          settings={settings}
          change={{ kind: "provider", provider_id: dialog.provider.id, enabled: dialog.enabled }}
          rows={[{ key: "enabled", label: "Status", now: dialog.provider.enabled ? "Enabled" : "Disabled", after: dialog.enabled ? "Enabled" : "Disabled" }]}
          appliedTitle={dialog.enabled ? "Provider enabled" : "Provider disabled"}
          onClose={onClose}
          onApplied={applied}
        />
      );
    case "data_terms":
      return <DataTermsForm provider={dialog.provider} settings={settings} onClose={onClose} onApplied={applied} />;
    case "endpoint":
      return <EndpointForm provider={dialog.provider} settings={settings} onClose={onClose} onApplied={applied} />;
    case "credential":
      return <CredentialForm provider={dialog.provider} onClose={onClose} onApplied={applied} />;
    case "revoke":
      return <RevokeForm provider={dialog.provider} onClose={onClose} onApplied={applied} />;
  }
}

/* --- shared ------------------------------------------------------------------------------------ */

function useReason() {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const error = tried && !reason.trim() ? "Give a reason for this change." : undefined;
  return { reason: reason.trim(), value: reason, setReason, tried, setTried, error, ref };
}

function Refused({ error, onClose }: { error: unknown; onClose: () => void }) {
  const conflict = isApiError(error) && error.status === 409;
  return (
    <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not applied"} role="alert">
      <p>{providerError(error)}</p>
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

function useMutate(onApplied: (title: string, detail: string) => void) {
  const change = useChangeProviders();
  const run = (m: ProviderMutation, title: string, detail: string) => change.mutate(m, { onSuccess: () => onApplied(title, detail) });
  return { change, run };
}

/* --- Now → After, and the step-up --------------------------------------------------------------- */

/** The server's reading of a change: what newly may (and may no longer) leave Lattice. */
export function PreviewReading({ preview }: { preview: ChangePreview }) {
  const remaining = preview.remaining.map(remainingSentence).filter((s): s is string => s !== null);
  const defaults = preview.default_query_sensitivity;
  return (
    <div className={styles.reading} data-testid="egress-preview" data-escalation={preview.escalation}>
      {preview.newly_allowed.length > 0 && (
        <div>
          <h4 className={styles.readingTitle}>Newly allowed to leave Lattice</h4>
          <ul className={styles.effects} data-testid="newly-allowed">
            {preview.newly_allowed.map((t) => (
              <li key={`${t.provider_id}|${t.level}`}>{transitionSentence(t)}</li>
            ))}
          </ul>
        </div>
      )}
      {preview.no_longer_allowed.length > 0 && (
        <div>
          <h4 className={styles.readingTitle}>No longer allowed</h4>
          <ul className={styles.effects} data-testid="no-longer-allowed">
            {preview.no_longer_allowed.map((t) => (
              <li key={`${t.provider_id}|${t.level}`}>{transitionSentence(t)}</li>
            ))}
          </ul>
        </div>
      )}
      {defaults.before !== defaults.after && (
        <p className={styles.note}>
          Questions will default to {levelLabel(defaults.after ?? "")} (now {levelLabel(defaults.before ?? "")}).
          {preview.escalation && " A lower default lets more questions reach a model."}
        </p>
      )}
      {preview.newly_allowed.length === 0 && preview.no_longer_allowed.length === 0 && defaults.before === defaults.after && (
        <p className={styles.note}>No data level newly reaches, or stops reaching, any provider with the policy as it is now.</p>
      )}
      <div>
        <h4 className={styles.readingTitle}>Stays in force</h4>
        <ul className={styles.effects} data-testid="remaining">
          {remaining.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * Preview `change` on the server and commit it: directly when it lowers, through a fresh
 * sign-in at the identity provider when it raises. `rows`: the change in the page's words.
 */
function EgressReview({
  settings,
  change,
  rows,
  appliedTitle,
  onClose,
  onApplied,
  before,
}: {
  settings: ProviderSettings;
  change: EgressChange;
  rows: { key: string; label: string; now: ReactNode; after: ReactNode }[];
  appliedTitle: string;
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
  before?: ReactNode;
}) {
  const id = useId();
  const preview = usePreviewEgressChange();
  const { change: apply, run } = useMutate(onApplied);
  const start = useStartStepUp();
  const reason = useReason();
  const [confirmation, setConfirmation] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const { mutate } = preview;
  const key = JSON.stringify(change);
  useEffect(() => mutate(JSON.parse(key) as EgressChange), [mutate, key]);
  const shown = preview.data;
  useEffect(() => {
    if (shown) heading.current?.focus();
  }, [shown]);
  const escalation = shown?.escalation ?? false;
  const expected = settings.confirmation;
  const confirmError = reason.tried && escalation && confirmation.trim() !== expected ? `Type ${expected} exactly.` : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!shown) return;
    reason.setTried(true);
    if (!reason.reason) return;
    if (!escalation) {
      run({ kind: "change", body: { change, policy_version: shown.policy_version, reason: reason.reason } }, appliedTitle, "It applies to the next request.");
      return;
    }
    if (confirmation.trim() !== expected || !settings.step_up.available) return;
    start.mutate(
      { change, policy_version: shown.policy_version, reason: reason.reason, confirmation: confirmation.trim() },
      { onSuccess: (started) => window.location.assign(started.authorization_url) },
    );
  };
  const error = apply.error ?? start.error;
  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-describedby={`${id}-what`}>
      {before}
      <NowAfter caption="The change, now and after" rows={rows} />
      {preview.isError ? (
        <Refused error={preview.error} onClose={onClose} />
      ) : !shown ? (
        <RuleSkeleton variant="ledger" rows={3} label="Reviewing what would change" />
      ) : (
        <>
          <h3 className={styles.reviewHeading} ref={heading} tabIndex={-1} id={`${id}-what`}>
            {escalation ? "This raises what may leave Lattice" : "This doesn't raise anything"}
          </h3>
          <PreviewReading preview={shown} />
          {escalation &&
            (settings.step_up.available ? (
              <Callout tone="boundary" label="Confirm it by signing in again" labelAs="p" icon={<Icon as={ShieldAlert} />}>
                <p>
                  After you continue, your identity provider asks you to sign in again. Lattice applies exactly this change — on the policy as it is now — only if that
                  sign-in is fresh and yours, and you confirm within five minutes of it. Typing the name and the reason are recorded with the change.
                </p>
              </Callout>
            ) : (
              <Callout tone="fault" label="Can't be confirmed here" labelAs="p" role="alert">
                <p>{STEP_UP_UNAVAILABLE[settings.step_up.reason]}</p>
              </Callout>
            ))}
          <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
          {escalation && (
            <Field label={`Type ${expected} to confirm`} required error={confirmError}>
              {(control) => (
                <TextInput {...control} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" spellCheck={false} data-testid="confirmation" />
              )}
            </Field>
          )}
        </>
      )}
      {error && <Refused error={error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={apply.isPending || start.isPending}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          loading={apply.isPending || start.isPending}
          disabled={!shown || (escalation && !settings.step_up.available)}
          iconEnd={escalation ? <Icon as={ArrowUpRight} /> : undefined}
        >
          {escalation ? "Continue to sign in again" : "Apply change"}
        </Button>
      </Actions>
    </form>
  );
}

const STEP_UP_UNAVAILABLE: Record<string, string> = {
  available: "",
  no_connection:
    "Raising what may leave Lattice needs a fresh sign-in at your identity provider, and this organization doesn't sign in through one. Your Lattice operator can make this change.",
  no_identity:
    "Raising what may leave Lattice needs a fresh sign-in at your identity provider, and you don't sign in through it. Ask another administrator, or your Lattice operator.",
};

/* --- policy ------------------------------------------------------------------------------------ */

type CeilingChoice = "none" | (typeof LEVELS)[number];

function CeilingSelect({ label, value, onChange, help, allowNone = true }: { label: string; value: string; onChange: (v: string) => void; help?: string; allowNone?: boolean }) {
  return (
    <Field label={label} help={help}>
      {(control) => (
        <Select {...control} value={value} onChange={(e) => onChange(e.target.value)}>
          {allowNone && <option value="none">Not allowed</option>}
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {allowNone ? `Up to ${levelLabel(l)}` : levelLabel(l)}
            </option>
          ))}
        </Select>
      )}
    </Field>
  );
}

function PolicyForm({ settings, onClose, onApplied }: { settings: ProviderSettings; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const egress = settings.egress;
  const initial = {
    external: (egress.external_egress_level ?? "none") as CeilingChoice,
    controlled: (egress.organization_controlled_level ?? "none") as CeilingChoice,
    question: egress.default_query_sensitivity as string,
  };
  const [external, setExternal] = useState<string>(initial.external);
  const [controlled, setControlled] = useState<string>(initial.controlled);
  const [question, setQuestion] = useState<string>(initial.question);
  const [review, setReview] = useState<EgressChange | null>(null);
  const change: Record<string, unknown> = { kind: "settings" };
  if (external !== initial.external) change.external_egress_level = external === "none" ? null : external;
  if (controlled !== initial.controlled) change.organization_controlled_level = controlled === "none" ? null : controlled;
  if (question !== initial.question) change.default_query_sensitivity = question;
  const unchanged = Object.keys(change).length === 1;
  if (review) {
    const rows = [
      ...("external_egress_level" in review ? [{ key: "ext", label: "External APIs", now: ceilingWords(egress.external_egress_level), after: ceilingWords(review.external_egress_level) }] : []),
      ...("organization_controlled_level" in review
        ? [{ key: "org", label: "Organization-controlled servers", now: ceilingWords(egress.organization_controlled_level), after: ceilingWords(review.organization_controlled_level) }]
        : []),
      ...("default_query_sensitivity" in review
        ? [{ key: "q", label: "Questions default to", now: levelLabel(egress.default_query_sensitivity), after: levelLabel(review.default_query_sensitivity ?? "") }]
        : []),
    ];
    return <EgressReview settings={settings} change={review} rows={rows} appliedTitle="AI data policy changed" onClose={onClose} onApplied={onApplied} />;
  }
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (!unchanged) setReview(change as EgressChange);
      }}
      noValidate
    >
      <p className={styles.note}>
        The highest level of content each kind of provider may receive. Inside the Lattice deployment, nothing leaves: only each provider&apos;s own approval applies.
      </p>
      <CeilingSelect label="External APIs" value={external} onChange={setExternal} help="Third-party APIs such as Gemini." />
      <CeilingSelect label="Organization-controlled servers" value={controlled} onChange={setControlled} help="Model servers your organization runs, outside Lattice." />
      <CeilingSelect
        label="Questions default to"
        value={question}
        onChange={setQuestion}
        allowNone={false}
        help="A question is treated as at least this sensitive; whoever asks can only raise it. Lowering it lets more questions reach a model."
      />
      <Actions>
        <Button type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={unchanged}>
          Review change
        </Button>
      </Actions>
    </form>
  );
}

/* --- a provider's approval and data terms ----------------------------------------------------- */

function ApprovalForm({
  provider,
  settings,
  onClose,
  onApplied,
}: {
  provider: ProviderOut;
  settings: ProviderSettings;
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
}) {
  const id = useId();
  const current = provider.explicitly_approved ? provider.approved_for_level : "none";
  const [choice, setChoice] = useState<string>(current);
  const [review, setReview] = useState(false);
  const generation = provider.purpose === "generation";
  const words = (v: string) => (v === "none" ? (generation ? "Not approved" : `Default (${levelLabel("internal")})`) : `Up to ${levelLabel(v)}`);
  if (review) {
    return (
      <EgressReview
        settings={settings}
        change={{ kind: "provider", provider_id: provider.id, approval: choice === "none" ? null : (choice as (typeof LEVELS)[number]) }}
        rows={[{ key: "approval", label: "Approved", now: words(current), after: words(choice) }]}
        appliedTitle={choice === "none" ? "Approval withdrawn" : "Approval changed"}
        onClose={onClose}
        onApplied={onApplied}
      />
    );
  }
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (choice !== current) setReview(true);
      }}
      noValidate
    >
      <p className={styles.note}>
        The highest level of content you approve {providerName(provider)} to receive ({trustClassName(provider.deployment)}). The AI data policy can still
        cap it lower.
      </p>
      <ChoiceGroup legend="Approve up to">
        {LEVELS.map((l) => (
          <Radio key={l} name={`${id}-approval`} label={levelLabel(l)} checked={choice === l} onChange={() => setChoice(l)} />
        ))}
        <Radio name={`${id}-approval`} label={generation ? "Withdraw approval: send nothing" : "Back to the default approval"} checked={choice === "none"} onChange={() => setChoice("none")} />
      </ChoiceGroup>
      <Actions>
        <Button type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={choice === current}>
          Review change
        </Button>
      </Actions>
    </form>
  );
}

function DataTermsForm({
  provider,
  settings,
  onClose,
  onApplied,
}: {
  provider: ProviderOut;
  settings: ProviderSettings;
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
}) {
  const id = useId();
  const [optOut, setOptOut] = useState(provider.training_opt_out);
  const [review, setReview] = useState(false);
  const words = (v: boolean) => (v ? "No training on submitted content (paid)" : "May use submitted content (unpaid)");
  if (review) {
    return (
      <EgressReview
        settings={settings}
        change={{ kind: "provider", provider_id: provider.id, training_opt_out: optOut }}
        rows={[{ key: "terms", label: "Data terms", now: words(provider.training_opt_out), after: words(optOut) }]}
        appliedTitle="Data terms changed"
        onClose={onClose}
        onApplied={onApplied}
      />
    );
  }
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (optOut !== provider.training_opt_out) setReview(true);
      }}
      noValidate
    >
      <p className={styles.note}>
        What your agreement with the provider says it may do with content Lattice sends. A provider that may use submitted content only ever receives synthetic test data.
        The API key&apos;s declared tier must agree before anything is sent.
      </p>
      <ChoiceGroup legend="Data terms">
        <Radio name={`${id}-terms`} label={words(true)} checked={optOut} onChange={() => setOptOut(true)} />
        <Radio name={`${id}-terms`} label={words(false)} checked={!optOut} onChange={() => setOptOut(false)} />
      </ChoiceGroup>
      <Actions>
        <Button type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={optOut === provider.training_opt_out}>
          Review change
        </Button>
      </Actions>
    </form>
  );
}

/* --- configure ------------------------------------------------------------------------------- */

function AddProviderForm({ settings, onClose, onApplied }: { settings: ProviderSettings; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const id = useId();
  const { change, run } = useMutate(onApplied);
  const reason = useReason();
  const supported = settings.supported;
  const [kind, setKind] = useState(supported[0]?.provider ?? "gemini");
  const adapter = supported.find((s) => s.provider === kind);
  const [model, setModel] = useState(adapter?.models[0] ?? "");
  const [target, setTarget] = useState(settings.targets.find((t) => t.usable)?.id ?? "");
  const [optOut, setOptOut] = useState(true);
  const modelError = reason.tried && !model.trim() ? "Choose or enter the model." : undefined;
  const targetError = reason.tried && adapter?.needs_target && !target ? "Choose a server your Lattice operator approved." : undefined;
  const choose = (next: string) => {
    setKind(next as typeof kind);
    setModel(supported.find((s) => s.provider === next)?.models[0] ?? "");
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason || !model.trim() || (adapter?.needs_target && !target)) return;
    run(
      {
        kind: "create",
        body: {
          provider: kind,
          model: model.trim(),
          ...(adapter?.needs_target ? { target_id: target } : {}),
          ...(kind === "gemini" ? { training_opt_out: optOut } : {}),
          reason: reason.reason,
        },
      },
      "Provider added",
      "It's disabled and unapproved: nothing is sent to it until you approve and enable it.",
    );
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        A provider starts disabled and unapproved: adding one sends nothing anywhere. Its trust class follows where it runs — you don&apos;t choose it.
      </p>
      <ChoiceGroup legend="Provider">
        {supported.map((s) => (
          <Radio
            key={s.provider}
            name={`${id}-provider`}
            label={`${s.provider === "gemini" ? "Gemini API" : s.provider === "ollama" ? "Ollama on a server your organization runs" : "Test double (development)"} — ${trustClassName(s.trust_class)}`}
            checked={kind === s.provider}
            onChange={() => choose(s.provider)}
          />
        ))}
      </ChoiceGroup>
      {adapter && adapter.models.length > 0 ? (
        <Field label="Model" required error={modelError}>
          {(control) => (
            <Select {...control} value={model} onChange={(e) => setModel(e.target.value)}>
              {adapter.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
          )}
        </Field>
      ) : (
        <Field label="Model" required error={modelError} help="The model's name on the server, e.g. qwen3.5:4b.">
          {(control) => <TextInput {...control} value={model} onChange={(e) => setModel(e.target.value)} autoComplete="off" spellCheck={false} />}
        </Field>
      )}
      {adapter?.needs_target &&
        (settings.targets.length === 0 ? (
          <Callout tone="neutral" label="No server approved yet" labelAs="p">
            <p>Your Lattice operator must approve your organization&apos;s model server (its exact address) before it can be used here.</p>
          </Callout>
        ) : (
          <Field label="Server" required error={targetError} help="Only servers your Lattice operator approved for this organization.">
            {(control) => (
              <Select {...control} value={target} onChange={(e) => setTarget(e.target.value)}>
                {settings.targets.map((t) => (
                  <option key={t.id} value={t.id} disabled={!t.usable}>
                    {t.origin} — {trustClassName(t.trust_class)}
                    {t.usable ? "" : " (needs https)"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        ))}
      {kind === "gemini" && (
        <ChoiceGroup legend="Data terms" help="What your agreement with Google says it may do with content Lattice sends.">
          <Radio name={`${id}-terms`} label="No training on submitted content (paid)" checked={optOut} onChange={() => setOptOut(true)} />
          <Radio name={`${id}-terms`} label="May use submitted content (unpaid): synthetic data only" checked={!optOut} onChange={() => setOptOut(false)} />
        </ChoiceGroup>
      )}
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending} disabled={adapter?.needs_target === true && settings.targets.length === 0}>
          Add provider
        </Button>
      </Actions>
    </form>
  );
}

function EndpointForm({
  provider,
  settings,
  onClose,
  onApplied,
}: {
  provider: ProviderOut;
  settings: ProviderSettings;
  onClose: () => void;
  onApplied: (title: string, detail: string) => void;
}) {
  const { change, run } = useMutate(onApplied);
  const reason = useReason();
  const choices = settings.targets.filter((t) => t.trust_class === provider.deployment && t.origin !== provider.destination.origin);
  const [target, setTarget] = useState(choices.find((t) => t.usable)?.id ?? "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason || !target) return;
    run(
      { kind: "endpoint", id: provider.id, body: { target_id: target, policy_version: settings.egress.version, reason: reason.reason } },
      "Provider moved",
      "Its approval was given for the previous server and is withdrawn: approve it again to send anything.",
    );
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        Only servers your Lattice operator approved for this organization, with the same trust class. Moving withdraws the provider&apos;s approval: it was given for{" "}
        {provider.destination.origin ?? "the current server"}.
      </p>
      {choices.length === 0 ? (
        <Callout tone="neutral" label="No other server approved" labelAs="p">
          <p>Your Lattice operator must approve the other server first.</p>
        </Callout>
      ) : (
        <Field label="Server" required>
          {(control) => (
            <Select {...control} value={target} onChange={(e) => setTarget(e.target.value)}>
              {choices.map((t) => (
                <option key={t.id} value={t.id} disabled={!t.usable}>
                  {t.origin}
                  {t.usable ? "" : " (needs https)"}
                </option>
              ))}
            </Select>
          )}
        </Field>
      )}
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending} disabled={choices.length === 0}>
          Move provider
        </Button>
      </Actions>
    </form>
  );
}

/* --- API keys -------------------------------------------------------------------------------- */

function CredentialForm({ provider, onClose, onApplied }: { provider: ProviderOut; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const id = useId();
  const { change, run } = useMutate(onApplied);
  const reason = useReason();
  const [key, setKey] = useState("");
  const [dataUse, setDataUse] = useState<"paid" | "unpaid">(provider.training_opt_out ? "paid" : "unpaid");
  const replacing = provider.credential.state === "active";
  const keyError = reason.tried && !key.trim() ? "Paste the API key." : undefined;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!key.trim() || !reason.reason) return;
    run(
      {
        kind: "credential",
        id: provider.id,
        body: { api_key: key, data_use: dataUse, expected_credential_id: provider.credential.id ?? null, reason: reason.reason },
      },
      replacing ? "API key replaced" : "API key added",
      "Encrypted and stored; it can't be shown again. Nothing more may leave Lattice because of it — test it to check it works.",
    );
    // The typed key is not kept in the form once sent.
    setKey("");
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        {replacing
          ? "The new key replaces the current one at once, and the current one is destroyed."
          : `Lattice presents this key when it calls ${provider.provider}.`}{" "}
        It&apos;s encrypted as soon as it arrives and never shown again, to you or anyone else. A key approves nothing and raises no limit.
      </p>
      <Field label={replacing ? "New API key" : "API key"} required error={keyError} help="Pasted, never prefilled. It can't be displayed again after you save it.">
        {(control) => (
          <TextInput
            {...control}
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="new-password"
            spellCheck={false}
            maxLength={4096}
            data-testid="api-key-input"
          />
        )}
      </Field>
      <ChoiceGroup legend="The key's tier" help="An unpaid tier may let the provider use what it receives: then only synthetic test data is ever sent.">
        <Radio name={`${id}-use`} label="Paid: the provider doesn't use submitted content" checked={dataUse === "paid"} onChange={() => setDataUse("paid")} />
        <Radio name={`${id}-use`} label="Unpaid: the provider may use submitted content" checked={dataUse === "unpaid"} onChange={() => setDataUse("unpaid")} />
      </ChoiceGroup>
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={change.isPending}>
          {replacing ? "Replace key" : "Save key"}
        </Button>
      </Actions>
    </form>
  );
}

function RevokeForm({ provider, onClose, onApplied }: { provider: ProviderOut; onClose: () => void; onApplied: (title: string, detail: string) => void }) {
  const { change, run } = useMutate(onApplied);
  const reason = useReason();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reason.setTried(true);
    if (!reason.reason || !provider.credential.id) return;
    run(
      { kind: "revoke_credential", id: provider.id, body: { expected_credential_id: provider.credential.id, reason: reason.reason } },
      "API key revoked",
      "The next request can't use it; its stored copy is destroyed.",
    );
  };
  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <p className={styles.note}>
        {provider.provider} can&apos;t be called from the next request on; Ask shows evidence only until a new key is added. The stored copy is destroyed.
        {provider.credential.managed_by === "operator" && " This key was set by your Lattice operator; revoking it here stops it for this organization."}
      </p>
      <ReasonField value={reason.value} onChange={reason.setReason} error={reason.error} inputRef={reason.ref} />
      {change.isError && <Refused error={change.error} onClose={onClose} />}
      <Actions>
        <Button type="button" onClick={onClose} disabled={change.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="danger" loading={change.isPending}>
          Revoke key
        </Button>
      </Actions>
    </form>
  );
}

/* --- coming back from the identity provider ------------------------------------------------------ */

export function StepUpResult({ id, onDismiss, onApplied }: { id: string; onDismiss: () => void; onApplied: (applied: Applied) => void }) {
  const query = useStepUp(id);
  const apply = useChangeProviders();
  const stepUp = query.data;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (stepUp) heading.current?.focus();
  }, [stepUp]);
  if (query.isError) {
    return (
      <Callout tone="fault" label="Sign-in check not available" role="alert">
        <p>{isApiError(query.error) && query.error.isNotFound ? "This sign-in check isn't yours, or it has gone. Nothing changed." : providerError(query.error)}</p>
        <p>
          <Button size="sm" onClick={onDismiss}>
            Dismiss
          </Button>
        </p>
      </Callout>
    );
  }
  if (!stepUp) return <RuleSkeleton variant="ledger" rows={2} label="Reading the sign-in" />;
  const words = STEP_UP_OUTCOME[stepUp.outcome];
  const usable = usableGrant(stepUp);
  return (
    <section className={styles.stepUp} aria-labelledby="step-up-title" data-outcome={stepUp.outcome} data-testid="step-up-result">
      <h2 className={styles.stepUpTitle} id="step-up-title" ref={heading} tabIndex={-1}>
        <StatusMark tone={stepUp.outcome === "granted" ? "healthy" : stepUp.outcome === "pending" ? "time" : "fault"}>{words.title}</StatusMark>
      </h2>
      <p role="status">{usable || stepUp.outcome !== "granted" ? words.message : grantGone(stepUp)}</p>
      {stepUp.grant && (
        <p className={styles.note} data-testid="step-up-facts">
          Signed in at {formatTime(stepUp.grant.auth_time)} UTC; apply by {formatTime(stepUp.grant.expires_at)} UTC.{" "}
          {stepUp.grant.assurance
            ? `Your identity provider asserted the sign-in method your Lattice operator requires (${stepUp.grant.assurance}).`
            : "Lattice verified a fresh sign-in; whether it used a second factor is up to your identity provider."}
        </p>
      )}
      <p className={styles.note}>Reason: {stepUp.reason}</p>
      {usable && stepUp.preview && <PreviewReading preview={stepUp.preview} />}
      {apply.isError && <Refused error={apply.error} onClose={onDismiss} />}
      <div className={styles.testActions}>
        {usable && (
          <Button
            variant="primary"
            loading={apply.isPending}
            onClick={() =>
              apply.mutate(
                { kind: "apply_step_up", id },
                { onSuccess: () => onApplied({ title: "AI data policy raised", detail: "Exactly the change you confirmed, applied once.", at: new Date().toISOString() }) },
              )
            }
          >
            Apply this change
          </Button>
        )}
        <Button onClick={onDismiss} disabled={apply.isPending}>
          {usable ? "Don't apply" : "Dismiss"}
        </Button>
      </div>
    </section>
  );
}

function usableGrant(s: StepUp): boolean {
  return s.outcome === "granted" && s.grant !== null && s.grant !== undefined && !s.grant.consumed && s.current_version === s.policy_version && s.preview !== null;
}

function grantGone(s: StepUp): string {
  if (s.grant?.consumed) return "This change was already applied with that sign-in.";
  if (s.current_version !== s.policy_version) return "The AI data policy changed after you reviewed this, so this sign-in can't apply it. Review the change and sign in again.";
  return "More than five minutes passed since you signed in again. Review the change and sign in again.";
}
