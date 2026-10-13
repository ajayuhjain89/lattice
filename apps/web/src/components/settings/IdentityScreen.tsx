"use client";

import { CheckCircle2, Copy, ExternalLink, KeyRound, Plus, Power, RefreshCw, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useCheckConnection, useIdentity, useProvisioningActivity, useStartTestSignIn, useTestSignIn } from "@/api/queries";
import type { AuditEvent, IdentitySettings, IdentitySetupStatus, OidcConnection, ScimToken } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Icon } from "@/components/primitives/icons";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StatusMark } from "@/components/primitives/StatusMark";
import { useToast } from "@/components/primitives/Toast";
import { AdminGate, OpsHead, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { formatCount, formatDayTime, formatTime } from "@/lib/format";

import { checkMessage, clientSecretLabel, identityError, issuerAccessLabel, testMessage, testTitle, lowerFirst, tokenLabel, tokenName } from "./identity-copy";
import { IdentityDialogs, type Applied, type IdentityDialog } from "./IdentityDialogs";
import styles from "./IdentityScreen.module.css";
import { STATE } from "./setup-model";

/**
 * Identity (phase-12-plan §7, §10, C5): how the organization's people sign in -- its OIDC
 * connection -- and how its identity provider keeps them up to date -- SCIM tokens. Every
 * state, verdict and refusal is the API's; this page says them and offers the actions the
 * API accepts. It never shows a client secret, a token after its one reveal, keys or
 * resolved addresses. Being an administrator never gives access to content.
 */
export function IdentityScreen() {
  const { isAdmin, me } = useWorkspace();
  const query = useIdentity(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const [dialog, setDialog] = useState<IdentityDialog | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const search = useSearchParams();
  const testId = search.get("test");
  const router = useRouter();
  const pathname = usePathname();
  const dismissTest = () => router.replace(pathname, { scroll: false });

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="The organization's identity settings are visible to its administrators.">
        <OpsHead
          title="Identity"
          deck={`How people sign in to ${me.organization.name} through your identity provider, and how it keeps members up to date. Lattice never stores a password.`}
        />
        {testId && /^[0-9a-f-]{36}$/.test(testId) && (
          <TestResult
            id={testId}
            onDismiss={dismissTest}
            onContinue={(issuer, claim) => {
              dismissTest();
              setDialog({ kind: "identity", issuer, claim });
            }}
          />
        )}
        {applied && <AppliedLine applied={applied} onDismiss={() => setApplied(null)} />}
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load the identity settings" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={8} label="Loading the identity settings" />
        ) : (
          <>
            <SignIn settings={query.data} onOpen={setDialog} />
            <Provisioning settings={query.data} onOpen={setDialog} />
            <IdentityDialogs dialog={dialog} settings={query.data} onClose={() => setDialog(null)} onApplied={setApplied} />
          </>
        )}
      </AdminGate>
    </OpsPage>
  );
}

/* --- shared ------------------------------------------------------------------------------------ */

function Status({ status, id }: { status: IdentitySetupStatus; id: string }) {
  const state = STATE[status.state];
  return (
    <div className={styles.status} id={id} data-state={status.state} data-reason={status.reason}>
      <StatusMark tone={state.tone} icon={state.icon}>
        {state.word}
      </StatusMark>
      <p className={styles.statusMessage}>{status.message}</p>
    </div>
  );
}

function CopyValue({ value, label }: { value: string; label: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast({ title: `${label} copied` });
    } catch {
      toast({ title: "Couldn't copy", description: "Select the text and copy it instead." });
    }
  };
  return (
    <span className={styles.copyable}>
      <span className={styles.ledger}>{value}</span>
      <Button size="sm" variant="quiet" icon={<Icon as={Copy} />} aria-label={`Copy ${lowerFirst(label)}`} onClick={copy} />
    </span>
  );
}

function AppliedLine({ applied, onDismiss }: { applied: Applied; onDismiss: () => void }) {
  return (
    <div className={styles.applied} role="status" data-testid="applied-change">
      <span className={styles.appliedIcon}>
        <Icon as={CheckCircle2} />
      </span>
      <p className={styles.appliedText}>
        <strong>{applied.title}</strong> <span className={styles.when}>{formatTime(applied.at)} UTC</span>
        <span className={styles.appliedDetail}>
          {applied.detail} Recorded in the <Link href="/access/audit">audit log</Link> with your name and reason.
        </span>
      </p>
      <Button size="sm" variant="quiet" icon={<Icon as={X} />} aria-label="Dismiss" onClick={onDismiss} />
    </div>
  );
}

export function TestResult({ id, onDismiss, onContinue }: { id: string; onDismiss: () => void; onContinue: (issuer: string, claim: string) => void }) {
  const query = useTestSignIn(id);
  const test = query.data;
  if (query.isError) {
    return (
      <Callout tone="fault" label="Test sign-in not available" role="alert">
        <p>{isApiError(query.error) && query.error.isNotFound ? "This test sign-in isn't yours, or it has gone." : identityError(query.error)}</p>
        <p>
          <Button size="sm" onClick={onDismiss}>
            Dismiss
          </Button>
        </p>
      </Callout>
    );
  }
  if (!test) return <RuleSkeleton variant="ledger" rows={1} label="Reading the test sign-in" />;
  const good = test.outcome === "matched";
  return (
    <section className={styles.test} aria-labelledby="test-result" data-outcome={test.outcome ?? test.status} data-testid="test-result">
      <h2 className={styles.testTitle} id="test-result">
        <StatusMark tone={good ? "healthy" : test.status === "pending" ? "time" : "neutral"}>{testTitle(test)}</StatusMark>
      </h2>
      <p className={styles.testMessage} role="status">
        {testMessage(test)}
      </p>
      {test.identifier && (
        <p className={styles.testFacts}>
          {test.subject_claim}: <span className={styles.ledger}>{test.identifier}</span>
          {test.proposed && (
            <>
              {" "}
              · tested <span className={styles.ledger}>{test.issuer}</span>
            </>
          )}
        </p>
      )}
      <div className={styles.testActions}>
        {test.proof_available && (
          <Button size="sm" variant="primary" onClick={() => onContinue(test.issuer, test.subject_claim)}>
            Continue the change
          </Button>
        )}
        <Button size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </section>
  );
}

/* --- sign-in ------------------------------------------------------------------------------------ */

function SignIn({ settings, onOpen }: { settings: IdentitySettings; onOpen: (dialog: IdentityDialog) => void }) {
  const oidc = settings.oidc ?? null;
  return (
    <OpsSection id="sign-in-heading" title="Sign-in">
      <Status status={settings.sign_in_status} id="sign-in-status" />
      {!oidc ? (
        <div className={styles.empty}>
          <p className={styles.note}>
            {settings.can_add_connection
              ? "Connect your organization's OpenID Connect identity provider. Members sign in through it; nobody gets a Lattice password."
              : "A disabled connection still signs members in. Enable it again rather than adding another."}
          </p>
          {settings.can_add_connection && (
            <Button variant="primary" icon={<Icon as={Plus} />} onClick={() => onOpen({ kind: "add" })}>
              Connect identity provider
            </Button>
          )}
        </div>
      ) : (
        <Connection oidc={oidc} onOpen={onOpen} />
      )}
    </OpsSection>
  );
}

function Connection({ oidc, onOpen }: { oidc: OidcConnection; onOpen: (dialog: IdentityDialog) => void }) {
  const active = oidc.status === "active";
  const check = useCheckConnection();
  const test = useStartTestSignIn();
  const blocked = oidc.issuer_access === "not_permitted";
  const startTest = () => test.mutate({}, { onSuccess: (started) => window.location.assign(started.authorization_url) });
  return (
    <>
      {blocked && (
        <Callout tone="neutral" label="Waiting for your Lattice operator" labelAs="p">
          <p>
            This issuer is a private or non-standard address. Lattice reaches it only once your Lattice operator approves it for this organization; until then nobody can sign in
            through it.
          </p>
        </Callout>
      )}
      <dl className={styles.facts} data-testid="connection-facts">
        <div className={styles.wide}>
          <dt>Issuer</dt>
          <dd>
            <span className={styles.ledger}>{oidc.issuer}</span>
            <span className={styles.secondary}>{issuerAccessLabel(oidc.issuer_access)}</span>
          </dd>
        </div>
        <div>
          <dt>Connection</dt>
          <dd>{active ? "Active" : "Disabled: nobody can sign in through it"}</dd>
        </div>
        <div>
          <dt>Client ID</dt>
          <dd className={styles.ledger}>{oidc.client_id}</dd>
        </div>
        <div>
          <dt>Client secret</dt>
          <dd>
            {clientSecretLabel(oidc.client_secret)}
            {oidc.client_secret.set_at && <span className={styles.secondary}>Set {formatDayTime(oidc.client_secret.set_at)}</span>}
          </dd>
        </div>
        <div>
          <dt>Identifying claim</dt>
          <dd className={styles.ledger}>{oidc.subject_claim}</dd>
        </div>
        <div>
          <dt>Members signing in this way</dt>
          <dd>{formatCount(oidc.members_bound, "member", "members")}</dd>
        </div>
        <div className={styles.wide}>
          <dt>Sign-in URL</dt>
          <dd>
            <CopyValue value={oidc.sign_in_url} label="Sign-in URL" />
          </dd>
        </div>
        <div className={styles.wide}>
          <dt>Redirect URI</dt>
          <dd>
            <CopyValue value={oidc.redirect_uri} label="Redirect URI" />
            <span className={styles.secondary}>Register it with your identity provider for this client.</span>
          </dd>
        </div>
        <div>
          <dt>Last successful sign-in</dt>
          <dd className={styles.when}>{oidc.last_success_at ? `${formatDayTime(oidc.last_success_at)}` : "None yet"}</dd>
        </div>
        <div>
          <dt>Last test</dt>
          <dd className={styles.when}>
            {oidc.last_test ? `${oidc.last_test.kind === "check" ? "Check" : "Test sign-in"}: ${oidc.last_test.result.replaceAll("_", " ")} · ${formatDayTime(oidc.last_test.at)}` : "None yet"}
          </dd>
        </div>
      </dl>
      {active && (
        <div className={styles.actions} role="group" aria-label="Test the connection">
          <Button variant="primary" icon={<Icon as={ExternalLink} />} onClick={startTest} loading={test.isPending}>
            Test sign-in
          </Button>
          <Button icon={<Icon as={ShieldCheck} />} onClick={() => check.mutate(oidc.id)} loading={check.isPending}>
            Check connection
          </Button>
          <span className={styles.secondary}>A test sign-in takes you to your identity provider and back. It creates no session.</span>
        </div>
      )}
      {(check.data || check.isError || test.isError) && (
        <p className={styles.checkResult} role="status" data-testid="check-result" data-result={check.data?.result}>
          {check.data ? checkMessage(check.data) : identityError(check.error ?? test.error)}
        </p>
      )}
      <div className={styles.actions} role="group" aria-label="Change the connection">
        {active && (
          <>
            <Button size="sm" icon={<Icon as={KeyRound} />} onClick={() => onOpen({ kind: "secret" })}>
              {oidc.client_secret.state === "none" ? "Set client secret" : "Replace client secret"}
            </Button>
            {oidc.client_secret.state === "encrypted" && (
              <Button size="sm" onClick={() => onOpen({ kind: "remove_secret" })}>
                Remove client secret
              </Button>
            )}
            <Button size="sm" onClick={() => onOpen({ kind: "client_id" })}>
              Change client ID
            </Button>
            <Button size="sm" onClick={() => onOpen({ kind: "identity" })}>
              Change issuer or claim
            </Button>
          </>
        )}
        <Button size="sm" variant={active ? "danger" : "primary"} icon={<Icon as={Power} />} onClick={() => onOpen({ kind: "status", enabled: !active })}>
          {active ? "Disable sign-in" : "Enable sign-in"}
        </Button>
      </div>
    </>
  );
}

/* --- provisioning ------------------------------------------------------------------------------- */

function tokenState(token: ScimToken): string {
  if (token.revoked_at) return `Revoked ${formatDayTime(token.revoked_at)}`;
  return token.current_connection ? "Live" : "Live, for a disabled connection";
}

function Provisioning({ settings, onOpen }: { settings: IdentitySettings; onOpen: (dialog: IdentityDialog) => void }) {
  const scim = settings.scim;
  const active = settings.oidc?.status === "active";
  const columns: Column<ScimToken>[] = [
    { key: "token", header: "Token", primary: true, cell: (t) => tokenLabel(t) },
    { key: "created", header: "Created", mono: true, cell: (t) => `${formatDayTime(t.created_at)}` },
    { key: "used", header: "Last used", mono: true, cell: (t) => (t.last_used_at ? `${formatDayTime(t.last_used_at)}` : "Never"), short: true },
    { key: "state", header: "State", cell: (t) => tokenState(t) },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      align: "end",
      cell: (t) =>
        t.revoked_at ? null : (
          <span className={styles.rowActions}>
            {active && (
              <Button size="sm" icon={<Icon as={RefreshCw} />} onClick={() => onOpen({ kind: "token_rotate", token: t })} aria-label={`Rotate ${tokenName(t)}`}>
                Rotate
              </Button>
            )}
            <Button size="sm" variant="danger" onClick={() => onOpen({ kind: "token_revoke", token: t })} aria-label={`Revoke ${tokenName(t)}`}>
              Revoke
            </Button>
          </span>
        ),
    },
  ];
  return (
    <OpsSection
      id="provisioning-heading"
      title="Provisioning (SCIM)"
      aside={
        active ? (
          <Button size="sm" variant="primary" icon={<Icon as={Plus} />} onClick={() => onOpen({ kind: "token_create" })}>
            Create token
          </Button>
        ) : undefined
      }
    >
      <Status status={scim.status} id="provisioning-status" />
      <dl className={styles.facts} data-testid="scim-facts">
        <div className={styles.wide}>
          <dt>SCIM base URL</dt>
          <dd>
            <CopyValue value={scim.base_url} label="SCIM base URL" />
          </dd>
        </div>
        <div className={styles.wide}>
          <dt>Identity rule</dt>
          <dd>
            {scim.identifying_claim ? (
              <>
                Each person&apos;s externalId must equal their <span className={styles.ledger}>{scim.identifying_claim}</span> claim. People only; groups are managed in Lattice.
              </>
            ) : (
              "Connect an identity provider first."
            )}
          </dd>
        </div>
        <div>
          <dt>Provisioned members</dt>
          <dd>{formatCount(scim.provisioned_members, "member", "members")}</dd>
        </div>
        <div>
          <dt>Refused in the last 24 hours</dt>
          <dd>{formatCount(scim.refused_recently, "request", "requests")}</dd>
        </div>
      </dl>
      <DataTable
        caption="Provisioning tokens"
        columns={columns}
        rows={scim.tokens}
        rowKey={(t) => t.id}
        stackBelow="768"
        empty={active ? "No token yet. Provisioning is optional." : "Provisioning needs an active sign-in connection."}
      />
      <p className={styles.note}>
        Tokens don&apos;t expire. <strong>Rotate</strong> replaces a token at once: the provider is refused until you paste the new one. For no interruption, create a second
        token, switch your identity provider to it, then revoke the old one. New members get the least access; nothing the provider sends sets anyone&apos;s access.
      </p>
      <Activity />
    </OpsSection>
  );
}

const ACTION_LABEL: Record<string, string> = {
  "access.member_changed": "Changed a member",
  "access.identity_changed": "Updated a member's details",
  "scim.request_refused": "Request refused",
};

const REFUSAL: Record<string, string> = {
  manual_member_conflict: "the person exists as a member added by hand",
  identity_conflict: "the person exists with another identity",
  immutable_id: "it tried to change a person's externalId",
};

function eventLine(event: AuditEvent): string {
  const label = ACTION_LABEL[event.action] ?? event.action;
  if (event.action === "scim.request_refused") {
    const reason = event.details.find((d) => d.field === "reason")?.after;
    return `${label}: ${(reason && REFUSAL[reason]) ?? "refused"}${event.member?.name ? ` (${event.member.name})` : ""}`;
  }
  return event.member?.name ? `${label}: ${event.member.name}` : label;
}

function Activity() {
  const { isAdmin } = useWorkspace();
  const query = useProvisioningActivity(isAdmin);
  const items = query.data?.items ?? [];
  return (
    <div className={styles.activity}>
      <h3 className={styles.subTitle} id="provisioning-activity">
        Recent provisioning activity
      </h3>
      {query.isError ? (
        <p className={styles.note}>The activity couldn&apos;t be loaded.</p>
      ) : !query.data ? (
        <RuleSkeleton variant="ledger" rows={2} label="Loading the provisioning activity" />
      ) : items.length === 0 ? (
        <p className={styles.note}>Nothing from the identity provider yet.</p>
      ) : (
        <ol className={styles.ledgerList} aria-labelledby="provisioning-activity">
          {items.map((event) => (
            <li key={event.id}>
              <span className={styles.when}>{formatDayTime(event.created_at)}</span>
              <span>{eventLine(event)}</span>
            </li>
          ))}
        </ol>
      )}
      <p className={styles.note}>
        The <Link href="/access/audit">audit log</Link> has every change, with its details.
      </p>
    </div>
  );
}
