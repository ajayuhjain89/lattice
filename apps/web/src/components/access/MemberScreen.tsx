"use client";

import { PauseCircle, PlayCircle, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccessMember } from "@/api/queries";
import type { DocumentRef, MemberAccess } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { Tag } from "@/components/primitives/Tag";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { humanize, isUuid, levelLabel } from "@/lib/format";

import styles from "./Access.module.css";
import { AccessFrame, Loading } from "./AccessFrame";
import { ChangeAccessDialog, StatusDialog } from "./ChangeAccess";
import { accessSummary, groupLabel, scopeLabel, type Part } from "./copy";
import { DocumentSearch } from "./DocumentSearch";
import { GrantsSection, Verdict } from "./Grants";

/**
 * One member's access (GET /v1/admin/access/members/{id}): the explanation in sentences,
 * the facts behind it, groups (a private channel you aren't in stays unnamed), direct
 * grants on documents you can read, and a check against any document you can read.
 * Changes are previewed, need a reason and are audited; your own access can't be changed.
 */
export function MemberScreen({ id }: { id: string }) {
  const { isAdmin } = useWorkspace();
  const valid = isUuid(id);
  const query = useAccessMember(id, isAdmin && valid);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const missing = !valid || (query.isError && isApiError(query.error) && query.error.isNotFound);
  const name = query.data?.member.name;

  return (
    <AccessFrame trail={[{ label: name ?? "Member" }]} forbidden={forbidden}>
      {missing ? (
        <NotAvailable
          label="Not available"
          title="This member isn't available"
          body="They may have been removed, or the link is wrong."
          action={{ href: "/access", label: "Back to Access" }}
        />
      ) : query.isError ? (
        <ErrorState error={query.error} title="Couldn't load this member's access" onRetry={() => query.refetch()} retrying={query.isFetching} headingLevel="h1" />
      ) : !query.data ? (
        <Loading label="Loading this member's access" lines={5} />
      ) : (
        <Member access={query.data} />
      )}
    </AccessFrame>
  );
}

function Member({ access }: { access: MemberAccess }) {
  const [changing, setChanging] = useState(false);
  const [status, setStatus] = useState(false);
  const m = access.member;
  const canChange = !access.is_self && m.status !== "removed";
  return (
    <>
      <header className={styles.head}>
        <h1 className={styles.title}>{m.name}</h1>
        <p className={styles.meta}>
          <span className={styles.fact}>{m.email}</span>
          {m.role_label && <span className={styles.fact}>{m.role_label}</span>}
          {m.status !== "active" && <Tag tone="muted">{humanize(m.status)}</Tag>}
          {m.is_admin && <Tag tone="quiet">Admin</Tag>}
        </p>
      </header>

      <div className={styles.summary} data-testid="access-summary">
        {accessSummary(access).map((sentence, i) => (
          <p key={i}>
            <Sentence parts={sentence} />
          </p>
        ))}
      </div>

      <div className={styles.actions}>
        {canChange ? (
          <>
            <Button variant="primary" icon={<Icon as={SlidersHorizontal} />} onClick={() => setChanging(true)}>
              Change access
            </Button>
            <Button icon={<Icon as={m.status === "active" ? PauseCircle : PlayCircle} />} onClick={() => setStatus(true)}>
              {m.status === "active" ? "Suspend" : "Reactivate"}
            </Button>
          </>
        ) : (
          <p className={styles.selfNote}>
            {access.is_self
              ? "This is you. Another administrator must change your access."
              : `${m.name} has been removed; access can't be changed.`}
          </p>
        )}
      </div>

      <section className={styles.section} aria-labelledby="rules-label">
        <SectionLabel id="rules-label">Access rules</SectionLabel>
        <dl className={styles.facts}>
          <dt>Status</dt>
          <dd>{humanize(m.status)}</dd>
          <dt>Clearance</dt>
          <dd>{levelLabel(m.clearance)}</dd>
          <dt>Account scope</dt>
          <dd>{scopeLabel(m.account_scope)}</dd>
          <dt>Assigned accounts</dt>
          <dd>{access.accounts.map((a) => a.name).join(", ") || <span className={styles.off}>None</span>}</dd>
          <dt>Departments</dt>
          <dd>{access.departments.map((d) => d.name).join(", ") || <span className={styles.off}>None</span>}</dd>
        </dl>
      </section>

      <section className={styles.section} aria-labelledby="groups-label">
        <SectionLabel id="groups-label" meta={access.groups.length ? `${access.groups.length}` : undefined}>
          Groups and channels
        </SectionLabel>
        {access.groups.length === 0 ? (
          <p className={styles.off}>Not in any group or synced channel.</p>
        ) : (
          <ul className={styles.list}>
            {access.groups.map((g) => (
              <li key={g.id} className={styles.row}>
                <span className={styles.rowBody}>
                  <span className={styles.primary}>{groupLabel(g)}</span>
                  <span className={styles.secondary}>
                    {g.origin === "admin" ? "Administrator-defined" : "Follows the source"}
                    {g.reference && (
                      <>
                        {" · "}
                        <span className={styles.mono} title="Opaque reference: you aren't a member of this channel">
                          {g.reference}
                        </span>
                      </>
                    )}
                  </span>
                  <span className={styles.secondary}>
                    {g.members.length} {g.members.length === 1 ? "member" : "members"}: {memberNames(g.members.map((x) => x.name))}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <GrantsSection member={access} canChange={canChange} />
      <DocumentCheck access={access} />

      {canChange && <ChangeAccessDialog member={access} open={changing} onOpenChange={setChanging} />}
      {canChange && <StatusDialog member={access} open={status} onOpenChange={setStatus} />}
    </>
  );
}

function DocumentCheck({ access }: { access: MemberAccess }) {
  const [doc, setDoc] = useState<DocumentRef | null>(null);
  return (
    <section className={styles.section} aria-labelledby="check-label">
      <SectionLabel id="check-label">Check a document</SectionLabel>
      <p className={styles.note}>Why can or can&apos;t {access.member.name} read a document? Pick one you can read.</p>
      <DocumentSearch label="Document to check" selected={doc} onSelect={setDoc} />
      {doc && <Verdict memberId={access.member.id} name={access.member.name} doc={doc} />}
    </section>
  );
}

function Sentence({ parts }: { parts: Part[] }) {
  return (
    <>
      {parts.map((p, i) => (typeof p === "string" ? <span key={i}>{p}</span> : <strong key={i}>{p.strong}</strong>))}
    </>
  );
}

function memberNames(names: string[]): string {
  const shown = names.slice(0, 8);
  return names.length > shown.length ? `${shown.join(", ")} and ${names.length - shown.length} more` : shown.join(", ");
}
