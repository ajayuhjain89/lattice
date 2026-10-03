"use client";

import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccessMembers } from "@/api/queries";
import type { MemberSummary } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Field, SearchInput } from "@/components/primitives/Field";
import { LevelMeter } from "@/components/primitives/LevelMeter";
import { StateBlock } from "@/components/primitives/StateBlock";
import { AdminGate, OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";

import styles from "./Access.module.css";
import { AccessViews } from "./AccessViews";
import { accountsSummary, managedByIdentityProvider, MemberStatus } from "./MemberFacts";

/**
 * Access (design-direction §6.8): the members of the organization, found by name or
 * e-mail (GET /v1/admin/access/members?q= -- the API searches; nothing is filtered or
 * enumerated here), each with the broad facts of their access. A member's own page
 * explains why, and is where changes are previewed, given a reason and audited.
 */
export function AccessScreen() {
  const { isAdmin, me } = useWorkspace();
  const [filter, setFilter] = useState("");
  const q = useDeferredValue(filter.trim());
  const query = useAccessMembers(q, isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const members = query.data?.pages.flatMap((p) => p.items) ?? [];

  const columns: Column<MemberSummary>[] = [
    {
      key: "member",
      header: "Member",
      primary: true,
      cell: (m) => (
        <span className={styles.who}>
          <Link href={`/access/members/${m.id}`} className={styles.memberLink}>
            {m.name}
          </Link>
          <span className={styles.email}>{m.email}</span>
          {(m.role_label || m.is_admin) && (
            <span className={styles.secondary}>{[m.role_label, m.is_admin ? "Administrator" : null].filter(Boolean).join(" · ")}</span>
          )}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      short: true,
      cell: (m) => (
        <span className={styles.stack}>
          <MemberStatus status={m.status} />
          {managedByIdentityProvider(m) && <span className={styles.secondary}>Managed by your identity provider</span>}
        </span>
      ),
    },
    { key: "clearance", header: "Clearance", short: true, width: "9.5rem", cell: (m) => <LevelMeter level={m.clearance} /> },
    { key: "accounts", header: "Accounts", short: true, cell: (m) => accountsSummary(m) },
    {
      key: "departments",
      header: "Departments",
      short: true,
      cell: (m) => (m.departments.length ? m.departments.map((d) => d.name).join(", ") : <span className={styles.secondary}>None</span>),
    },
  ];

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="Access management is available to your organization's administrators. Being an administrator never gives access to content.">
        <OpsHead
          title="Access"
          deck={`Who can read what in ${me.organization.name}, and why. Every change is previewed, needs a reason and is recorded in the audit log. Being an administrator gives you no access to content.`}
        >
          <AccessViews />
        </OpsHead>
        <div className={styles.find}>
          <Field label="Find a member" help="Searches names and e-mail addresses.">
            {(control) => (
              <SearchInput {...control} value={filter} onValueChange={setFilter} maxLength={100} placeholder="Name or e-mail" clearLabel="Clear member search" />
            )}
          </Field>
          {/* Results are announced as they arrive; with none, the state below says so on screen. */}
          <p className={members.length ? styles.shown : "visually-hidden"} role="status">
            {!query.data
              ? ""
              : members.length === 0
                ? q
                  ? `No member matches “${q}”`
                  : "No members yet"
                : `${members.length.toLocaleString("en-GB")}${query.hasNextPage ? "+" : ""} ${members.length === 1 ? "member" : "members"} shown`}
          </p>
        </div>
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load members" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : (
          <DataTable
            caption="Members and their access"
            columns={columns}
            rows={members}
            rowKey={(m) => m.id}
            loading={!query.data}
            interactiveRows
            stackBelow="768"
            empty={
              <StateBlock family="quiet" heading={q ? `No member matches “${q}”` : "No members yet"} headingLevel="h2" compact>
                <p>{q ? "Try part of a name or an e-mail address." : "Members appear here once they're provisioned or invited."}</p>
              </StateBlock>
            }
            footer={
              query.hasNextPage ? (
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show more members
                </Button>
              ) : undefined
            }
          />
        )}
      </AdminGate>
    </OpsPage>
  );
}
