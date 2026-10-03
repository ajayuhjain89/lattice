"use client";

import { XCircle } from "lucide-react";
import Link from "next/link";

import { isApiError } from "@/api/errors";
import { useAdminActivity } from "@/api/queries";
import type { OperationalActivity } from "@/api/types";
import { BoundaryBand } from "@/components/primitives/Boundary";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { AdminGate, OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { shortStatus } from "@/components/shell/status-labels";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { formatDay, formatDayTime, formatDuration, formatTime, humanize } from "@/lib/format";

import styles from "./OrganizationActivity.module.css";
import { ActivityViews } from "./ActivityViews";

/**
 * The organization's runs (administrators; GET /v1/admin/activity): who asked, when, how
 * the request ran -- status, model, duration, tokens -- and never what was asked, answered
 * or found (SEC-15, D-5). The boundary is drawn once, over the whole ledger, because it
 * holds for every row alike.
 */
export function OrganizationActivityScreen() {
  const { isAdmin, me } = useWorkspace();
  const query = useAdminActivity(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  const columns: Column<OperationalActivity>[] = [
    {
      key: "when",
      header: "When",
      primary: true,
      width: "10rem",
      cell: (r) => (
        <time className={styles.when} dateTime={r.created_at} title={formatDayTime(r.created_at) ?? undefined}>
          {formatDay(r.created_at)}, {formatTime(r.created_at)}
        </time>
      ),
    },
    {
      key: "member",
      header: "Member",
      cell: (r) => (
        <Link href={`/access/members/${r.membership_id}`} className={styles.member}>
          {r.member_name}
        </Link>
      ),
    },
    { key: "status", header: "Status", width: "11rem", cell: (r) => <RunStatus r={r} /> },
    { key: "model", header: "Model", cell: (r) => <Model r={r} /> },
    { key: "duration", header: "Duration", align: "end", mono: true, width: "6rem", cell: (r) => formatDuration(r.duration_ms) ?? "—" },
    {
      key: "tokens",
      header: "Tokens in / out",
      align: "end",
      mono: true,
      width: "8rem",
      cell: (r) => (r.generation_calls === 0 ? "—" : `${r.input_tokens.toLocaleString("en-GB")} / ${r.output_tokens.toLocaleString("en-GB")}`),
    },
    {
      key: "trace",
      header: "Run",
      hideHeader: true,
      width: "7rem",
      cell: (r) => (
        <Link href={`/traces/${r.trace_id}`} className={styles.run} aria-label={`Run details: ${r.member_name}, ${formatDayTime(r.created_at)}`}>
          Run details
        </Link>
      ),
    },
  ];

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden}>
        <OpsHead
          title="Activity"
          deck={`How each question in ${me.organization.name} ran: who asked, when, the model it used and how long it took.`}
        >
          <ActivityViews />
        </OpsHead>
        <BoundaryBand>
          Questions, answers and evidence aren&apos;t visible to administrators. Each run shows who asked, when and how it ran —
          never what was asked or found.
        </BoundaryBand>
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load activity" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : query.data && items.length === 0 ? (
          <StateBlock family="quiet" heading={`No questions have been asked in ${me.organization.name} yet`}>
            <p>Each question anyone asks appears here as a run: who, when, and how it ran.</p>
          </StateBlock>
        ) : (
          <DataTable
            caption="Organization runs (operational view)"
            columns={columns}
            rows={items}
            rowKey={(r) => r.trace_id}
            loading={!query.data}
            density="dense"
            footer={
              query.hasNextPage ? (
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show older runs
                </Button>
              ) : query.data ? (
                <p className={styles.end}>No older runs.</p>
              ) : undefined
            }
          />
        )}
      </AdminGate>
    </OpsPage>
  );
}

function RunStatus({ r }: { r: OperationalActivity }) {
  const failed = r.status === "failed";
  const code = r.error_code ?? (r.status_reason ? humanize(r.status_reason) : null);
  return (
    <span className={styles.stack}>
      <span className={failed ? styles.failed : undefined}>
        {failed && <Icon as={XCircle} size={14} />}
        {shortStatus(r.status)}
      </span>
      {code && <span className={styles.code}>{code}</span>}
    </span>
  );
}

function Model({ r }: { r: OperationalActivity }) {
  // A configured provider that wasn't called (policy, kill switch, no evidence) is not
  // shown as if it had answered.
  if (!r.provider || r.generation_calls === 0) return <span className={styles.secondary}>No model call</span>;
  return (
    <span className={styles.stack}>
      <span>{r.provider}</span>
      <span className={styles.code}>
        {r.model}
        {r.generation_calls > 1 ? ` · ${r.generation_calls} calls` : ""}
      </span>
    </span>
  );
}
