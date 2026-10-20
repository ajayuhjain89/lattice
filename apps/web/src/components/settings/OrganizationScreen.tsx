"use client";

import { CheckCircle2, Pencil, X } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { isApiError } from "@/api/errors";
import { useRenameOrganization, useSetup } from "@/api/queries";
import type { SetupOrganization, SetupCategory } from "@/api/types";
import { Button, ButtonLink } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Field, TextArea, TextInput } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { Modal } from "@/components/primitives/Modal";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsHead, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { formatTime, humanize } from "@/lib/format";

import { ACTIONABLE, CATEGORY_LABEL, STATE, factLine } from "./setup-model";
import styles from "./OrganizationScreen.module.css";

/**
 * Organization (phase-12-plan §7, C12): the organization's setup record. One row per
 * category, each with the state, reason and message the API derived from live data on
 * this read -- the page never decides whether anything is complete -- the API's facts as a
 * ledger line, and the page that owns the item, or who acts when no page does yet. Not a
 * wizard and not a score: it stays true after onboarding.
 *
 * The one change made here is the organization's display name (audited, with a reason).
 */
export function OrganizationScreen() {
  const { isAdmin, me } = useWorkspace();
  const query = useSetup(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const [renaming, setRenaming] = useState(false);
  const [renamed, setRenamed] = useState<{ name: string; at: string } | null>(null);
  const organization = query.data?.organization;

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="The organization's setup record is visible to its administrators.">
        <OpsHead
          title="Organization"
          deck={`${organization?.name ?? me.organization.name}'s setup record: what is in place, what is still needed and who acts next — read from its live configuration each time you open this page.`}
        />
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load the setup record" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data || !organization ? (
          <RuleSkeleton variant="ledger" rows={6} label="Loading the setup record" />
        ) : (
          <>
            {renamed && <RenamedLine name={renamed.name} at={renamed.at} onDismiss={() => setRenamed(null)} />}
            <Identity organization={organization} onRename={() => setRenaming(true)} />
            <SetupRecord categories={query.data.categories} />
            <RenameDialog
              organization={organization}
              open={renaming}
              onOpenChange={setRenaming}
              onRenamed={(name) => setRenamed({ name, at: new Date().toISOString() })}
            />
          </>
        )}
      </AdminGate>
    </OpsPage>
  );
}

/* --- the organization ----------------------------------------------------------------------- */

const STATUS_WORD: Record<string, string> = { active: "Active", suspended: "Suspended" };

function Identity({ organization, onRename }: { organization: SetupOrganization; onRename: () => void }) {
  return (
    <OpsSection
      id="organization-heading"
      title="The organization"
      aside={
        <Button size="sm" icon={<Icon as={Pencil} />} onClick={onRename}>
          Rename
        </Button>
      }
    >
      <dl className={styles.facts}>
        <div>
          <dt>Name</dt>
          <dd data-testid="organization-name">{organization.name}</dd>
        </div>
        <div>
          <dt>Identifier</dt>
          <dd className={styles.ledger}>{organization.slug}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{STATUS_WORD[organization.status] ?? humanize(organization.status)}</dd>
        </div>
        <div>
          <dt>Data origin</dt>
          <dd>{organization.data_origin === "synthetic" ? "Synthetic test data" : "Customer data"}</dd>
        </div>
      </dl>
      <p className={styles.note}>
        The identifier, status and data origin are set by your Lattice operator. Being an administrator never gives access to content.
      </p>
    </OpsSection>
  );
}

/* --- the setup record ---------------------------------------------------------------------- */

function SetupRecord({ categories }: { categories: readonly SetupCategory[] }) {
  return (
    <OpsSection id="setup-heading" title="Setup record">
      <ol className={styles.record} aria-labelledby="setup-heading" data-testid="setup-record">
        {categories.map((category) => (
          <SetupRow key={category.key} category={category} />
        ))}
      </ol>
    </OpsSection>
  );
}

function SetupRow({ category }: { category: SetupCategory }) {
  const state = STATE[category.state];
  const facts = factLine(category);
  const id = `setup-${category.key}`;
  return (
    <li className={styles.row} aria-labelledby={id} data-state={category.state} data-reason={category.reason}>
      <h3 className={styles.item} id={id}>
        {CATEGORY_LABEL[category.key]}
      </h3>
      <p className={styles.state}>
        <StatusMark tone={state.tone} icon={state.icon}>
          {state.word}
        </StatusMark>
      </p>
      <div className={styles.reading}>
        <p className={styles.message}>{category.message}</p>
        {facts.length > 0 && (
          <p className={styles.ledgerLine}>
            {facts.map((fact, i) => (
              <span key={fact}>
                {i > 0 && <span aria-hidden="true"> · </span>}
                {fact}
              </span>
            ))}
          </p>
        )}
      </div>
      <div className={styles.next}>
        {category.action ? (
          <ButtonLink href={category.action.href} size="sm" aria-describedby={id}>
            {category.action.label}
          </ButtonLink>
        ) : ACTIONABLE.has(category.state) ? (
          <span className={styles.operator}>Your Lattice operator acts on this</span>
        ) : null}
      </div>
    </li>
  );
}

/* --- rename -------------------------------------------------------------------------------- */

function RenamedLine({ name, at, onDismiss }: { name: string; at: string; onDismiss: () => void }) {
  return (
    <div className={styles.applied} role="status" data-testid="renamed">
      <span className={styles.appliedIcon}>
        <Icon as={CheckCircle2} />
      </span>
      <p className={styles.appliedText}>
        <strong>{`Renamed to ${name}`}</strong> <span className={styles.when}>{formatTime(at)} UTC</span>
        <span className={styles.appliedDetail}>
          Recorded in the <Link href="/access/audit">audit log</Link> with your name and reason.
        </span>
      </p>
      <Button size="sm" variant="quiet" icon={<Icon as={X} />} aria-label="Dismiss" onClick={onDismiss} />
    </div>
  );
}

function RenameDialog({
  organization,
  open,
  onOpenChange,
  onRenamed,
}: {
  organization: SetupOrganization;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRenamed: (name: string) => void;
}) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Rename the organization" description="The name people see in Lattice. The identifier and sign-in address don't change.">
      {open && (
        <RenameForm
          organization={organization}
          onDone={() => onOpenChange(false)}
          onRenamed={(name) => {
            onRenamed(name);
            onOpenChange(false);
          }}
        />
      )}
    </Modal>
  );
}

function RenameForm({ organization, onDone, onRenamed }: { organization: SetupOrganization; onDone: () => void; onRenamed: (name: string) => void }) {
  const id = useId();
  const rename = useRenameOrganization();
  const [name, setName] = useState(organization.name);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const trimmed = name.trim();
  const nameError = tried && !trimmed ? "Enter a name." : tried && trimmed.length > 200 ? "Use at most 200 characters." : undefined;
  const reasonError = tried && !reason.trim() ? "Say why: the reason is recorded in the audit log." : undefined;
  const unchanged = trimmed === organization.name;
  const conflict = rename.isError && isApiError(rename.error) && rename.error.status === 409;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (!trimmed || trimmed.length > 200 || !reason.trim() || unchanged) return;
    rename.mutate(
      { name: trimmed, version: organization.version, reason: reason.trim() },
      { onSuccess: (updated) => onRenamed(updated.name) },
    );
  };

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-describedby={`${id}-audit`}>
      <Field label="Name" required error={nameError}>
        {(control) => <TextInput {...control} value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoComplete="organization" />}
      </Field>
      <Field label="Reason" required error={reasonError} help="Recorded in the audit log with your name.">
        {(control) => <TextArea {...control} value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500} />}
      </Field>
      <p className={styles.note} id={`${id}-audit`}>
        The change is recorded in the audit log together with the previous name.
      </p>
      {rename.isError && (
        <Callout tone="fault" label={conflict ? "Changed since you opened this" : "Not renamed"} role="alert">
          <p>
            {conflict
              ? "Someone changed the organization meanwhile. Close this dialog; the page shows the current name."
              : isApiError(rename.error)
                ? rename.error.message
                : "The change could not be sent. Try again."}
          </p>
        </Callout>
      )}
      <div className={styles.actions}>
        {unchanged && tried && <p className={styles.note}>The name is unchanged.</p>}
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={rename.isPending} disabled={conflict}>
          Rename
        </Button>
      </div>
    </form>
  );
}
