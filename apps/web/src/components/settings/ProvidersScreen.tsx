"use client";

import { AlertTriangle, CheckCircle2, CircleSlash, Lock } from "lucide-react";

import { useProviders } from "@/api/queries";
import { isApiError } from "@/api/errors";
import type { EgressOut, ProviderOut } from "@/api/types";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { SkeletonLines } from "@/components/primitives/Skeleton";
import { PageHeader } from "@/components/shell/PageHeader";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { humanize, levelLabel, retentionLabel, trustClassLabel } from "@/lib/format";

import styles from "./ProvidersScreen.module.css";

/**
 * GET /v1/admin/providers, read-only. There is no provider-configuration write API: every
 * change is an audited administrative command, so this page offers no controls that would
 * pretend otherwise. Credentials are masked by the API (state and scheme, never a value or
 * reference).
 */
export function ProvidersScreen() {
  const { isAdmin, me } = useWorkspace();
  // The API decides; a non-admin still gets its 403 if they reach this page.
  const query = useProviders(true);
  const forbidden = !isAdmin || (query.isError && isApiError(query.error) && query.error.status === 403);

  return (
    <>
      <PageHeader trail={[{ label: "Admin" }, { label: "AI providers" }]} />
      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.column}>
          {forbidden ? (
            <NotAvailable
              label="Administrators only"
              title="You need administrator access to view this page"
              body="AI provider settings are visible to your organization's administrators."
              action={{ href: "/ask", label: "Back to Ask" }}
            />
          ) : (
            <>
              <header className={styles.head}>
                <h1 className={styles.title}>AI providers</h1>
                <p className={styles.deck}>
                  How {me.organization.name}&apos;s AI features are configured, and the most sensitive data each provider may
                  receive.
                </p>
                <div className={styles.readonly}>
                  <span className={styles.readonlyIcon}>
                    <Icon as={Lock} />
                  </span>
                  <p>
                    <span className={styles.readonlyTitle}>Read-only</span>
                    <span className={styles.readonlyBody}>
                      Providers, approvals, credentials and the AI data policy are changed by an administrator with the
                      audited <code>lattice provider</code> and <code>lattice org egress</code> commands. Each change
                      records who made it and why.
                    </span>
                  </p>
                </div>
              </header>
              {query.isError ? (
                <ErrorState error={query.error} title="Couldn't load provider settings" onRetry={() => query.refetch()} retrying={query.isFetching} />
              ) : !query.data ? (
                <div aria-busy="true">
                  <span className="visually-hidden" role="status">
                    Loading provider settings
                  </span>
                  <SkeletonLines widths={["100%", "100%", "100%", "60%"]} />
                </div>
              ) : (
                <>
                  <Policy egress={query.data.egress} />
                  <Providers providers={query.data.providers} />
                </>
              )}
            </>
          )}
        </div>
      </main>
    </>
  );
}

function Policy({ egress }: { egress: EgressOut }) {
  return (
    <section className={styles.section} aria-labelledby="policy-label">
      <SectionLabel id="policy-label">AI data policy</SectionLabel>
      <dl className={styles.policy}>
        <div className={styles.policyItem}>
          <dt>External providers</dt>
          <dd>
            <span className={styles.policyValue}>Up to {levelLabel(egress.external_egress_level)}</span>
            <span className={styles.policyNote}>Third-party APIs, when approved</span>
          </dd>
        </div>
        <div className={styles.policyItem}>
          <dt>Organization-controlled</dt>
          <dd>
            <span className={styles.policyValue}>
              {egress.organization_controlled_level ? `Up to ${levelLabel(egress.organization_controlled_level)}` : "Not allowed"}
            </span>
            <span className={styles.policyNote}>Your own model infrastructure</span>
          </dd>
        </div>
        <div className={styles.policyItem}>
          <dt>Questions default to</dt>
          <dd>
            <span className={styles.policyValue}>{levelLabel(egress.default_query_sensitivity)}</span>
            <span className={styles.policyNote}>Askers can only raise this</span>
          </dd>
        </div>
        <div className={styles.policyItem}>
          <dt>Data origin</dt>
          <dd>
            <span className={styles.policyValue}>{humanize(egress.data_origin)}</span>
            <span className={styles.policyNote}>{egress.data_origin === "synthetic" ? "Generated test data" : "Recorded by the operator"}</span>
          </dd>
        </div>
      </dl>
      <p className={styles.note}>
        A credential never widens what a provider may receive. The provider&apos;s trust class, its approval and this policy
        decide; content above the allowed level stays out of the prompt and is shown to readers as evidence only.
      </p>
    </section>
  );
}

function Status({ p }: { p: ProviderOut }) {
  return p.enabled ? (
    <span className={`${styles.state} ${styles.ok}`}>
      <Icon as={CheckCircle2} size={14} />
      Enabled
    </span>
  ) : (
    <span className={`${styles.state} ${styles.off}`}>
      <Icon as={CircleSlash} size={14} />
      Disabled
    </span>
  );
}

function Credential({ p }: { p: ProviderOut }) {
  const c = p.credential;
  if (c.state === "none") return <span className={styles.off}>None</span>;
  return (
    <>
      <span className={`${styles.state} ${c.resolvable ? styles.ok : styles.warn}`}>
        <Icon as={c.resolvable ? CheckCircle2 : AlertTriangle} size={14} />
        {c.resolvable ? "Active" : "Active · doesn't resolve"}
      </span>
      <span className={styles.secondary}>
        {[c.source ? `${c.source} reference` : null, c.data_use ? `${humanize(c.data_use)} data use` : null].filter(Boolean).join(" · ") ||
          "Value never shown"}
      </span>
    </>
  );
}

function Approval({ p }: { p: ProviderOut }) {
  return (
    <>
      <span className={styles.primary}>Up to {levelLabel(p.approved_for_level)}</span>
      <span className={styles.secondary}>{p.explicitly_approved ? "Explicitly approved" : "Default approval"}</span>
    </>
  );
}

function Terms({ p }: { p: ProviderOut }) {
  return (
    <>
      <span className={styles.primary}>{retentionLabel(p.retention_mode)}</span>
      <span className={styles.secondary}>{p.training_opt_out ? "Training opt-out" : "No training opt-out"}</span>
    </>
  );
}

function Providers({ providers }: { providers: ProviderOut[] }) {
  return (
    <section className={styles.section} aria-labelledby="providers-label">
      <SectionLabel id="providers-label" meta={`${providers.length} configured`}>
        Providers
      </SectionLabel>
      {providers.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>No AI providers are configured</p>
          <p>Search still works. Without a generation provider, Ask returns the sources it finds as evidence only.</p>
        </div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">Configured AI providers (read-only)</caption>
              <thead>
                <tr>
                  <th scope="col">Purpose</th>
                  <th scope="col">Provider · model</th>
                  <th scope="col">Trust class</th>
                  <th scope="col">Approved for</th>
                  <th scope="col">Status</th>
                  <th scope="col">Credential</th>
                  <th scope="col">Data terms</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => (
                  <tr key={`${p.purpose}-${p.provider}-${p.model}`}>
                    <td>
                      <span className={styles.primary}>{humanize(p.purpose)}</span>
                    </td>
                    <td>
                      <span className={styles.primary}>{p.provider}</span>
                      <span className={styles.secondary}>
                        {p.model}
                        {p.endpoint_configured ? " · custom endpoint" : ""}
                      </span>
                    </td>
                    <td>{trustClassLabel(p.deployment)}</td>
                    <td>
                      <Approval p={p} />
                    </td>
                    <td>
                      <Status p={p} />
                    </td>
                    <td>
                      <Credential p={p} />
                    </td>
                    <td>
                      <Terms p={p} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className={styles.cards} aria-label="Configured AI providers (read-only)">
            {providers.map((p) => (
              <li key={`${p.purpose}-${p.provider}-${p.model}`} className={styles.card}>
                <span>
                  <span className={styles.primary}>
                    {p.provider} · {humanize(p.purpose)}
                  </span>
                  <span className={styles.secondary}>{p.model}</span>
                </span>
                <dl>
                  <dt>Trust class</dt>
                  <dd>{trustClassLabel(p.deployment)}</dd>
                  <dt>Approved for</dt>
                  <dd>
                    <Approval p={p} />
                  </dd>
                  <dt>Status</dt>
                  <dd>
                    <Status p={p} />
                  </dd>
                  <dt>Credential</dt>
                  <dd>
                    <Credential p={p} />
                  </dd>
                  <dt>Data terms</dt>
                  <dd>
                    <Terms p={p} />
                  </dd>
                </dl>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
