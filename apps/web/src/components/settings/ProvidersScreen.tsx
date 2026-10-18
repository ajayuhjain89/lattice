"use client";

import { CheckCircle2, CircleSlash, FlaskConical, KeyRound, MoreHorizontal, Plus, Power, ShieldCheck, SlidersHorizontal, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { isApiError } from "@/api/errors";
import { useProviders, useTestProvider } from "@/api/queries";
import type { AiSetupStatus, ProviderOut, ProviderSettings } from "@/api/types";
import { BoundaryBand } from "@/components/primitives/Boundary";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { Icon } from "@/components/primitives/icons";
import { cellKey, Lattice, LatticeLegend, type LatticeAxis, type LatticeCell } from "@/components/primitives/Lattice";
import { LEVELS, LevelMeter } from "@/components/primitives/LevelMeter";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/primitives/Menu";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsHead, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { formatTime, humanize, levelLabel } from "@/lib/format";

import {
  approvalLine,
  byPurpose,
  ceilingWords,
  cellClause,
  credentialHeadline,
  credentialLine,
  dataTermsLabel,
  destinationBlocked,
  destinationLine,
  levelsList,
  providerError,
  providerName,
  sendsLine,
  testLine,
  testResultMessage,
  trustClassMeaning,
  trustClassName,
} from "./provider-copy";
import { ProviderDialogs, StepUpResult, type Applied, type ProviderDialog } from "./ProviderDialogs";
import styles from "./ProvidersScreen.module.css";
import { STATE } from "./setup-model";

/**
 * AI providers (phase-12-plan §11, C6; design-direction §6.9): the boundary between
 * Lattice and the models it may call, operated by the organization's administrators.
 *
 * Seven facts stay apart here as in the API: whether a key exists, whether its test
 * passed, what a provider is approved for, its trust class, the policy's ceiling for that
 * class, the questions' default level -- and, only when someone asks, each piece of
 * evidence's own authorization. The egress lattice is the server's reading of the policy
 * (`lattice`), never computed here; a change is previewed by the server (Now → After) and,
 * when it raises what may leave Lattice, confirmed by a fresh sign-in at the identity
 * provider before it commits. Keys are typed into empty masked fields and never come back.
 */
export function ProvidersScreen() {
  const { isAdmin, me } = useWorkspace();
  const query = useProviders(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const [dialog, setDialog] = useState<ProviderDialog | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const search = useSearchParams();
  const stepUpId = search.get("step_up");
  const router = useRouter();
  const pathname = usePathname();
  const dismissStepUp = () => router.replace(pathname, { scroll: false });

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="AI provider settings are visible to your organization's administrators.">
        <OpsHead title="AI providers" deck={`Which AI providers ${me.organization.name}'s data may reach, at which sensitivity — and what never leaves Lattice.`} />
        {stepUpId && /^[0-9a-f-]{36}$/.test(stepUpId) && (
          <StepUpResult
            id={stepUpId}
            onDismiss={dismissStepUp}
            onApplied={(a) => {
              dismissStepUp();
              setApplied(a);
            }}
          />
        )}
        {applied && <AppliedLine applied={applied} onDismiss={() => setApplied(null)} />}
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load provider settings" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={6} label="Loading provider settings" />
        ) : (
          <Loaded settings={query.data} onOpen={setDialog} dialog={dialog} onClose={() => setDialog(null)} onApplied={setApplied} />
        )}
      </AdminGate>
    </OpsPage>
  );
}

function Loaded({
  settings,
  dialog,
  onOpen,
  onClose,
  onApplied,
}: {
  settings: ProviderSettings;
  dialog: ProviderDialog | null;
  onOpen: (dialog: ProviderDialog) => void;
  onClose: () => void;
  onApplied: (applied: Applied) => void;
}) {
  const generation = settings.providers.filter((p) => p.purpose === "generation");
  const embedding = settings.providers.filter((p) => p.purpose !== "generation");
  return (
    <>
      <Status status={settings.status} />
      <BoundaryBand>
        <strong>Raising what may leave Lattice needs a fresh sign-in</strong> at your identity provider, after a preview of exactly what changes. Lowering applies at
        once. Every change is recorded in the <Link href="/access/audit">audit log</Link> with who made it and why.
      </BoundaryBand>
      <Policy settings={settings} onChange={() => onOpen({ kind: "policy" })} />
      {settings.providers.length === 0 ? (
        <StateBlock family="quiet" heading="No AI providers are configured">
          <p>Nothing leaves Lattice. Search still works, and without a generation provider Ask returns the sources it finds as evidence only.</p>
        </StateBlock>
      ) : (
        <Egress settings={settings} />
      )}
      <Generation settings={settings} providers={generation} onOpen={onOpen} />
      {embedding.length > 0 && <Embedding settings={settings} providers={embedding} onOpen={onOpen} />}
      <ProviderDialogs dialog={dialog} settings={settings} onClose={onClose} onApplied={onApplied} />
    </>
  );
}

/* --- shared ------------------------------------------------------------------------------------ */

function Status({ status }: { status: AiSetupStatus }) {
  const state = STATE[status.state];
  return (
    <div className={styles.status} data-state={status.state} data-reason={status.reason} data-testid="ai-status">
      <StatusMark tone={state.tone} icon={state.icon}>
        {state.word}
      </StatusMark>
      <p className={styles.statusMessage}>{status.message}</p>
    </div>
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

/* --- the policy -------------------------------------------------------------------------------- */

function Policy({ settings, onChange }: { settings: ProviderSettings; onChange: () => void }) {
  const egress = settings.egress;
  const stepUp = settings.step_up;
  return (
    <OpsSection
      id="policy-heading"
      title="AI data policy"
      aside={
        <Button size="sm" icon={<Icon as={SlidersHorizontal} />} onClick={onChange}>
          Change policy
        </Button>
      }
    >
      <dl className={styles.policy}>
        <div>
          <dt>External APIs</dt>
          <dd>{ceilingWords(egress.external_egress_level)}</dd>
        </div>
        <div>
          <dt>Organization-controlled servers</dt>
          <dd>{ceilingWords(egress.organization_controlled_level)}</dd>
        </div>
        <div>
          <dt>Inside the Lattice deployment</dt>
          <dd>Each provider&apos;s own approval</dd>
        </div>
        <div>
          <dt>Questions default to</dt>
          <dd>
            {levelLabel(egress.default_query_sensitivity)}
            <span className={styles.policyNote}>Askers can only raise this</span>
          </dd>
        </div>
        <div>
          <dt>Data origin</dt>
          <dd>
            {humanize(egress.data_origin)}
            <span className={styles.policyNote}>{egress.data_origin === "synthetic" ? "Generated test data" : "Recorded by the operator"}</span>
          </dd>
        </div>
      </dl>
      <p className={styles.note} data-testid="step-up-rule">
        {stepUp.available
          ? stepUp.assurance_required
            ? `Raising any of this needs you to sign in again at your identity provider, with the sign-in method your Lattice operator requires (${stepUp.assurance_required.join(", ")}).`
            : "Raising any of this needs you to sign in again at your identity provider. Lattice checks the sign-in is fresh; whether it asks for a second factor is up to your identity provider."
          : stepUp.reason === "no_identity"
            ? "You don't sign in through the organization's identity provider, so you can't confirm a raise here. Lowering still works."
            : "This organization doesn't sign in through an identity provider, so raising can't be confirmed in Lattice: ask your Lattice operator. Lowering still works."}
      </p>
    </OpsSection>
  );
}

/* --- the egress lattice (the server's reading) -------------------------------------------------- */

const LEVEL_ROWS: LatticeAxis[] = LEVELS.map((l) => ({ key: l, label: levelLabel(l), name: `${levelLabel(l)} data` }));

function Egress({ settings }: { settings: ProviderSettings }) {
  const providers = settings.providers;
  const egress = settings.egress;
  const groups = useMemo(() => byPurpose(providers), [providers]);
  const ordered = groups.flatMap((g) => g.providers);
  const cols: LatticeAxis[] = ordered.map((p) => ({
    key: p.id,
    label: p.provider,
    name: `${providerName(p)} (${humanize(p.purpose).toLowerCase()}, ${trustClassName(p.deployment).toLowerCase()}${p.purpose === "generation" && !p.enabled ? ", disabled" : ""})`,
    detail: trustClassName(p.deployment),
  }));
  const readings = useMemo(() => new Map(settings.lattice.map((c) => [cellKey(c.level, c.provider_id), c])), [settings.lattice]);
  const cells = useMemo(() => new Map<string, LatticeCell>([...readings].map(([k, c]) => [k, c.permitted ? "filled" : "hatched"])), [readings]);
  const sentence = (row: string, col: string) => {
    const c = readings.get(cellKey(row, col));
    const p = ordered.find((x) => x.id === col);
    if (!c || !p) return "";
    return `${levelLabel(row)} data ${c.permitted ? "may be sent to" : "isn't sent to"} ${providerName(p)}: ${cellClause(c.reason, p, egress)}.`;
  };
  const [hover, setHover] = useState<{ row: string; col: string } | null>(null);
  const [pinned, setPinned] = useState<{ row: string; col: string } | null>(null);
  const shown = hover ?? pinned;
  const kept = LEVELS.filter((level) => !settings.lattice.some((c) => c.level === level && c.permitted));

  return (
    <OpsSection id="egress-heading" title="What may leave Lattice">
      <p className={styles.note}>
        Each cell is one data level and one provider: filled where the AI data policy and the provider&apos;s approval allow that level to be sent, hatched where they
        don&apos;t. An API key or a passing test never changes a cell.
      </p>
      <div className={styles.egress} data-testid="egress-lattice">
        <Lattice
          caption="Which data levels each AI provider may receive"
          corner="Data level"
          headers="interface"
          rows={LEVEL_ROWS}
          cols={cols}
          cells={cells}
          colGroups={groups.map((g) => ({ key: g.purpose, label: humanize(g.purpose), span: g.providers.length }))}
          focusable="all"
          ground="sheet"
          cellLabel={(r, c) => sentence(r.key, c.key)}
          onActivate={(row, col) => setPinned((current) => (current?.row === row && current.col === col ? null : { row, col }))}
          onHighlight={setHover}
          highlight={shown ?? undefined}
          selected={pinned ?? undefined}
          legend={
            <LatticeLegend
              items={[
                { state: "filled", label: "may be sent" },
                { state: "hatched", label: "not sent — kept inside Lattice" },
              ]}
            />
          }
        />
        <p className={styles.why} data-testid="egress-why">
          {shown ? sentence(shown.row, shown.col) : "Point at or move to a cell to see why it holds."}
        </p>
      </div>
      <div className={styles.keptBlock} data-testid="never-sent">
        <p className={styles.kept}>
          {kept.length > 0
            ? `${levelsList(kept)} content is never sent to an AI provider: it stays in Lattice.`
            : "Every level may reach at least one provider, each only within its own approval."}
        </p>
        <p className={styles.note}>
          Content held back from a model stays in Lattice and is shown, as evidence only, to the people allowed to read it. This policy is about models, not people:
          each person still sees only what they may read — and that is checked again just before any model is called.
        </p>
      </div>
    </OpsSection>
  );
}

/* --- generation providers: one record each ------------------------------------------------------ */

function Generation({ settings, providers, onOpen }: { settings: ProviderSettings; providers: ProviderOut[]; onOpen: (dialog: ProviderDialog) => void }) {
  return (
    <OpsSection
      id="generation-heading"
      title="Generation providers"
      count={providers.length > 0 ? `${providers.length.toLocaleString("en-GB")} configured` : undefined}
      aside={
        <Button size="sm" variant="primary" icon={<Icon as={Plus} />} onClick={() => onOpen({ kind: "add" })}>
          Add provider
        </Button>
      }
    >
      {providers.length === 0 ? (
        <p className={styles.note}>
          None: Ask shows the evidence it finds without a written answer. A provider you add starts disabled and unapproved, so nothing is sent to it until you approve and
          enable it.
        </p>
      ) : (
        <div className={styles.records}>
          {providers.map((p) => (
            <ProviderRecord key={p.id} provider={p} settings={settings} onOpen={onOpen} />
          ))}
        </div>
      )}
      <p className={styles.note}>
        A credential is how Lattice calls a provider. It never raises a provider&apos;s trust class, its approval or the levels it may receive, and it&apos;s never shown
        again once saved.
      </p>
    </OpsSection>
  );
}

function ProviderRecord({ provider: p, settings, onOpen }: { provider: ProviderOut; settings: ProviderSettings; onOpen: (dialog: ProviderDialog) => void }) {
  const titleId = `provider-${p.id}`;
  const test = useTestProvider();
  const runTest = () => test.mutate(p.id);
  const sends = p.allowed_up_to !== null;
  const blocked = destinationBlocked(p.destination);
  return (
    <article className={styles.record} aria-labelledby={titleId} data-testid="provider-record" data-provider={p.provider}>
      <header className={styles.recordHead}>
        <h3 className={styles.recordTitle} id={titleId}>
          {p.provider} <span className={styles.model}>{p.model}</span>
        </h3>
        <StatusMark tone="neutral" icon={sends ? ShieldCheck : CircleSlash}>
          {sendsLine(p)}
        </StatusMark>
        <div className={styles.recordActions}>
          <Button size="sm" icon={<Icon as={FlaskConical} />} onClick={runTest} loading={test.isPending} disabled={p.provider === "fake"}>
            Test
          </Button>
          <Menu label={`Actions for ${providerName(p)}`} trigger={<Button size="sm" variant="quiet" icon={<Icon as={MoreHorizontal} />} aria-label={`Actions for ${providerName(p)}`} />}>
            <MenuLabel>Policy</MenuLabel>
            <MenuItem onSelect={() => onOpen({ kind: "approval", provider: p })}>{p.explicitly_approved ? "Change approval…" : "Approve…"}</MenuItem>
            <MenuItem onSelect={() => onOpen({ kind: "enabled", provider: p, enabled: !p.enabled })}>{p.enabled ? "Disable…" : "Enable…"}</MenuItem>
            {p.provider === "gemini" && <MenuItem onSelect={() => onOpen({ kind: "data_terms", provider: p })}>Change data terms…</MenuItem>}
            {p.provider === "ollama" && settings.targets.length > 0 && <MenuItem onSelect={() => onOpen({ kind: "endpoint", provider: p })}>Move to another server…</MenuItem>}
            {p.requires_credential && (
              <>
                <MenuSeparator />
                <MenuLabel>API key</MenuLabel>
                <MenuItem onSelect={() => onOpen({ kind: "credential", provider: p })}>{p.credential.state === "active" ? "Replace key…" : "Add key…"}</MenuItem>
                {p.credential.state === "active" && (
                  <MenuItem danger onSelect={() => onOpen({ kind: "revoke", provider: p })}>
                    Revoke key…
                  </MenuItem>
                )}
              </>
            )}
          </Menu>
        </div>
      </header>
      <dl className={styles.facts}>
        <div>
          <dt>Configuration</dt>
          <dd>
            <span className={styles.inline}>
              <Icon as={Power} size={14} /> {p.enabled ? "Enabled" : "Disabled"}
            </span>
            <span className={styles.secondary}>{dataTermsLabel(p.retention_mode, p.training_opt_out)}</span>
          </dd>
        </div>
        <div>
          <dt>Approval</dt>
          <dd>
            {p.explicitly_approved && LEVELS.includes(p.approved_for_level as (typeof LEVELS)[number]) ? (
              <>
                <LevelMeter level={p.approved_for_level as (typeof LEVELS)[number]} />
                <span className={styles.secondary}>Approved by an administrator; the policy may cap it lower</span>
              </>
            ) : (
              approvalLine(p)
            )}
          </dd>
        </div>
        <div>
          <dt>Trust boundary</dt>
          <dd>
            {trustClassName(p.deployment)}
            <span className={styles.secondary}>{trustClassMeaning(p.deployment)}</span>
            <span className={blocked ? styles.blocked : styles.secondary}>
              {blocked ? "Refused by Lattice's outbound policy: nothing can be sent" : destinationLine(p.destination)}
            </span>
          </dd>
        </div>
        <div>
          <dt>API key</dt>
          <dd>
            {p.requires_credential ? (
              <>
                <span className={styles.inline}>
                  <Icon as={KeyRound} size={14} /> {credentialHeadline(p.credential)}
                </span>
                {p.credential.state === "active" && <span className={styles.secondary}>{credentialLine(p.credential, true)}</span>}
              </>
            ) : (
              <span className={styles.secondary}>None needed</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Latest test</dt>
          <dd>
            {testLine(p.last_test)}
            <span className={styles.secondary}>Shows only that the key or endpoint works</span>
          </dd>
        </div>
      </dl>
      {(test.data || test.isError) && (
        <p className={styles.testResult} role="status" data-testid="test-result" data-result={test.data?.result}>
          {test.isError ? `Couldn't run the test. ${providerError(test.error)}` : testResultMessage(test.data!.result)}
        </p>
      )}
    </article>
  );
}

/* --- embedding: the deployment's --------------------------------------------------------------- */

function Embedding({ settings, providers, onOpen }: { settings: ProviderSettings; providers: ProviderOut[]; onOpen: (dialog: ProviderDialog) => void }) {
  const columns: Column<ProviderOut>[] = [
    {
      key: "provider",
      header: "Provider",
      primary: true,
      cell: (p) => (
        <span className={styles.stack}>
          <span>{p.provider}</span>
          <span className={styles.model}>{p.model}</span>
        </span>
      ),
    },
    {
      key: "trust",
      header: "Trust class",
      cell: (p) => (
        <span className={styles.stack}>
          <span>{trustClassName(p.deployment)}</span>
          <span className={styles.secondary}>{trustClassMeaning(p.deployment)}</span>
        </span>
      ),
    },
    {
      key: "approval",
      header: "Approved up to",
      width: "11rem",
      cell: (p) => (
        <span className={styles.stack}>
          {LEVELS.includes(p.approved_for_level as (typeof LEVELS)[number]) ? <LevelMeter level={p.approved_for_level as (typeof LEVELS)[number]} /> : humanize(p.approved_for_level)}
          <span className={styles.secondary}>{p.explicitly_approved ? "Approved by an administrator" : "Default approval"}</span>
        </span>
      ),
    },
    { key: "sends", header: "May receive", width: "11rem", cell: (p) => <span>{sendsLine(p)}</span> },
    {
      key: "actions",
      header: "Actions",
      short: true,
      width: "9rem",
      cell: (p) => (
        <Button size="sm" onClick={() => onOpen({ kind: "approval", provider: p })} aria-label={`Change approval of ${providerName(p)}`}>
          Change approval
        </Button>
      ),
    },
  ];
  return (
    <OpsSection id="embedding-heading" title="Embedding (the deployment's)" count={`${providers.length.toLocaleString("en-GB")} recorded`}>
      <p className={styles.note}>
        Your Lattice operator runs the embedding provider for every organization of this deployment; its model and keys are theirs. What it may receive for {settings.confirmation}{" "}
        is yours: change its approval here. Exact and keyword search work whatever this says.
      </p>
      <DataTable caption="Embedding providers recorded for this organization" columns={columns} rows={providers} rowKey={(p) => p.id} />
    </OpsSection>
  );
}

