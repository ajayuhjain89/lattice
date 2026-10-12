"use client";

import { Building2, Link2Off } from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccountsList, useUnmappedReferences } from "@/api/queries";
import type { AccountLifecycle, AccountSummary } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Field, SearchInput, Select } from "@/components/primitives/Field";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { ErrorState } from "@/components/states/ErrorState";
import { formatCount, formatDay } from "@/lib/format";

import styles from "./Access.module.css";
import { AccessViews } from "./AccessViews";
import { CreateAccountDialog } from "./AccountDialogs";
import { businessStatusLabel } from "./accountsCopy";

/**
 * Accounts (Phase 12, P12.3; design-direction §6.8): the organization's canonical
 * customer accounts as a ledger -- name and key, business status, lifecycle, how many
 * source references map to each and how many members are assigned it. Administration
 * only: no activity, no documents, no counts of content (the account timeline is post-v1).
 * Creating an account grants nobody anything.
 */
export function AccountsScreen() {
  const [filter, setFilter] = useState("");
  const [lifecycle, setLifecycle] = useState<AccountLifecycle | "">("");
  const [creating, setCreating] = useState(false);
  const q = useDeferredValue(filter.trim());
  const query = useAccountsList(q, lifecycle || null, true);
  const unmapped = useUnmappedReferences(true);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const accounts = query.data?.pages.flatMap((p) => p.items) ?? [];
  const waiting = (unmapped.data?.items ?? []).filter((r) => r.state === "unmapped");

  const columns: Column<AccountSummary>[] = [
    {
      key: "account",
      header: "Account",
      primary: true,
      cell: (a) => (
        <span className={styles.who}>
          <Link href={`/access/accounts/${a.id}`} className={styles.memberLink}>
            {a.name}
          </Link>
          <span className={styles.email}>{a.key}</span>
          {a.aliases.length > 0 && <span className={styles.secondary}>Also {a.aliases.join(", ")}</span>}
        </span>
      ),
    },
    { key: "status", header: "Business status", short: true, cell: (a) => businessStatusLabel(a.status) },
    {
      key: "lifecycle",
      header: "Lifecycle",
      short: true,
      cell: (a) =>
        a.lifecycle === "archived" ? (
          <StatusMark tone="neutral">Archived{a.archived_at ? ` ${formatDay(a.archived_at)}` : ""}</StatusMark>
        ) : (
          <StatusMark tone="healthy">Active</StatusMark>
        ),
    },
    { key: "references", header: "References", short: true, mono: true, align: "end", cell: (a) => a.references.toLocaleString("en-GB") },
    { key: "members", header: "Members assigned", short: true, mono: true, align: "end", cell: (a) => a.assigned_members.toLocaleString("en-GB") },
  ];

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="Account administration is available to your organization's administrators. Being an administrator never gives access to content.">
        <OpsHead
          title="Accounts"
          deck="Your customer accounts, and the references your sources use for them. Changes need a reason and are recorded in the audit log. Managing accounts gives you no access to their content."
        >
          <AccessViews />
        </OpsHead>
        {waiting.length > 0 && (
          <NoticeLine icon={<Icon as={Link2Off} />}>
            <span data-testid="unmapped-notice">
              {formatCount(waiting.length, "reference", "references")} from your sources {waiting.length === 1 ? "isn't" : "aren't"} mapped to an account, so{" "}
              {waiting.length === 1 ? "its" : "their"} records aren&apos;t indexed. <Link href="/access/accounts/references">Review unmapped references</Link>
            </span>
          </NoticeLine>
        )}
        <div className={styles.find}>
          <div className={styles.filters}>
            <Field label="Find an account" help="Searches names, keys and other names.">
              {(control) => (
                <SearchInput {...control} value={filter} onValueChange={setFilter} maxLength={100} placeholder="Name or key" clearLabel="Clear account search" />
              )}
            </Field>
            <Field label="Lifecycle">
              {(control) => (
                <Select {...control} value={lifecycle} onChange={(e) => setLifecycle(e.target.value as AccountLifecycle | "")}>
                  <option value="">All</option>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                </Select>
              )}
            </Field>
          </div>
          <Button icon={<Icon as={Building2} />} onClick={() => setCreating(true)} className={styles.addMember}>
            Create account
          </Button>
        </div>
        <div className={`${styles.shownRow} ${styles.shownRowSplit}`}>
          {waiting.length === 0 && unmapped.data ? (
            <Link href="/access/accounts/references" className={styles.sideLink}>
              Unmapped references
            </Link>
          ) : (
            <span />
          )}
          <p className={accounts.length ? styles.shown : "visually-hidden"} role="status">
            {!query.data
              ? ""
              : accounts.length === 0
                ? "No accounts shown"
                : `${accounts.length.toLocaleString("en-GB")}${query.hasNextPage ? "+" : ""} ${accounts.length === 1 ? "account" : "accounts"} shown`}
          </p>
        </div>
        <CreateAccountDialog open={creating} onOpenChange={setCreating} />
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load accounts" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : (
          <DataTable
            caption="Accounts"
            columns={columns}
            rows={accounts}
            rowKey={(a) => a.id}
            loading={!query.data}
            interactiveRows
            stackBelow="768"
            empty={
              <StateBlock family="quiet" heading={q || lifecycle ? "No account matches" : "No accounts yet"} headingLevel="h2" compact>
                <p>{q || lifecycle ? "Try part of a name or key, or another lifecycle." : "Create your customer accounts here, then map your sources' references to them."}</p>
              </StateBlock>
            }
            footer={
              query.hasNextPage ? (
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show more accounts
                </Button>
              ) : undefined
            }
          />
        )}
      </AdminGate>
    </OpsPage>
  );
}
