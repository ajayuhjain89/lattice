"use client";

import { AlertTriangle, CheckCircle2, CircleSlash } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useProviders } from "@/api/queries";
import { isApiError } from "@/api/errors";
import type { EgressOut, ProviderOut } from "@/api/types";
import { BoundaryBand } from "@/components/primitives/Boundary";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { cellKey, Lattice, LatticeLegend, type LatticeAxis, type LatticeCell } from "@/components/primitives/Lattice";
import { LEVELS, LevelMeter, type Level } from "@/components/primitives/LevelMeter";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { AdminGate, OpsHead, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { humanize, levelLabel, retentionLabel } from "@/lib/format";

import { byPurpose, neverSent, providerKey, readCell, trustClassMeaning, trustClassName } from "./egress-model";
import styles from "./ProvidersScreen.module.css";

/**
 * AI providers (design-direction §6.9): the boundary between Lattice and the models it may
 * call. GET /v1/admin/providers, read-only -- there is no write API: every change is an
 * audited operator command, so the page has no control that would pretend otherwise.
 * Credentials are masked by the API (state and scheme, never a value or reference), and
 * none of them changes what a provider may receive.
 */
export function ProvidersScreen() {
  const { isAdmin, me } = useWorkspace();
  // The API decides; a non-admin still gets its 403 if they reach this page.
  const query = useProviders(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="AI provider settings are visible to your organization's administrators.">
        <OpsHead title="AI providers" deck={`Which AI providers ${me.organization.name}'s data may reach, at which sensitivity — and what never leaves Lattice.`} />
        <BoundaryBand>
          <strong>Read-only here.</strong> Providers, approvals, credentials and the AI data policy change only through the audited{" "}
          <code>lattice provider</code> and <code>lattice org egress</code> commands, run by an operator on the server. Each change is
          recorded in the <Link href="/access/audit">audit log</Link> with who made it and why.
        </BoundaryBand>
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load provider settings" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={4} label="Loading provider settings" />
        ) : (
          <>
            <Policy egress={query.data.egress} />
            {query.data.providers.length === 0 ? (
              <StateBlock family="quiet" heading="No AI providers are configured">
                <p>Nothing leaves Lattice. Search still works, and without a generation provider Ask returns the sources it finds as evidence only.</p>
              </StateBlock>
            ) : (
              <>
                <Egress egress={query.data.egress} providers={query.data.providers} />
                <Providers providers={query.data.providers} />
              </>
            )}
          </>
        )}
      </AdminGate>
    </OpsPage>
  );
}

/* --- the policy ------------------------------------------------------------------------- */

function Policy({ egress }: { egress: EgressOut }) {
  return (
    <OpsSection id="policy-heading" title="AI data policy">
      <dl className={styles.policy}>
        <div>
          <dt>External APIs</dt>
          <dd>{`Up to ${levelLabel(egress.external_egress_level)}`}</dd>
        </div>
        <div>
          <dt>Organization-controlled</dt>
          <dd>{egress.organization_controlled_level ? `Up to ${levelLabel(egress.organization_controlled_level)}` : "Not allowed"}</dd>
        </div>
        <div>
          <dt>Local</dt>
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
    </OpsSection>
  );
}

/* --- the egress lattice ---------------------------------------------------------------- */

const LEVEL_ROWS: LatticeAxis[] = LEVELS.map((l) => ({ key: l, label: levelLabel(l), name: `${levelLabel(l)} data` }));

function Egress({ egress, providers }: { egress: EgressOut; providers: ProviderOut[] }) {
  const groups = useMemo(() => byPurpose(providers), [providers]);
  const ordered = groups.flatMap((g) => g.providers);
  const cols: LatticeAxis[] = ordered.map((p) => ({
    key: providerKey(p),
    label: p.provider,
    name: `${p.provider} · ${p.model} (${humanize(p.purpose).toLowerCase()}, ${trustClassName(p.deployment).toLowerCase()}${p.purpose === "generation" && !p.enabled ? ", disabled" : ""})`,
    detail: trustClassName(p.deployment),
  }));
  const readings = useMemo(() => {
    const map = new Map<string, ReturnType<typeof readCell>>();
    for (const p of ordered) for (const level of LEVELS) map.set(cellKey(level, providerKey(p)), readCell(egress, p, level));
    return map;
  }, [egress, ordered]);
  // Every cell is a known relationship: permitted (filled) or a boundary (hatched). None is empty.
  const cells = useMemo(() => new Map<string, LatticeCell>([...readings].map(([k, r]) => [k, r.permitted ? "filled" : "hatched"])), [readings]);
  const sentence = (row: string, col: string) => {
    const r = readings.get(cellKey(row, col));
    const p = ordered.find((x) => providerKey(x) === col);
    if (!r || !p) return "";
    return `${levelLabel(row)} data ${r.permitted ? "may be sent to" : "isn't sent to"} ${p.provider} · ${p.model}: ${r.because}.`;
  };
  const [hover, setHover] = useState<{ row: string; col: string } | null>(null);
  const [pinned, setPinned] = useState<{ row: string; col: string } | null>(null);
  const shown = hover ?? pinned;
  const kept = neverSent(egress, providers);

  return (
    <OpsSection id="egress-heading" title="What may leave Lattice">
      <p className={styles.note}>
        Each cell is one data level and one provider: filled where the AI data policy and the provider&apos;s approval allow that level to be
        sent, hatched where they don&apos;t. A credential never changes a cell.
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
            ? `${listLevels(kept)} content is never sent to an AI provider: it stays in Lattice.`
            : "Every level may reach at least one provider, each only within its own approval."}
        </p>
        <p className={styles.note}>
          Content held back from a model stays in Lattice and is shown, as evidence only, to the people allowed to read it. This policy is
          about models, not people: each person still sees only what they may read.
        </p>
      </div>
    </OpsSection>
  );
}

const listLevels = (levels: readonly Level[]) =>
  levels.length <= 1 ? levels.map(levelLabel).join("") : `${levels.slice(0, -1).map(levelLabel).join(", ")} and ${levelLabel(levels.at(-1)!)}`;

/* --- the provider ledger ---------------------------------------------------------------- */

function Providers({ providers }: { providers: ProviderOut[] }) {
  const ordered = byPurpose(providers).flatMap((g) => g.providers);
  const columns: Column<ProviderOut>[] = [
    {
      key: "provider",
      header: "Provider",
      primary: true,
      cell: (p) => (
        <span className={styles.stack}>
          <span>{p.provider}</span>
          <span className={styles.model}>
            {p.model}
            {p.endpoint_configured ? " · custom endpoint" : ""}
          </span>
          {/* Purpose sits with the provider: a seventh column crushed the others at 1024 px. */}
          <span className={styles.secondary}>{humanize(p.purpose)}</span>
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
      width: "10rem",
      cell: (p) => (
        <span className={styles.stack}>
          {LEVELS.includes(p.approved_for_level as Level) ? <LevelMeter level={p.approved_for_level as Level} /> : humanize(p.approved_for_level)}
          <span className={styles.secondary}>{p.explicitly_approved ? "Explicitly approved" : "Default approval"}</span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      short: true,
      width: "7rem",
      cell: (p) =>
        p.enabled ? (
          <StatusMark tone="neutral" icon={CheckCircle2}>
            Enabled
          </StatusMark>
        ) : (
          <StatusMark tone="neutral" icon={CircleSlash}>
            Disabled
          </StatusMark>
        ),
    },
    { key: "credential", header: "Credential", width: "10rem", cell: (p) => <Credential p={p} /> },
    {
      key: "terms",
      header: "Data terms",
      width: "10rem",
      cell: (p) => (
        <span className={styles.stack}>
          <span>{retentionLabel(p.retention_mode)}</span>
          <span className={styles.secondary}>{p.training_opt_out ? "Training opt-out" : "May use submitted data"}</span>
        </span>
      ),
    },
  ];
  return (
    <OpsSection id="providers-heading" title="Providers" count={`${providers.length.toLocaleString("en-GB")} recorded`}>
      <DataTable caption="Recorded AI providers (read-only)" columns={columns} rows={ordered} rowKey={providerKey} />
      <p className={styles.note}>
        A credential is how Lattice calls a provider. It never raises a provider&apos;s trust class, its approval or the levels it may
        receive; its value is never shown.
      </p>
    </OpsSection>
  );
}

function Credential({ p }: { p: ProviderOut }) {
  const c = p.credential;
  if (c.state === "none") return <span className={styles.secondary}>None recorded</span>;
  return (
    <span className={styles.stack}>
      {c.resolvable ? (
        <span>Active</span>
      ) : (
        <StatusMark tone="neutral" icon={AlertTriangle}>
          Active · doesn&apos;t resolve
        </StatusMark>
      )}
      <span className={styles.secondary}>
        {[c.source ? `${c.source} reference` : null, c.data_use ? `${humanize(c.data_use)} data use` : null].filter(Boolean).join(" · ") || "Value never shown"}
      </span>
    </span>
  );
}
