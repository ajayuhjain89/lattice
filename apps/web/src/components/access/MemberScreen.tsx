"use client";

import { CheckCircle2, PauseCircle, PlayCircle, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useAccessMember } from "@/api/queries";
import type { DocumentRef, MemberAccess } from "@/api/types";
import { BoundaryInline } from "@/components/primitives/Boundary";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { LevelMeter } from "@/components/primitives/LevelMeter";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { Tag } from "@/components/primitives/Tag";
import { AdminGate, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { formatCount, formatTime, isUuid } from "@/lib/format";

import styles from "./Access.module.css";
import { ChangeAccessDialog, StatusDialog } from "./ChangeAccess";
import { accessSummary, groupOrigin, listNames, scopeLabel, type Part } from "./copy";
import { DocumentSearch } from "./DocumentSearch";
import { GrantsSection, Verdict } from "./Grants";
import { managedByIdentityProvider, MemberStatus } from "./MemberFacts";

/** A write that has just been applied, kept on the page (the toast only echoes it). */
export interface AppliedChange {
  title: string;
  detail?: string;
  at: string;
}

/**
 * One member's access (GET /v1/admin/access/members/{id}), answering "why can this person
 * read what they can?": the explanation in sentences, then the rules it is made of, each
 * with what it gives. Groups the administrator isn't in and grants on documents they can't
 * read are acknowledged once each -- never named, listed, counted or identified (D-18).
 * Changes are previewed as Now → After, need a reason and are audited; your own access
 * can't be changed here (D-17).
 */
export function MemberScreen({ id }: { id: string }) {
  const { isAdmin } = useWorkspace();
  const valid = isUuid(id);
  const query = useAccessMember(id, isAdmin && valid);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const missing = !valid || (query.isError && isApiError(query.error) && query.error.isNotFound);
  // The trail names the member only once the API has returned them (never the requested id).
  const name = query.data?.member.name;

  return (
    <OpsPage trail={[{ label: "Access", href: "/access" }, { label: name ?? "Member" }]}>
      <AdminGate forbidden={forbidden} body="Access management is available to your organization's administrators. Being an administrator never gives access to content.">
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
          <RuleSkeleton variant="ledger" rows={5} label="Loading this member's access" />
        ) : (
          <Member access={query.data} />
        )}
      </AdminGate>
    </OpsPage>
  );
}

function Member({ access }: { access: MemberAccess }) {
  const [changing, setChanging] = useState(false);
  const [status, setStatus] = useState(false);
  const [applied, setApplied] = useState<AppliedChange | null>(null);
  const m = access.member;
  const canChange = !access.is_self && m.status !== "removed";
  // Provisioned by the identity provider (SCIM): it owns suspension and reactivation.
  const managed = managedByIdentityProvider(m);

  return (
    <>
      <header className={styles.memberHead}>
        <h1 className={styles.memberName}>{m.name}</h1>
        <p className={styles.identity}>
          <span className={styles.email}>{m.email}</span>
          {m.role_label && <span>{m.role_label}</span>}
        </p>
        <p className={styles.facts}>
          <MemberStatus status={m.status} />
          {m.is_admin && <span>Administrator — no content access from this</span>}
          {managed && <Tag tone="quiet">Identity provider</Tag>}
        </p>
      </header>

      <div className={styles.lead} data-testid="access-summary">
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
            {!managed && (
              <Button icon={<Icon as={m.status === "active" ? PauseCircle : PlayCircle} />} onClick={() => setStatus(true)}>
                {m.status === "active" ? "Suspend" : "Reactivate"}
              </Button>
            )}
          </>
        ) : null}
        {canChange && managed && (
          <p className={styles.note} data-testid="managed-note">
            {m.name}&apos;s name, e-mail and whether they&apos;re active come from your identity provider. Suspend or reactivate them
            there; access is managed here.
          </p>
        )}
        {!canChange && (
          <p className={styles.note} data-testid="no-change-note">
            {access.is_self ? "This is you. Another administrator must change your access." : `${m.name} has been removed; access can't be changed.`}
          </p>
        )}
      </div>

      {applied && <AppliedLine change={applied} onDismiss={() => setApplied(null)} />}

      <Reach access={access} />
      <GrantsSection member={access} canChange={canChange} onApplied={setApplied} />
      <DocumentCheck access={access} />

      {canChange && <ChangeAccessDialog member={access} open={changing} onOpenChange={setChanging} onApplied={setApplied} />}
      {canChange && !managed && <StatusDialog member={access} open={status} onOpenChange={setStatus} onApplied={setApplied} />}
    </>
  );
}

/** The result of the last write, on the page until dismissed or replaced. */
function AppliedLine({ change, onDismiss }: { change: AppliedChange; onDismiss: () => void }) {
  return (
    <div className={styles.applied} role="status" data-testid="applied-change">
      <span className={styles.appliedIcon}>
        <Icon as={CheckCircle2} />
      </span>
      <p className={styles.appliedText}>
        <strong>{change.title}</strong> <span className={styles.when}>{formatTime(change.at)} UTC</span>
        {change.detail && <span className={styles.appliedDetail}>{change.detail}</span>}
        <span className={styles.appliedDetail}>
          Recorded in the <Link href="/access/audit">audit log</Link> with your name and reason.
        </span>
      </p>
      <Button size="sm" variant="quiet" icon={<Icon as={X} />} aria-label="Dismiss" onClick={onDismiss} />
    </div>
  );
}

/* --- the rules access is made of ------------------------------------------------------ */

function Reach({ access }: { access: MemberAccess }) {
  const m = access.member;
  const name = m.name;
  const visibleGroups = access.groups;
  return (
    <OpsSection id="reach-heading" title="How this access is made up">
      {m.status !== "active" && (
        <NoticeLine icon={<Icon as={PauseCircle} />}>
          {m.status === "suspended"
            ? `${name} is suspended: none of these rules are in effect until they're reactivated. They are kept as they are.`
            : `${name} has been removed: none of these rules are in effect.`}
        </NoticeLine>
      )}
      <dl className={styles.reach} data-testid="reach">
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Clearance</dt>
          <dd className={styles.value}>
            <LevelMeter level={m.clearance} />
          </dd>
          <dd className={styles.effect}>
            {m.clearance === "confidential"
              ? "Internal and Confidential content the other rules allow. Restricted content only through a direct grant."
              : "Internal content the other rules allow. Confidential and Restricted content only through a direct grant."}
          </dd>
        </div>
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Accounts</dt>
          <dd className={styles.value}>
            {m.account_scope === "all" ? scopeLabel("all") : access.accounts.length ? listNames(access.accounts.map((a) => a.name)) : "None assigned"}
          </dd>
          <dd className={styles.effect}>
            {m.account_scope === "all"
              ? "Content for every account."
              : access.accounts.length
                ? "Content for these accounts, and content that isn't tied to an account."
                : "Only content that isn't tied to an account."}
          </dd>
        </div>
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Departments</dt>
          <dd className={styles.value}>{access.departments.length ? listNames(access.departments.map((d) => d.name)) : "None"}</dd>
          <dd className={styles.effect}>
            {access.departments.length ? "Also content restricted to these departments." : "Content restricted to a department isn't readable through the rules."}
          </dd>
        </div>
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Groups and channels</dt>
          <dd className={styles.value}>
            {visibleGroups.length === 0 && !access.has_hidden_groups && "None"}
            {visibleGroups.length > 0 && (
              <ul className={styles.groups}>
                {visibleGroups.map((g) => (
                  <li key={g.id}>
                    <span className={styles.groupName}>{g.name}</span>
                    <span className={styles.secondary}>
                      {groupOrigin(g)} · {formatCount(g.members.length, "member", "members")}
                      {g.members.length > 0 && `: ${memberNames(g.members.map((x) => x.name))}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {access.has_hidden_groups && <BoundaryInline>Also in private channels you aren&apos;t in</BoundaryInline>}
          </dd>
          <dd className={styles.effect}>
            {visibleGroups.length || access.has_hidden_groups ? "What's shared with these groups and channels." : "Nothing is shared through a group or channel."}
            {access.has_hidden_groups && <span className={styles.secondary}>Private channels follow their source and can&apos;t be changed here.</span>}
          </dd>
        </div>
        <div className={styles.reachRow}>
          <dt className={styles.rule}>Direct grants</dt>
          <dd className={styles.value}>
            {access.grants.length ? `${formatCount(access.grants.length, "document", "documents")} you can read` : access.has_other_grants ? null : "None"}
            {access.has_other_grants && <BoundaryInline>Also documents you can&apos;t read</BoundaryInline>}
          </dd>
          <dd className={styles.effect}>Each shares one document, whatever the rules above say. Listed below.</dd>
        </div>
      </dl>
    </OpsSection>
  );
}

function DocumentCheck({ access }: { access: MemberAccess }) {
  const [doc, setDoc] = useState<DocumentRef | null>(null);
  return (
    <OpsSection id="check-heading" title="Check a document">
      <p className={styles.note}>Why can or can&apos;t {access.member.name} read a document? Pick one you can read.</p>
      <DocumentSearch label="Document to check" selected={doc} onSelect={setDoc} />
      {doc && <Verdict memberId={access.member.id} name={access.member.name} doc={doc} />}
    </OpsSection>
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

