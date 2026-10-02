"use client";

import { Search } from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccessMembers } from "@/api/queries";
import type { MemberSummary } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { Tag } from "@/components/primitives/Tag";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { humanize, levelLabel } from "@/lib/format";

import styles from "./Access.module.css";
import { AccessFrame, AccessTabs, Loading } from "./AccessFrame";

/**
 * Access (security-model §10; design-system B12 "Tables"). The member directory, paged by
 * name (GET /v1/admin/access/members). Each member opens their access page, where
 * changes are previewed, given a reason and audited.
 */
export function AccessScreen() {
  const { isAdmin, me } = useWorkspace();
  const [filter, setFilter] = useState("");
  const q = useDeferredValue(filter.trim());
  const query = useAccessMembers(q, isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.status === 403;
  const members = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <AccessFrame trail={[]} forbidden={forbidden}>
      <header className={styles.head}>
        <h1 className={styles.title}>Access</h1>
        <p className={styles.deck}>
          Who can read what in {me.organization.name}, and why. Every change is previewed first, needs a reason, and is
          recorded in the audit log. Being an administrator gives you no access to content: documents appear here only if
          you can read them yourself.
        </p>
      </header>
      <AccessTabs />
      <section className={styles.section} aria-labelledby="members-label">
        <SectionLabel id="members-label" meta={query.data ? `${members.length}${query.hasNextPage ? "+" : ""} shown` : undefined}>
          Members
        </SectionLabel>
        <label className={styles.search}>
          <span className={styles.searchIcon} aria-hidden="true">
            <Icon as={Search} size={16} />
          </span>
          <span className="visually-hidden">Find a member by name or e-mail</span>
          <input
            className={styles.searchInput}
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find a member by name or e-mail"
            maxLength={100}
          />
        </label>
        {query.isError && !forbidden ? (
          <ErrorState error={query.error} title="Couldn't load members" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <Loading label="Loading members" />
        ) : members.length === 0 ? (
          <div className={styles.empty}>
            <p className={styles.emptyTitle}>{q ? "No member matches" : "No members yet"}</p>
            <p>{q ? "Try part of a name or an e-mail address." : "Members are provisioned from your identity provider."}</p>
          </div>
        ) : (
          <>
            <MembersTable members={members} />
            {query.hasNextPage && (
              <div className={styles.more}>
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show more members
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </AccessFrame>
  );
}

function MembersTable({ members }: { members: MemberSummary[] }) {
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className="visually-hidden">Members and their access</caption>
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Status</th>
              <th scope="col">Clearance</th>
              <th scope="col">Accounts</th>
              <th scope="col">Departments</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <th scope="row" className={styles.rowHead}>
                  <MemberLink m={m} />
                </th>
                <td>
                  <Status m={m} />
                </td>
                <td>{levelLabel(m.clearance)}</td>
                <td>{accountsSummary(m)}</td>
                <td>{m.departments.map((d) => d.name).join(", ") || <span className={styles.off}>None</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className={styles.cards} aria-label="Members and their access">
        {members.map((m) => (
          <li key={m.id} className={styles.card}>
            <MemberLink m={m} />
            <dl>
              <dt>Status</dt>
              <dd>
                <Status m={m} />
              </dd>
              <dt>Clearance</dt>
              <dd>{levelLabel(m.clearance)}</dd>
              <dt>Accounts</dt>
              <dd>{accountsSummary(m)}</dd>
              <dt>Departments</dt>
              <dd>{m.departments.map((d) => d.name).join(", ") || "None"}</dd>
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

function MemberLink({ m }: { m: MemberSummary }) {
  return (
    <span>
      <Link href={`/access/members/${m.id}`} className={styles.memberLink}>
        {m.name}
      </Link>
      <span className={styles.secondary}>
        {m.email}
        {m.role_label ? ` · ${m.role_label}` : ""}
      </span>
    </span>
  );
}

function Status({ m }: { m: MemberSummary }) {
  return (
    <span className={styles.statusCell}>
      <span className={m.status === "active" ? undefined : styles.warn}>{humanize(m.status)}</span>
      {m.is_admin && <Tag tone="quiet">Admin</Tag>}
    </span>
  );
}

function accountsSummary(m: MemberSummary): string {
  if (m.account_scope === "all") return "All accounts";
  return m.assigned_accounts === 0 ? "None assigned" : `${m.assigned_accounts} assigned`;
}
