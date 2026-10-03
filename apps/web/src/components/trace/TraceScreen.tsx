"use client";

import { ChevronRight, EyeOff } from "lucide-react";
import { useCallback, useId, useMemo, useState, type ReactNode } from "react";

import { useEvidenceList, useTrace } from "@/api/queries";
import { isApiError } from "@/api/errors";
import type { AdminTrace, AskerTrace, EvidenceDetail } from "@/api/types";
import { AnswerContextProvider, NO_POINTER, useAnswer, type Pointer } from "@/components/ask/answer-context";
import { Disagreements, Statements, Unknowns } from "@/components/ask/Brief";
import { BoundaryBand } from "@/components/primitives/Boundary";
import { Callout } from "@/components/primitives/Callout";
import { ExhibitMarker, type ExhibitState } from "@/components/primitives/ExhibitMarker";
import { DocumentGlyph, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { RecordPage, record } from "@/components/record/RecordPage";
import { PageTrail } from "@/components/shell/PageTrail";
import { shortStatus } from "@/components/shell/status-labels";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { buildAnswerModel, type AnswerModel } from "@/lib/answer-model";
import { formatCount, formatDay, formatDayTime, formatDuration, humanize, isUuid, levelLabel, sourceTypeLabel, trustClassLabel } from "@/lib/format";
import { createStore, useStore } from "@/lib/store";
import { DECISION_REASON, REJECTION_CODE, isAskerTrace, storedAnswer, traceScope, traceTimings, traceUsage, traceValidation } from "@/lib/trace-model";

import styles from "./TraceScreen.module.css";

/**
 * GET /v1/traces/{id}, fetched fresh on every visit, in the same provenance grammar as
 * the brief: a ledger line, the pipeline as a trail of numbered stops, the answer as
 * recorded, and its exhibits. The API decides the view: the asker's own trace
 * (re-authorized now: the answer is hidden when any of its evidence or accounts are no
 * longer visible), an administrator's operational view of someone else's (no content),
 * or 404. Nothing from the Ask page's memory is used to fill anything in.
 */
export function TraceScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  return (
    <RecordPage>
      <div className={record.text}>
        <PageTrail trail={[{ label: "Activity", href: "/activity" }, { label: "Trace" }]} />
      </div>
      {valid ? <TraceLoader id={id} /> : <TraceNotAvailable />}
    </RecordPage>
  );
}

function TraceNotAvailable() {
  return (
    <div className={record.text}>
      <NotAvailable label="Not available" title="This trace isn't available" body="It doesn't exist, or it isn't one you can see." action={{ href: "/ask", label: "Back to Ask" }} />
    </div>
  );
}

function TraceLoader({ id }: { id: string }) {
  const query = useTrace(id);
  if (query.isError) {
    if (isApiError(query.error) && (query.error.isNotFound || query.error.status === 422)) return <TraceNotAvailable />;
    return (
      <div className={record.text}>
        <ErrorState error={query.error} title="Couldn't load this trace" headingLevel="h1" onRetry={() => query.refetch()} retrying={query.isFetching} />
      </div>
    );
  }
  if (!query.data) {
    return (
      <div className={`${record.text} ${styles.loading}`} aria-busy="true">
        <span className="visually-hidden" role="status">
          Loading trace
        </span>
        <span className={styles.ruleSkeleton} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </div>
    );
  }
  return <div className={styles.trace}>{isAskerTrace(query.data) ? <AskerView trace={query.data} /> : <OperationalView trace={query.data} />}</div>;
}

/** Title and ledger line: STATUS · created · total · question sensitivity. */
function Header({ trace, eyebrow, title }: { trace: AskerTrace | AdminTrace; eyebrow: string; title: ReactNode }) {
  const t = traceTimings(trace);
  const facts = [
    formatDayTime(trace.created_at),
    formatDuration(t.total),
    "question_sensitivity" in trace && trace.question_sensitivity !== "internal" ? `Question ${levelLabel(trace.question_sensitivity)}` : null,
  ].filter((f): f is string => Boolean(f));
  return (
    <header className={`${record.text} ${styles.head}`}>
      <p className={styles.eyebrow}>{eyebrow}</p>
      <h1 className={styles.title}>{title}</h1>
      <div className={styles.statusLine}>
        <p className={styles.facts}>
          <span className={styles.keyword} data-failed={trace.status === "failed" || undefined}>
            {shortStatus(trace.status)}
          </span>
          {facts.map((f) => (
            <span key={f} className={styles.fact}>
              {f}
            </span>
          ))}
        </p>
        <p className={styles.id}>
          <span className="visually-hidden">Trace </span>
          {trace.trace_id}
        </p>
      </div>
    </header>
  );
}

interface Stop {
  name: string;
  summary: string | null;
  ms: number | null;
  state: "done" | "skipped" | "failed";
}

/** The pipeline as a trail of numbered stops (vertical when narrow). Durations are the trace's own. */
function Pipeline({ stops, total, rejections }: { stops: Stop[]; total: number | null; rejections?: [string, number][] }) {
  return (
    <section className={`${record.text} ${styles.section}`} aria-labelledby="pipeline-label">
      <div className={styles.sectionHeading}>
        <h2 id="pipeline-label">Pipeline</h2>
        {total !== null && <span className={styles.sectionMeta}>total {formatDuration(total)}</span>}
      </div>
      <ol className={styles.pipeline} style={{ "--stops": stops.length } as React.CSSProperties}>
        {stops.map((s, i) => {
          const state: ExhibitState = s.state === "done" ? "rest" : s.state;
          return (
            <li key={s.name} className={styles.stop} data-state={s.state}>
              <span className={styles.stopMark}>
                <ExhibitMarker n={i + 1} size="lg" state={state} />
              </span>
              <span className={styles.stopName}>
                {s.name}
                {s.state === "skipped" && <span className="visually-hidden"> (skipped)</span>}
                {s.state === "failed" && <span className="visually-hidden"> (failed)</span>}
              </span>
              <span className={styles.stopDuration}>{s.state === "skipped" ? "skipped" : (formatDuration(s.ms) ?? "")}</span>
              {s.summary && <span className={styles.stopSummary}>{s.summary}</span>}
            </li>
          );
        })}
      </ol>
      {rejections && rejections.length > 0 && (
        <div className={styles.rejections}>
          <h3 className={styles.subHeading}>Claims rejected by validation</h3>
          <ul className={styles.codes} aria-label="Rejection reasons">
            {rejections.map(([code, n]) => (
              <li key={code} className={styles.code}>
                <span className={styles.codeName}>{code}</span>
                <span className={styles.codeText}>{REJECTION_CODE[code] ?? humanize(code)}</span>
                <span className={styles.codeCount}>×{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function RunDetails({ trace, member }: { trace: AskerTrace | AdminTrace; member?: boolean }) {
  const usage = traceUsage(trace);
  const tokens = usage.input !== null || usage.output !== null ? `${usage.input ?? 0} in · ${usage.output ?? 0} out` : null;
  const rows: [string, string | null, string | null][] = [
    ["Status", humanize(trace.status), trace.status_reason ? humanize(trace.status_reason) : null],
    ["Provider", trace.provider ? `${trace.provider} · ${trace.model ?? "—"}` : "No model call", trace.deployment ? trustClassLabel(trace.deployment) : null],
    ["Tokens", tokens, trace.generation_calls ? formatCount(trace.generation_calls, "call", "calls") : null],
    ["Error", trace.error_code, null],
    ["Prompt", trace.prompt_version, null],
    ["Policy", trace.policy_version, null],
    ["Retrieval", trace.retrieval_config_version, null],
  ];
  if (member) rows.unshift(["Member", trace.membership_id, null]);
  return (
    <section className={`${record.text} ${styles.section}`} aria-labelledby="run-label">
      <h2 className={styles.sectionHeading} id="run-label">
        Run details
      </h2>
      <dl className={styles.details}>
        {rows
          .filter(([, v]) => v)
          .map(([k, v, d]) => (
            <div key={k} className={styles.detail}>
              <dt>{k}</dt>
              <dd>
                {v}
                {d && <span className={styles.detailNote}>{d}</span>}
              </dd>
            </div>
          ))}
      </dl>
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

  const stops: Stop[] = [
    { name: "Retrieval", summary: `${formatCount(trace.evidence_ids.length, "passage", "passages")} recorded and still readable by you.`, ms: t.retrieval, state: "done" },
    { name: "AI data policy", summary: decision, ms: t.policy, state: "done" },
    {
      name: "Generation",
      summary: called ? `${formatCount(trace.generation_calls, "model call", "model calls")}.` : "No model call.",
      ms: t.generation,
      state: called ? (failed && trace.error_code === "provider_unavailable" ? "failed" : "done") : "skipped",
    },
    {
      name: "Claim validation",
      summary: called ? `${validationSummary || "No claims proposed"}.` : "Nothing to validate.",
      ms: null,
      state: called ? (failed && trace.error_code ? "failed" : "done") : "skipped",
    },
    {
      name: "Answer",
      summary: `${shortStatus(trace.status)}${trace.status_reason ? ` · ${humanize(trace.status_reason)}` : ""}${trace.error_code ? ` · error ${trace.error_code}` : ""}.`,
      ms: null,
      state: failed ? "failed" : "done",
    },
  ];

  const LONG: Record<string, string> = { doc: "documents", ticket: "tickets", chat: "chat messages" };
  const scopeParts = [
    scope.sourceTypes.length ? scope.sourceTypes.map((s) => LONG[s] ?? s).join(", ") : "every source type you could read",
    scope.account ? `account ${scope.account}` : "any account",
    scope.since && scope.until
      ? `${formatDay(`${scope.since}T00:00:00Z`)} – ${formatDay(`${scope.until}T00:00:00Z`)}`
      : scope.since
        ? `from ${formatDay(`${scope.since}T00:00:00Z`)}`
        : scope.until
          ? `until ${formatDay(`${scope.until}T00:00:00Z`)}`
          : "any date",
    `question treated as ${levelLabel(trace.question_sensitivity)}`,
  ];

  return (
    <>
      <Header trace={trace} eyebrow="Trace · your question" title={trace.question} />
      <p className={`${record.text} ${styles.scope}`} data-testid="trace-scope">
        <span className={styles.scopeLead}>Searched</span>
        {scopeParts.map((p) => (
          <span key={p} className={styles.scopePart}>
            {p}
          </span>
        ))}
      </p>
      <Pipeline stops={stops} total={t.total} rejections={v.rejectionCodes} />
      <RecordedAnswer trace={trace} />
      <RunDetails trace={trace} />
      <RawRecord trace={trace} />
    </>
  );
}

/**
 * The answer as recorded, and the exhibits still readable now. The stored answer carries
 * evidence IDs only; each exhibit's title and source come from its own re-authorized
 * read, and one that is no longer readable shows the same not-available row whatever the
 * reason.
 */
function RecordedAnswer({ trace }: { trace: AskerTrace }) {
  const { state } = useWorkspace();
  const stored = useMemo(() => storedAnswer(trace), [trace]);
  const available = useMemo(() => new Set(trace.evidence_ids), [trace.evidence_ids]);
  const model = useMemo(() => buildAnswerModel(stored ?? { claims: [], conflicts: [] }, available), [stored, available]);
  const order = useMemo(
    () => [...trace.evidence_ids].sort((a, b) => (model.numberOf.get(a) ?? Infinity) - (model.numberOf.get(b) ?? Infinity)),
    [trace.evidence_ids, model],
  );
  const items = useMemo(() => new Map(), []);
  const pointer = useMemo(() => createStore<Pointer>(NO_POINTER), []);
  const open = useCallback(
    (evidenceId: string, from: HTMLElement | null, claim: string | null = null) =>
      state.inspector.set({ evidenceId, claim, returnFocus: from, context: { numberOf: model.numberOf, citedBy: model.citedBy, order, items } }),
    [state.inspector, model, order, items],
  );
  const context = useMemo(() => ({ model, items, open, pointer }), [model, items, open, pointer]);

  return (
    <AnswerContextProvider value={context}>
      <section className={styles.section} aria-labelledby="stored-answer-label">
        <h2 className={`${record.text} ${styles.sectionHeading}`} id="stored-answer-label">
          Answer as recorded
        </h2>
        {trace.answer_hidden ? (
          <div className={record.text}>
            <Callout tone="neutral" label="Answer hidden" icon={<Icon as={EyeOff} />}>
              <p>This answer used sources or accounts you no longer have access to, so it isn&apos;t shown. The trace&apos;s structure is still available.</p>
            </Callout>
          </div>
        ) : !stored ? (
          <p className={`${record.text} ${styles.quiet}`}>No answer text was recorded for this question.</p>
        ) : (
          <div className={styles.answer}>
            {stored.message && <p className={`${record.text} ${styles.quiet}`}>{stored.message}</p>}
            <Statements />
            <Disagreements />
            <Unknowns unknowns={stored.unknowns} />
          </div>
        )}
      </section>
      <TraceExhibits ids={order} model={model} />
    </AnswerContextProvider>
  );
}

function TraceExhibits({ ids, model }: { ids: string[]; model: AnswerModel }) {
  const results = useEvidenceList(ids);
  if (!ids.length) return null;
  const cited = ids.filter((id) => model.numberOf.has(id)).length;
  return (
    <section className={styles.section} aria-labelledby="refs-label" data-testid="trace-exhibits">
      <div className={`${record.text} ${styles.sectionHeading}`}>
        <h2 id="refs-label">Exhibits</h2>
        <span className={styles.sectionMeta}>
          {cited ? `${cited} cited · ` : ""}
          {ids.length} readable now
        </span>
      </div>
      <ol className={styles.exhibits}>
        {ids.map((id, i) => (
          <li key={id}>
            <TraceExhibit id={id} number={model.numberOf.get(id)} result={results[i]} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function TraceExhibit({ id, number, result }: { id: string; number: number | undefined; result: { data?: EvidenceDetail; isError: boolean } | undefined }) {
  const { open } = useAnswer();
  const { state } = useWorkspace();
  const selected = useStore(state.inspector, (s) => s.evidenceId === id);
  const detail = result?.data;
  const Glyph = detail ? (SOURCE_GLYPHS[detail.source_type as keyof typeof SOURCE_GLYPHS] ?? DocumentGlyph) : DocumentGlyph;
  const date = detail ? formatDay(detail.occurred_at ?? detail.document_updated_at) : null;
  const name = detail ? detail.title : result?.isError ? "Not available" : "Loading";
  return (
    <button
      type="button"
      className={`${record.row} ${styles.exhibit}`}
      aria-current={selected || undefined}
      aria-label={`Inspect evidence ${number ?? "(not cited)"}: ${name}`}
      onClick={(e) => open(id, e.currentTarget)}
      data-evidence={id}
    >
      <span className={styles.exhibitMarker}>
        <ExhibitMarker n={number ?? null} size="lg" state={selected ? "selected" : "rest"} />
      </span>
      <span className={styles.exhibitBody}>
        {detail ? (
          <>
            <span className={styles.exhibitTitle}>
              <Glyph size={14} aria-hidden="true" focusable="false" />
              <span>{detail.title}</span>
            </span>
            <span className={styles.exhibitMeta}>
              {[sourceTypeLabel(detail.source_type), detail.source_name, detail.display_ref, date].filter(Boolean).join(" · ")}
              {number === undefined && " · retrieved, not cited"}
            </span>
          </>
        ) : result?.isError ? (
          <span className={styles.exhibitMeta}>This evidence isn&apos;t available now.</span>
        ) : (
          <span className={styles.exhibitSkeleton} aria-hidden="true" />
        )}
      </span>
    </button>
  );
}

function RawRecord({ trace }: { trace: AskerTrace | AdminTrace }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <section className={`${record.text} ${styles.raw}`}>
      <button type="button" className={styles.rawToggle} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <Icon as={ChevronRight} size={14} />
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
  const stops: Stop[] = [
    { name: "Retrieval", summary: null, ms: t.retrieval, state: "done" },
    { name: "AI data policy", summary: null, ms: t.policy, state: "done" },
    { name: "Generation", summary: called ? formatCount(trace.generation_calls, "model call", "model calls") : null, ms: t.generation, state: called ? "done" : "skipped" },
    { name: "Answer", summary: `${shortStatus(trace.status)}${trace.error_code ? ` · error ${trace.error_code}` : ""}`, ms: null, state: trace.status === "failed" ? "failed" : "done" },
  ];
  return (
    <>
      <Header trace={trace} eyebrow="Trace · operational view" title="Operational trace" />
      <div className={record.text}>
        <BoundaryBand>Question, answer and evidence aren&apos;t visible to administrators — no content, counts or scope. This is the operational record only.</BoundaryBand>
      </div>
      <Pipeline stops={stops} total={t.total} />
      <RunDetails trace={trace} member />
      <RawRecord trace={trace} />
    </>
  );
}
