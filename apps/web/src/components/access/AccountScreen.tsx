"use client";

import { Archive, ArchiveRestore, Link2, Pencil, RefreshCw } from "lucide-react";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccount, useRequestSync } from "@/api/queries";
import type { AccountDetail, AccountMapping, AccountRef } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { connectorLabel, formatCount, formatDay, isUuid } from "@/lib/format";

import styles from "./Access.module.css";
import { EditAccountDialog, LifecycleDialog } from "./AccountDialogs";
import { businessStatusLabel, refKindLabel } from "./accountsCopy";
import { MappingDialog, type AppliedMapping, type MappingTarget } from "./MappingDialog";
import { AppliedLine, type AppliedChange } from "./MemberScreen";

/**
 * One account's administrative record (GET /v1/admin/access/accounts/{id}): its metadata,
 * lifecycle and the source references that map to it, with the operations that change
 * them -- each previewed, given a reason and audited. Not an account timeline: no
 * activity, documents or content of any kind (post-v1).
 */
export function AccountScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  const query = useAccount(id, valid);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const missing = !valid || (query.isError && isApiError(query.error) && query.error.isNotFound);
  const name = query.data?.name;

  return (
    <OpsPage
      trail={[
        { label: "Access", href: "/access" },
        { label: "Accounts", href: "/access/accounts" },
        { label: name ?? "Account" },
      ]}
    >
      <AdminGate forbidden={forbidden} body="Account administration is available to your organization's administrators. Being an administrator never gives access to content.">
        {missing ? (
          <NotAvailable
            label="Not available"
            title="This account isn't available"
            body="The link may be wrong."
            action={{ href: "/access/accounts", label: "Back to Accounts" }}
          />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Couldn't load this account" onRetry={() => query.refetch()} retrying={query.isFetching} headingLevel="h1" />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={4} label="Loading this account" />
        ) : (
          <Account account={query.data} />
        )}
      </AdminGate>
    </OpsPage>
  );
}

type Applied = AppliedChange & { source?: AppliedMapping["source"] };

function Account({ account }: { account: AccountDetail }) {
  const [editing, setEditing] = useState(false);
  const [lifecycle, setLifecycle] = useState(false);
  // Fixed when the dialog opens: the account reloads underneath it once the change applies.
  const [archiving, setArchiving] = useState(true);
  const [mapping, setMapping] = useState<MappingTarget | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const archived = account.lifecycle === "archived";
  const self: AccountRef = { id: account.id, key: account.key, name: account.name, lifecycle: account.lifecycle };

  return (
    <>
      <header className={styles.memberHead}>
        <h1 className={styles.memberName}>{account.name}</h1>
        <p className={styles.identity}>
          <span className={styles.email} data-testid="account-key">
            {account.key}
          </span>
          {account.aliases.length > 0 && <span>Also {account.aliases.join(", ")}</span>}
        </p>
        <p className={styles.facts}>
          {archived ? (
            <StatusMark tone="neutral">Archived{account.archived_at ? ` ${formatDay(account.archived_at)}` : ""}</StatusMark>
          ) : (
            <StatusMark tone="healthy">Active</StatusMark>
          )}
          <span>Business status: {businessStatusLabel(account.status)}</span>
          <span>{formatCount(account.assigned_members, "member assigned", "members assigned")}</span>
          <span>Created {formatDay(account.created_at)}</span>
        </p>
      </header>

      {archived && (
        <NoticeLine icon={<Icon as={Archive} />}>
          <span data-testid="archived-notice">
            Archived: nothing was deleted or hidden. Its documents and assigned members are unchanged, and its references keep working. It takes no new
            references, and can&apos;t be edited, until it&apos;s reactivated.
          </span>
        </NoticeLine>
      )}

      <div className={styles.actions}>
        {!archived && (
          <>
            <Button variant="primary" icon={<Icon as={Link2} />} onClick={() => setMapping({ mode: "add", account: self })}>
              Add a reference
            </Button>
            <Button icon={<Icon as={Pencil} />} onClick={() => setEditing(true)}>
              Edit
            </Button>
          </>
        )}
        <Button
          icon={<Icon as={archived ? ArchiveRestore : Archive} />}
          variant={archived ? "primary" : "secondary"}
          onClick={() => {
            setArchiving(!archived);
            setLifecycle(true);
          }}
        >
          {archived ? "Reactivate" : "Archive"}
        </Button>
        <p className={styles.note}>Managing this account gives you no access to its content.</p>
      </div>

      {applied && (
        <AppliedLine change={applied} onDismiss={() => setApplied(null)}>
          {applied.source && <SyncNow source={applied.source} />}
        </AppliedLine>
      )}

      <References account={account} onChange={setMapping} />

      {!archived && <EditAccountDialog account={account} open={editing} onOpenChange={setEditing} onApplied={setApplied} />}
      <LifecycleDialog account={account} archive={archiving} open={lifecycle} onOpenChange={setLifecycle} onApplied={setApplied} />
      <MappingDialog target={mapping} onOpenChange={(open) => !open && setMapping(null)} onApplied={setApplied} />
    </>
  );
}

function References({ account, onChange }: { account: AccountDetail; onChange: (target: MappingTarget) => void }) {
  const current: AccountRef = { id: account.id, key: account.key, name: account.name, lifecycle: account.lifecycle };
  const reference = (m: AccountMapping) => ({ source: m.source, ref_kind: m.ref_kind, external_ref: m.external_ref });
  const columns: Column<AccountMapping>[] = [
    {
      key: "reference",
      header: "Reference",
      primary: true,
      cell: (m) => (
        <span className={styles.stack}>
          <code className={styles.claim}>{m.external_ref}</code>
          <span className={styles.secondary}>{refKindLabel(m.ref_kind)}</span>
        </span>
      ),
    },
    {
      key: "source",
      header: "Source",
      cell: (m) => (
        <span className={styles.stack}>
          <span>{m.source.name}</span>
          <span className={styles.secondary}>{connectorLabel(m.source.connector_type)}</span>
        </span>
      ),
    },
    { key: "since", header: "Mapped since", short: true, mono: true, cell: (m) => formatDay(m.updated_at) },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      align: "end",
      cell: (m) => (
        <span className={styles.rowActions}>
          <Button size="sm" aria-label={`Move ${m.external_ref} to another account`} onClick={() => onChange({ mode: "remap", reference: reference(m), current })}>
            Move
          </Button>
          <Button
            size="sm"
            variant="quiet"
            aria-label={`Remove the mapping of ${m.external_ref}`}
            onClick={() => onChange({ mode: "unmap", reference: reference(m), current })}
          >
            Remove
          </Button>
        </span>
      ),
    },
  ];
  return (
    <OpsSection id="references-heading" title="References" count={account.mappings.length}>
      <p className={styles.note}>
        The exact identifiers your ticketing and chat exports use for this account. Records that carry one are indexed under this account at each sync.
        Documents name their account themselves.
      </p>
      <DataTable
        caption={`References mapped to ${account.name}`}
        columns={columns}
        rows={account.mappings}
        rowKey={(m) => `${m.source.id}/${m.ref_kind}/${m.external_ref}`}
        stackBelow="768"
        empty={
          <StateBlock family="quiet" heading="No references" headingLevel="h3" compact>
            <p>No ticketing organization or channel maps to this account.</p>
          </StateBlock>
        }
      />
    </OpsSection>
  );
}

/** Start the source's next sync right after a mapping change (the existing request). */
export function SyncNow({ source }: { source: AppliedMapping["source"] }) {
  const sync = useRequestSync();
  return (
    <span className={styles.appliedDetail}>
      {sync.isSuccess ? (
        <span role="status">A sync of {source.name} is queued. See its progress in Sources.</span>
      ) : (
        <Button size="sm" icon={<Icon as={RefreshCw} />} loading={sync.isPending} onClick={() => sync.mutate(source.id)}>
          Sync {source.name} now
        </Button>
      )}
      {sync.isError && (
        <span className={styles.problem} role="alert">
          {" "}
          Couldn&apos;t start the sync. Try again from Sources.
        </span>
      )}
    </span>
  );
}
