"use client";

import { ChevronRight, EyeOff, Lock, Search } from "lucide-react";
import { useCallback, useId, useMemo, useState } from "react";

import { useTrace } from "@/api/queries";
import { isApiError } from "@/api/errors";
import type { AdminTrace, AskerTrace } from "@/api/types";
import { AnswerContextProvider } from "@/components/ask/answer-context";
import { Conflicts, Prose, Unknowns } from "@/components/ask/Brief";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { SkeletonLines } from "@/components/primitives/Skeleton";
import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { PageTrail } from "@/components/shell/PageTrail";
import { shortStatus } from "@/components/shell/status-labels";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { buildAnswerModel } from "@/lib/answer-model";
import { formatCount, formatDay, formatDayTime, formatDuration, humanize, isUuid, levelLabel, shortId, trustClassLabel } from "@/lib/format";
import {
  DECISION_REASON,
  REJECTION_CODE,
  isAskerTrace,
  storedAnswer,
  traceScope,
  traceTimings,
  traceUsage,
  traceValidation,
} from "@/lib/trace-model";

import styles from "./TraceScreen.module.css";

/**
 * GET /v1/traces/{id}, fetched fresh on every visit. The API decides the view: the
 * asker's own trace (re-authorized now: the answer is hidden when any of its evidence or
 * accounts are no longer visible), an administrator's operational view of someone else's
 * (no content), or 404. Nothing from the Ask page's memory is used to fill anything in.
 */
export function TraceScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  return (
    <>
      <div className={styles.main}>
        <div className={styles.column}>
          <PageTrail trail={[{ label: "Activity", href: "/activity" }, { label: "Trace" }]} />
          {valid ? <TraceLoader id={id} /> : <TraceNotAvailable />}
        </div>
      </div>
    </>
  );
}

function TraceNotAvailable() {
  return (
    <NotAvailable
      label="Not available"
      title="This trace isn't available"
      body="It doesn't exist, or it isn't one you can see."
      action={{ href: "/ask", label: "Back to Ask" }}
    />
  );
}

function TraceLoader({ id }: { id: string }) {
  const query = useTrace(id);
  if (query.isError) {
    if (isApiError(query.error) && (query.error.isNotFound || query.error.status === 422)) return <TraceNotAvailable />;
    return <ErrorState error={query.error} title="Couldn't load this trace" headingLevel="h1" onRetry={() => query.refetch()} retrying={query.isFetching} />;
  }
  if (!query.data) {
    return (
      <div className={styles.head} aria-busy="true">
        <span className="visually-hidden" role="status">
          Loading trace
        </span>
        <p className={styles.eyebrow}>Trace</p>
        <SkeletonLines widths={["40%", "72%"]} />
        <SkeletonLines widths={["100%", "100%", "100%", "80%"]} />
      </div>
    );
  }
  return isAskerTrace(query.data) ? <AskerView trace={query.data} /> : <OperationalView trace={query.data} />;
}

function Header({ trace, eyebrow }: { trace: AskerTrace | AdminTrace; eyebrow: string }) {
  return (
    <header className={styles.head}>
      <p className={styles.eyebrow}>{eyebrow}</p>
      <h1 className={styles.title}>{shortStatus(trace.status)}</h1>
      <p className={styles.id}>Trace {trace.trace_id}</p>
    </header>
  );
}

function Facts({ trace, member }: { trace: AskerTrace | AdminTrace; member?: boolean }) {
  const t = traceTimings(trace);
  const usage = traceUsage(trace);
  const tokens = usage.input !== null || usage.output !== null ? `${usage.input ?? 0} in · ${usage.output ?? 0} out` : null;
  const rows: [string, string | null, string | null][] = [
    ["Status", humanize(trace.status), trace.status_reason ? humanize(trace.status_reason) : null],
    ["Created", formatDayTime(trace.created_at), null],
    ["Total", formatDuration(t.total), null],
    ["Provider", trace.provider ? `${trace.provider} · ${trace.model ?? "—"}` : "No model call", trace.deployment ? trustClassLabel(trace.deployment) : null],
    ["Tokens", tokens, trace.generation_calls ? formatCount(trace.generation_calls, "call", "calls") : null],
    ["Error", trace.error_code, null],
    ["Prompt", trace.prompt_version, null],
    ["Policy", trace.policy_version, null],
    ["Retrieval", trace.retrieval_config_version, null],
  ];
  if (member) rows.unshift(["Member", trace.membership_id, null]);
  return (
    <dl className={styles.facts}>
      {rows
        .filter(([, v]) => v)
        .map(([k, v, d]) => (
          <div key={k} className={styles.fact}>
            <dt>{k}</dt>
            <dd>
              {v}
              {d && <span className={styles.factDetail}>{d}</span>}
            </dd>
          </div>
        ))}
    </dl>
  );
}

interface Stage {
  name: string;
  summary: string | null;
  ms: number | null;
  state: "done" | "skipped" | "failed";
  details?: [string, number][];
}

function Stages({ stages, total }: { stages: Stage[]; total: number | null }) {
  return (
    <section className={styles.section} aria-labelledby="stages-label">
      <SectionLabel id="stages-label" meta={total !== null ? `total ${formatDuration(total)}` : undefined}>
        Pipeline
      </SectionLabel>
      <ol className={styles.stages}>
        {stages.map((s, i) => (
          <li key={s.name} className={`${styles.stage} ${s.state === "skipped" ? styles.stageSkipped : ""}`}>
            <ExhibitMarker n={i + 1} size="lg" state={s.state === "done" ? "rest" : s.state} />
            <div className={styles.stageBody}>
              <span className={styles.stageName}>
                {s.name}
                {s.state === "skipped" && <span className="visually-hidden"> (skipped)</span>}
                {s.state === "failed" && <span className="visually-hidden"> (failed)</span>}
              </span>
              {s.summary && <span className={styles.stageSummary}>{s.summary}</span>}
              {s.details && s.details.length > 0 && (
                <ul className={styles.codes} aria-label="Rejection reasons">
                  {s.details.map(([code, n]) => (
                    <li key={code} className={styles.code}>
                      <span>
                        <span className={styles.codeName}>{code}</span> — {REJECTION_CODE[code] ?? humanize(code)}
                      </span>
                      <span className={styles.codeCount}>×{n}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <span className={styles.stageDuration}>{s.state === "skipped" ? "—" : (formatDuration(s.ms) ?? "")}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function AskerView({ trace }: { trace: AskerTrace }) {
  const t = traceTimings(trace);
  const v = traceValidation(trace);
  const scope = traceScope(trace);
  const called = trace.generation_calls > 0;
  const failed = trace.status === "failed";

  const decision = trace.decision_reason
    ? (DECISION_REASON[trace.decision_reason] ?? humanize(trace.decision_reason))
    : trace.provider
      ? `Allowed: ${trace.provider}${trace.model ? ` · ${trace.model}` : ""}${trace.deployment ? ` (${trustClassLabel(trace.deployment)})` : ""}.`
      : "No model call was needed.";

  const validationSummary = called
    ? [
        v.claimsProposed !== null ? `${v.claimsValid ?? 0} of ${v.claimsProposed} claims verified` : null,
        v.regenerated ? "regenerated once after rejections" : null,
        v.schemaFailures ? `${v.schemaFailures} schema ${v.schemaFailures === 1 ? "failure" : "failures"}` : null,
        v.conflictsProposed ? `${v.conflictsProposed - (v.conflictsDiscarded ?? 0)} of ${v.conflictsProposed} conflicts kept` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  const stages: Stage[] = [
    {
      name: "Retrieval",
      summary: `${formatCount(trace.evidence_ids.length, "evidence passage", "evidence passages")} recorded and still readable by you.`,
      ms: t.retrieval,
      state: "done",
    },
    { name: "AI data policy and provider", summary: decision, ms: t.policy, state: "done" },
    {
      name: "Generation",
      summary: called ? `${formatCount(trace.generation_calls, "model call", "model calls")}.` : "Skipped — no model call.",
      ms: t.generation,
      state: called ? (failed && trace.error_code === "provider_unavailable" ? "failed" : "done") : "skipped",
    },
    {
      name: "Claim validation",
      summary: called ? validationSummary || "No claims proposed." : "Skipped.",
      ms: null,
      state: called ? (failed && trace.error_code ? "failed" : "done") : "skipped",
      details: v.rejectionCodes,
    },
    {
      name: "Answer",
      summary: `${shortStatus(trace.status)}${trace.status_reason ? ` · ${humanize(trace.status_reason)}` : ""}${trace.error_code ? ` · error ${trace.error_code}` : ""}.`,
      ms: null,
      state: failed ? "failed" : "done",
    },
  ];

  const scopeParts = [
    `Question ${levelLabel(trace.question_sensitivity)}`,
    scope.account ? `Account ${scope.account}` : null,
    scope.sourceTypes.length ? `Sources ${scope.sourceTypes.join(", ")}` : null,
    scope.since ? `From ${formatDay(`${scope.since}T00:00:00Z`)}` : null,
    scope.until ? `Until ${formatDay(`${scope.until}T00:00:00Z`)}` : null,
  ].filter(Boolean);

  return (
    <>
      <Header trace={trace} eyebrow="Your question · trace" />
      <section className={styles.section} aria-labelledby="question-label">
        <SectionLabel id="question-label">Question</SectionLabel>
        <p className={styles.question}>{trace.question}</p>
        <p className={styles.scope}>
          {scopeParts.map((p) => (
            <span key={p}>{p}</span>
          ))}
        </p>
      </section>
      <Facts trace={trace} />
      <Stages stages={stages} total={t.total} />
      <StoredAnswerSection trace={trace} />
      <EvidenceRefs trace={trace} />
      <RawRecord trace={trace} />
    </>
  );
}

function StoredAnswerSection({ trace }: { trace: AskerTrace }) {
  const { state } = useWorkspace();
  const stored = useMemo(() => storedAnswer(trace), [trace]);
  const available = useMemo(() => new Set(trace.evidence_ids), [trace.evidence_ids]);
  const model = useMemo(() => buildAnswerModel(stored ?? { claims: [], conflicts: [] }, available), [stored, available]);
  const items = useMemo(() => new Map(), []);
  const open = useCallback(
    (evidenceId: string, from: HTMLElement | null) =>
      state.inspector.set({ evidenceId, returnFocus: from, context: { numberOf: model.numberOf, citedBy: model.citedBy, order: model.cited, items } }),
    [state.inspector, model, items],
  );
  const context = useMemo(() => ({ model, items, open }), [model, items, open]);

  return (
    <section className={styles.section} aria-labelledby="stored-answer-label">
      <SectionLabel id="stored-answer-label">Answer as recorded</SectionLabel>
      {trace.answer_hidden ? (
        <Callout tone="neutral" label="Answer hidden" icon={<Icon as={EyeOff} />}>
          <p>
            This answer used sources or accounts you no longer have access to, so it isn&apos;t shown. The trace&apos;s
            structure is still available.
          </p>
        </Callout>
      ) : !stored ? (
        <p className={styles.message}>No answer text was recorded for this question.</p>
      ) : (
        <AnswerContextProvider value={context}>
          <div className={styles.answer}>
            {stored.message && <p className={styles.message}>{stored.message}</p>}
            <Prose />
            <Conflicts />
            <Unknowns unknowns={stored.unknowns} />
          </div>
        </AnswerContextProvider>
      )}
    </section>
  );
}

function EvidenceRefs({ trace }: { trace: AskerTrace }) {
  const { state } = useWorkspace();
  const stored = useMemo(() => storedAnswer(trace), [trace]);
  const model = useMemo(() => buildAnswerModel(stored ?? { claims: [], conflicts: [] }, new Set(trace.evidence_ids)), [stored, trace.evidence_ids]);
  const ordered = useMemo(
    () => [...trace.evidence_ids].sort((a, b) => (model.numberOf.get(a) ?? Infinity) - (model.numberOf.get(b) ?? Infinity)),
    [trace.evidence_ids, model],
  );
  if (!trace.evidence_ids.length) return null;
  const items = new Map();
  return (
    <section className={styles.section} aria-labelledby="refs-label">
      <SectionLabel id="refs-label" meta={`${trace.evidence_ids.length} readable now`}>
        Evidence references
      </SectionLabel>
      <ol className={styles.refs}>
        {ordered.map((id) => {
          const n = model.numberOf.get(id);
          return (
            <li key={id} className={styles.ref}>
              <span className={`${styles.refNumber} ${n ? "" : styles.refNumberNone}`} aria-label={n ? `Citation ${n}` : "Not cited"}>
                {n ?? "–"}
              </span>
              <span className={styles.refId} title={id}>
                {id}
              </span>
              <Button
                size="sm"
                variant="quiet"
                icon={<Icon as={Search} />}
                onClick={(e) =>
                  state.inspector.set({
                    evidenceId: id,
                    returnFocus: e.currentTarget,
                    context: { numberOf: model.numberOf, citedBy: model.citedBy, order: ordered, items },
                  })
                }
                aria-label={`Inspect evidence ${n ?? shortId(id)}`}
              >
                Inspect
              </Button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RawRecord({ trace }: { trace: AskerTrace | AdminTrace }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <section className={styles.raw}>
      <button type="button" className={styles.rawToggle} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={14} strokeWidth={1.5} aria-hidden="true" />
        Raw trace record
      </button>
      <div id={id} hidden={!open}>
        {open && (
          // A scrollable region must be reachable by keyboard (WCAG 2.1.1).
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          <pre className={styles.codeBlock} tabIndex={0} aria-label="Raw trace record (JSON, as returned by the API)">
            {JSON.stringify(trace, null, 2)}
          </pre>
        )}
      </div>
    </section>
  );
}

function OperationalView({ trace }: { trace: AdminTrace }) {
  const t = traceTimings(trace);
  const called = trace.generation_calls > 0;
  const stages: Stage[] = [
    { name: "Retrieval", summary: null, ms: t.retrieval, state: "done" },
    { name: "AI data policy and provider", summary: null, ms: t.policy, state: "done" },
    { name: "Generation", summary: called ? formatCount(trace.generation_calls, "model call", "model calls") : null, ms: t.generation, state: called ? "done" : "skipped" },
    { name: "Answer", summary: `${shortStatus(trace.status)}${trace.error_code ? ` · error ${trace.error_code}` : ""}`, ms: null, state: trace.status === "failed" ? "failed" : "done" },
  ];
  return (
    <>
      <Header trace={trace} eyebrow="Operational view" />
      <div className={styles.operational}>
        <NoticeLine icon={<Icon as={Lock} />} tone="neutral">
          Content of other members&apos; questions isn&apos;t visible to administrators: no question, answer, evidence, counts or scope.
        </NoticeLine>
      </div>
      <Facts trace={trace} member />
      <Stages stages={stages} total={t.total} />
      <RawRecord trace={trace} />
    </>
  );
}
