"use client";

import { Link2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useUnmappedReferences } from "@/api/queries";
import type { AccountRef, UnmappedReference } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { ErrorState } from "@/components/states/ErrorState";
import { connectorLabel, formatDayTime } from "@/lib/format";

import styles from "./Access.module.css";
import { AccessViews } from "./AccessViews";
import { CreateAccountDialog } from "./AccountDialogs";
import { SyncNow } from "./AccountScreen";
import { refKindLabel } from "./accountsCopy";
import { MappingDialog, type AppliedMapping, type MappingTarget, type Reference } from "./MappingDialog";
import { AppliedLine } from "./MemberScreen";

/**
 * Unmapped references (Phase 12, P12.3; phase-12-plan C10): what the latest sync of each
 * ticketing or chat source could not attach to an account -- the reference exactly as
 * the source sent it and how many records wait for it. Nothing from the records: no
 * text, titles or channel names (C9, D-18), and no suggested account (D-10). The
 * administrator maps a reference to an existing active account, creates one explicitly
 * first, or leaves it unresolved.
 */
export function ReferencesScreen() {
  const query = useUnmappedReferences(true);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const [mapping, setMapping] = useState<MappingTarget | null>(null);
  // Creating an account for a reference, then mapping it to that account.
  const [creatingFor, setCreatingFor] = useState<Reference | null>(null);
  const [applied, setApplied] = useState<AppliedMapping | null>(null);
  const items = query.data?.items ?? [];
  const reference = (r: UnmappedReference): Reference => ({ source: r.source, ref_kind: r.ref_kind, external_ref: r.external_ref });

  const columns: Column<UnmappedReference>[] = [
    {
      key: "reference",
      header: "Reference",
      primary: true,
      cell: (r) => (
        <span className={styles.stack}>
          <code className={styles.claim}>{r.external_ref}</code>
          <span className={styles.secondary}>{refKindLabel(r.ref_kind)}</span>
        </span>
      ),
    },
    {
      key: "source",
      header: "Source",
      cell: (r) => (
        <span className={styles.stack}>
          <span>{r.source.name}</span>
          <span className={styles.secondary}>{connectorLabel(r.source.connector_type)}</span>
        </span>
      ),
    },
    { key: "records", header: "Records waiting", short: true, mono: true, align: "end", cell: (r) => r.records_waiting.toLocaleString("en-GB") },
    { key: "seen", header: "In the sync of", short: true, mono: true, cell: (r) => formatDayTime(r.seen_at) },
    {
      key: "state",
      header: "State",
      cell: (r) =>
        r.state === "unmapped" ? (
          <StatusMark tone="neutral">Not mapped</StatusMark>
        ) : (
          <span className={styles.stack}>
            <StatusMark tone="time">Mapped — waiting for the next sync</StatusMark>
            {r.account && (
              <Link href={`/access/accounts/${r.account.id}`} className={styles.secondary}>
                {r.account.name}
              </Link>
            )}
          </span>
        ),
    },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      align: "end",
      cell: (r) =>
        r.state === "unmapped" ? (
          <span className={styles.rowActions}>
            <Button size="sm" icon={<Icon as={Link2} />} aria-label={`Map ${r.external_ref}`} onClick={() => setMapping({ mode: "map", reference: reference(r) })}>
              Map
            </Button>
          </span>
        ) : null,
    },
  ];

  return (
    <OpsPage
      aria-labelledby="page-title"
      trail={[
        { label: "Access", href: "/access" },
        { label: "Accounts", href: "/access/accounts" },
        { label: "Unmapped references" },
      ]}
    >
      <AdminGate forbidden={forbidden} body="Account administration is available to your organization's administrators. Being an administrator never gives access to content.">
        <OpsHead
          title="Unmapped references"
          deck="References your sources sent in their latest sync that no account matches. On an account-scoped source their records aren't indexed until the reference is mapped. Only the reference itself is shown — never the records."
        >
          <AccessViews />
        </OpsHead>
        {applied && (
          <AppliedLine change={applied} onDismiss={() => setApplied(null)}>
            <SyncNow source={applied.source} />
          </AppliedLine>
        )}
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load unmapped references" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={3} label="Loading unmapped references" />
        ) : (
          <DataTable
            caption="References the latest syncs could not map to an account"
            columns={columns}
            rows={items}
            rowKey={(r) => `${r.source.id}/${r.ref_kind}/${r.external_ref}`}
            stackBelow="768"
            empty={
              <StateBlock family="quiet" heading="Every reference is mapped" headingLevel="h2" compact>
                <p>The latest sync of each ticketing and chat source found an account for every record that names one.</p>
              </StateBlock>
            }
          />
        )}
        <MappingDialog
          target={mapping}
          onOpenChange={(open) => !open && setMapping(null)}
          onApplied={setApplied}
          onCreateAccount={() => {
            if (mapping && mapping.mode === "map") setCreatingFor(mapping.reference);
            setMapping(null);
          }}
        />
        <CreateAccountDialog
          open={creatingFor !== null}
          onOpenChange={(open) => !open && setCreatingFor(null)}
          onCreated={(account) => {
            const ref = creatingFor;
            setCreatingFor(null);
            if (ref) {
              const created: AccountRef = { id: account.id, key: account.key, name: account.name, lifecycle: account.lifecycle };
              setMapping({ mode: "map", reference: ref, account: created });
            }
          }}
        />
        <p className={styles.note}>
          Records of a source that isn&apos;t account-scoped are indexed without an account when their reference isn&apos;t mapped, so they don&apos;t appear here.
        </p>
      </AdminGate>
    </OpsPage>
  );
}
