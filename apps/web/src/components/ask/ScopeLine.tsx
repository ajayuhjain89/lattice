"use client";

import { useState } from "react";

import { useAccounts, useSources } from "@/api/queries";
import { SOURCE_TYPES, type SourceType } from "@/api/types";
import { Checkbox, ChoiceGroup, DateInput, Field, Radio, SearchInput } from "@/components/primitives/Field";
import { FilterLine, FilterToken } from "@/components/primitives/FilterLine";
import { formatDay } from "@/lib/format";
import { readableSourceTypes } from "@/lib/scope";

import type { AskForm } from "./form";
import styles from "./ScopeLine.module.css";

const LONG: Record<SourceType, string> = { doc: "Documents", ticket: "Tickets", chat: "Chat messages" };
const SHORT: Record<SourceType, string> = { doc: "documents", ticket: "tickets", chat: "chat" };
const FILTER_FROM = 7;

/**
 * The search scope read as a sentence (design-direction §6.5):
 *
 *   Search  all my sources · any account · any date · as default sensitivity
 *
 * Each phrase opens its choices. Every choice can only narrow the search; none widens what
 * the API lets the person read. The accounts offered are exactly those GET /v1/accounts
 * lists (the only ones a scope may name), the source types those with a source the person
 * can read from (GET /v1/sources); the API enforces both regardless.
 */
export function ScopeLine({ form, onChange, datesInvalid }: { form: AskForm; onChange: (f: AskForm) => void; datesInvalid: boolean }) {
  const readable = useSources();
  // Until the list arrives (or if it fails) every type is offered: narrowing is a convenience.
  const offered = readable.data ? SOURCE_TYPES.filter((t) => readableSourceTypes(readable.data.sources).includes(t) || form.sourceTypes.includes(t)) : SOURCE_TYPES;
  const sources = form.sourceTypes.length ? form.sourceTypes.map((t) => SHORT[t]).join(", ") : "all my sources";
  const dates = dateSummary(form.since, form.until);
  const level = form.classification === "confidential" ? "Confidential" : form.classification === "restricted" ? "Restricted" : null;

  return (
    <FilterLine lead="Search" label="Search scope">
      <FilterToken name="Sources" value={sources} set={form.sourceTypes.length > 0} onClear={() => onChange({ ...form, sourceTypes: [] })}>
        <ChoiceGroup legend="Search only" help="None selected searches every source type you can read.">
          {offered.length === 0 && <p className={styles.help}>No source you can read from is indexed yet.</p>}
          {offered.map((t) => (
            <Checkbox
              key={t}
              label={LONG[t]}
              checked={form.sourceTypes.includes(t)}
              onChange={(e) =>
                onChange({
                  ...form,
                  sourceTypes: e.target.checked ? SOURCE_TYPES.filter((x) => x === t || form.sourceTypes.includes(x)) : form.sourceTypes.filter((x) => x !== t),
                })
              }
            />
          ))}
        </ChoiceGroup>
      </FilterToken>

      <FilterToken name="Account" value={form.account?.name ?? "any account"} set={form.account !== null} onClear={() => onChange({ ...form, account: null })}>
        <AccountOptions form={form} onChange={onChange} />
      </FilterToken>

      <FilterToken name="Dates" value={dates ?? "any date"} set={dates !== null} invalid={datesInvalid} onClear={() => onChange({ ...form, since: "", until: "" })}>
        <fieldset className={styles.dates}>
          <legend className={styles.legend}>Evidence dated</legend>
          <div className={styles.dateFields}>
            <Field label="From">
              {(control) => <DateInput {...control} size="sm" value={form.since} max={form.until || undefined} onChange={(e) => onChange({ ...form, since: e.target.value })} />}
            </Field>
            <Field label="To">
              {(control) => <DateInput {...control} size="sm" value={form.until} min={form.since || undefined} onChange={(e) => onChange({ ...form, until: e.target.value })} />}
            </Field>
          </div>
          <p className={datesInvalid ? styles.error : styles.help}>{datesInvalid ? "The start date must be on or before the end date." : "Either end may be left open."}</p>
        </fieldset>
      </FilterToken>

      <FilterToken name="Sensitivity" prefix="as" value={level ?? "default sensitivity"} set={level !== null} onClear={() => onChange({ ...form, classification: null })}>
        <ChoiceGroup legend="Treat this question as" help="This can only make handling stricter than your organization's default, never looser.">
          <Radio name="ask-classification" label="Organization default" help="The level your organization sets for questions." checked={form.classification === null} onChange={() => onChange({ ...form, classification: null })} />
          <Radio
            name="ask-classification"
            label="Confidential"
            help="Not sent to AI providers approved only for Internal data."
            checked={form.classification === "confidential"}
            onChange={() => onChange({ ...form, classification: "confidential" })}
          />
          <Radio
            name="ask-classification"
            label="Restricted"
            help="Sent only to providers approved for Restricted data."
            checked={form.classification === "restricted"}
            onChange={() => onChange({ ...form, classification: "restricted" })}
          />
        </ChoiceGroup>
      </FilterToken>
    </FilterLine>
  );
}

/** The member's accounts as the API lists them; choosing one narrows the search to it. */
function AccountOptions({ form, onChange }: { form: AskForm; onChange: (f: AskForm) => void }) {
  const query = useAccounts();
  const [filter, setFilter] = useState("");
  if (query.isError) return <p className={styles.help}>Couldn&apos;t load your accounts. Close this and try again.</p>;
  if (!query.data) {
    return (
      <p className={styles.help} role="status">
        Loading your accounts…
      </p>
    );
  }
  const accounts = query.data.accounts;
  if (accounts.length === 0) {
    return <p className={styles.help}>You aren&apos;t assigned to any accounts, so Lattice searches only content that isn&apos;t tied to an account.</p>;
  }
  const wanted = filter.trim().toLowerCase();
  const shown = wanted ? accounts.filter((a) => a.name.toLowerCase().includes(wanted)) : accounts;
  return (
    <>
      {accounts.length >= FILTER_FROM && (
        <SearchInput size="sm" value={filter} onValueChange={setFilter} placeholder="Filter accounts" aria-label="Filter accounts" clearLabel="Clear account filter" />
      )}
      <ChoiceGroup legend="Search only" help={query.data.account_scope === "all" ? "Every account is in your scope." : "Only the accounts assigned to you are listed."}>
        <div className={styles.accountList}>
          {shown.map((a) => (
            <Radio key={a.key} name="ask-account" label={a.name} checked={form.account?.key === a.key} onChange={() => onChange({ ...form, account: { key: a.key, name: a.name } })} />
          ))}
          {shown.length === 0 && <p className={styles.help}>No account matches.</p>}
        </div>
      </ChoiceGroup>
    </>
  );
}

function dateSummary(since: string, until: string): string | null {
  const a = since ? formatDay(`${since}T00:00:00Z`) : null;
  const b = until ? formatDay(`${until}T00:00:00Z`) : null;
  if (a && b) return `${a} – ${b}`;
  if (a) return `from ${a}`;
  if (b) return `until ${b}`;
  return null;
}
