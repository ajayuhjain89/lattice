"use client";

import { ChevronDown, X } from "lucide-react";
import { Popover } from "radix-ui";
import { useId, type ReactNode } from "react";

import { SOURCE_TYPES, type SourceType } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { formatDay } from "@/lib/format";

import type { AskForm } from "./form";
import styles from "./ScopeControls.module.css";

const PLURAL: Record<SourceType, string> = { doc: "Docs", ticket: "Tickets", chat: "Chat" };
const LONG: Record<SourceType, string> = { doc: "Documents", ticket: "Tickets", chat: "Chat messages" };

/**
 * Scope chips. Each one can only narrow the search; none of them can widen what the API
 * lets the person read. Accounts are never listed here: an account scope is set only from
 * a clarification the API offered.
 */
export function ScopeControls({ form, onChange, datesInvalid }: { form: AskForm; onChange: (f: AskForm) => void; datesInvalid: boolean }) {
  const sources = form.sourceTypes.length ? form.sourceTypes.map((s) => PLURAL[s]).join(", ") : null;
  const dates = dateSummary(form.since, form.until);

  return (
    <div className={styles.scope} role="group" aria-label="Search scope">
      {form.account && (
        <Chip
          label="Account"
          value={form.account.name}
          onClear={() => onChange({ ...form, account: null })}
        />
      )}

      <Chip label="Sources" value={sources} onClear={() => onChange({ ...form, sourceTypes: [] })}>
        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Search only</legend>
          {SOURCE_TYPES.map((t) => (
            <label key={t} className={styles.option}>
              <input
                type="checkbox"
                checked={form.sourceTypes.includes(t)}
                onChange={(e) =>
                  onChange({
                    ...form,
                    sourceTypes: e.target.checked
                      ? SOURCE_TYPES.filter((x) => x === t || form.sourceTypes.includes(x))
                      : form.sourceTypes.filter((x) => x !== t),
                  })
                }
              />
              <span>{LONG[t]}</span>
            </label>
          ))}
          <p className={styles.help}>None selected searches every source type you can read.</p>
        </fieldset>
      </Chip>

      <Chip label="Dates" value={dates} invalid={datesInvalid} onClear={() => onChange({ ...form, since: "", until: "" })}>
        <DateFields form={form} onChange={onChange} />
      </Chip>

      <Chip
        label="Sensitivity"
        value={form.classification ? `${form.classification === "confidential" ? "Confidential" : "Restricted"}` : null}
        onClear={() => onChange({ ...form, classification: null })}
      >
        <SensitivityOptions form={form} onChange={onChange} />
      </Chip>
    </div>
  );
}

function SensitivityOptions({ form, onChange }: { form: AskForm; onChange: (f: AskForm) => void }) {
  const id = useId();
  const options = [
    [null, "Organization default", "The level your organization sets for questions."],
    ["confidential", "Confidential", "Not sent to AI providers approved only for Internal data."],
    ["restricted", "Restricted", "Sent only to providers approved for Restricted data."],
  ] as const;
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>Treat this question as</legend>
      {options.map(([value, title, help], i) => (
        <div key={title} className={styles.option}>
          <input
            id={`${id}-${i}`}
            type="radio"
            name={`${id}-classification`}
            checked={form.classification === value}
            onChange={() => onChange({ ...form, classification: value })}
            aria-describedby={`${id}-${i}-help`}
          />
          <span className={styles.optionText}>
            <label htmlFor={`${id}-${i}`}>{title}</label>
            <span className={styles.optionHelp} id={`${id}-${i}-help`}>
              {help}
            </span>
          </span>
        </div>
      ))}
      <p className={styles.help}>This can only make handling stricter than your organization&apos;s default, never looser.</p>
    </fieldset>
  );
}

function DateFields({ form, onChange }: { form: AskForm; onChange: (f: AskForm) => void }) {
  const since = useId();
  const until = useId();
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>Evidence dated</legend>
      <div className={styles.dates}>
        <span className={styles.dateField}>
          <label className={styles.dateLabel} htmlFor={since}>
            From
          </label>
          <input id={since} className={styles.dateInput} type="date" value={form.since} max={form.until || undefined} onChange={(e) => onChange({ ...form, since: e.target.value })} />
        </span>
        <span className={styles.dateField}>
          <label className={styles.dateLabel} htmlFor={until}>
            To
          </label>
          <input id={until} className={styles.dateInput} type="date" value={form.until} min={form.since || undefined} onChange={(e) => onChange({ ...form, until: e.target.value })} />
        </span>
      </div>
      <p className={styles.help}>Either end may be left open.</p>
    </fieldset>
  );
}

function dateSummary(since: string, until: string): string | null {
  const a = since ? formatDay(`${since}T00:00:00Z`) : null;
  const b = until ? formatDay(`${until}T00:00:00Z`) : null;
  if (a && b) return `${a} – ${b}`;
  if (a) return `From ${a}`;
  if (b) return `Until ${b}`;
  return null;
}

function Chip({
  label,
  value,
  onClear,
  invalid = false,
  children,
}: {
  label: string;
  value: string | null;
  onClear: () => void;
  invalid?: boolean;
  children?: ReactNode;
}) {
  const set = value !== null;
  const text = set ? `${label}: ${value}` : label;
  const className = [styles.chip, set && styles.set, invalid && styles.invalid].filter(Boolean).join(" ");
  return (
    <span className={className}>
      {children ? (
        <Popover.Root>
          <Popover.Trigger className={styles.trigger} aria-label={set ? `${label}: ${value}. Change` : `Set ${label.toLowerCase()}`}>
            <span className={styles.triggerText}>{text}</span>
            <ChevronDown size={14} strokeWidth={1.5} className={styles.chevron} aria-hidden="true" />
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className={styles.popover} align="start" sideOffset={6} collisionPadding={16}>
              {children}
              <div className={styles.popoverActions}>
                <Button size="sm" variant="ghost" onClick={onClear} disabled={!set}>
                  Clear
                </Button>
                <Popover.Close asChild>
                  <Button size="sm">Done</Button>
                </Popover.Close>
              </div>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      ) : (
        <span className={styles.trigger}>
          <span className={styles.triggerText}>{text}</span>
        </span>
      )}
      {set && (
        <button type="button" className={styles.clear} onClick={onClear} aria-label={`Remove ${label.toLowerCase()} filter`}>
          <X size={12} strokeWidth={1.75} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}
