"use client";

import { Info } from "lucide-react";
import { memo, useCallback, useMemo } from "react";

import type { Answer, AskRequest } from "@/api/types";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";
import { record } from "@/components/record/RecordPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { buildAnswerModel, groupEvidence } from "@/lib/answer-model";
import { createStore } from "@/lib/store";

import { AnswerContextProvider, NO_POINTER, type Pointer } from "./answer-context";
import { AmbiguousState, EvidenceFirstState, FailedState, NoEvidenceState, UnsupportedState } from "./AnswerStates";
import { answerText, Disagreements, Notices, Statements, StatusLine, Unknowns } from "./Brief";
import styles from "./AnswerView.module.css";
import { Exhibits } from "./Exhibits";
import { ProvenanceMap } from "./ProvenanceMap";

export interface AnswerViewProps {
  answer: Answer;
  request: AskRequest;
  accountName: string | null;
  elapsedMs?: number;
  /** The composer's current text: when it differs, the brief says what it answers. */
  draft: string;
  pending: boolean;
  onRetry: () => void;
  onChoose: (key: string, name: string) => void;
  onEdit: () => void;
}

/**
 * Whether per-item "not sent to the AI model" reasons mean anything for this outcome. The
 * API marks items it did not send even when no model call was possible (no provider, an
 * action request, nothing relevant); there the status already says why, and a per-item
 * "AI data policy" note would contradict it.
 */
function modelInPlay(status: Answer["status"], reason: string | null): boolean {
  if (status === "evidence_only") return reason === "egress_policy";
  if (status === "insufficient_evidence") return reason === "claims_unverified";
  return status === "answered" || status === "partial" || status === "failed";
}

/**
 * One Ask response as a record (design-direction §6.5): the ledger line, notices, the
 * claims with their exhibit markers, any disagreement, what the evidence doesn't
 * establish, the provenance lattice when it adds information, and the exhibits. Every
 * status has its own presentation. The view model is derived once; selection and
 * pointing never re-render it (only the markers, claims and rows they touch subscribe).
 */
export const AnswerView = memo(function AnswerView(props: AnswerViewProps) {
  const { answer, request, accountName, elapsedMs, draft, pending, onRetry, onChoose, onEdit } = props;
  const { state } = useWorkspace();

  const items = useMemo(() => new Map(answer.evidence.map((e) => [e.evidence_id, e])), [answer.evidence]);
  const model = useMemo(() => buildAnswerModel(answer, new Set(items.keys())), [answer, items]);
  const order = useMemo(() => {
    const g = groupEvidence(answer.evidence, model);
    return [...g.cited, ...g.policyExcluded, ...g.other].map((e) => e.evidence_id);
  }, [answer.evidence, model]);
  const pointer = useMemo(() => createStore<Pointer>(NO_POINTER), []);

  const open = useCallback(
    (evidenceId: string, from: HTMLElement | null, claim: string | null = null) =>
      state.inspector.set({
        evidenceId,
        claim,
        returnFocus: from,
        context: { numberOf: model.numberOf, citedBy: model.citedBy, order, items },
      }),
    [state.inspector, model, order, items],
  );
  const context = useMemo(() => ({ model, items, open, pointer }), [model, items, open, pointer]);

  const cited = answer.evidence.filter((e) => model.numberOf.has(e.evidence_id));
  const hasProse = model.lead.length + model.supporting.length > 0;
  const status = answer.status;
  const reason = answer.status_reason;
  const asked = request.question.trim();
  const showAsked = draft.trim() !== asked;
  const prose = status === "answered" || status === "partial";
  // A partial answer's own message is shown unless a notice already says the same.
  const partialNote = status === "partial" && answer.message && !answer.notices.some((n) => n.message === answer.message) ? answer.message : null;
  const notices = status === "evidence_only" ? answer.notices.filter((n) => n.code !== "egress_excluded") : answer.notices;

  return (
    <AnswerContextProvider value={context}>
      <article className={styles.answer} aria-labelledby="brief-heading" data-status={status} data-testid="answer">
        <h2 id="brief-heading" className="visually-hidden">
          Answer
        </h2>
        <div className={`${record.text} ${styles.head}`}>
          {showAsked && (
            <p className={styles.askedAs}>
              Answer to <q className={styles.askedQuestion}>{asked}</q>
            </p>
          )}
          <StatusLine answer={answer} elapsedMs={elapsedMs} traceHref={`/traces/${answer.trace_id}`} copy={prose && hasProse ? () => answerText(model, items) : undefined} />
          {/* Under evidence_only the state below already says why nothing was sent. */}
          {(notices.length > 0 || cited.some((e) => e.classification !== "internal")) && <Notices notices={notices} cited={cited} />}
          {partialNote && <p className={styles.note}>{partialNote}</p>}
        </div>

        {status === "insufficient_evidence" && reason !== "claims_unverified" && <NoEvidenceState answer={answer} request={request} accountName={accountName} />}
        {(status === "evidence_only" || (status === "insufficient_evidence" && reason === "claims_unverified")) && <EvidenceFirstState answer={answer} />}
        {status === "ambiguous" && <AmbiguousState answer={answer} onChoose={onChoose} onEdit={onEdit} pending={pending} />}
        {status === "unsupported_request" && <UnsupportedState answer={answer} />}
        {status === "failed" && <FailedState answer={answer} onRetry={onRetry} pending={pending} />}

        {prose &&
          (hasProse ? (
            <Statements />
          ) : (
            <div className={record.text}>
              <Callout tone="neutral" label="Answer withheld" icon={<Icon as={Info} />}>
                <p>Lattice couldn&apos;t match this answer&apos;s statements to the evidence it returned, so none are shown. Review the evidence below.</p>
              </Callout>
            </div>
          ))}
        <Disagreements />
        {prose && <Unknowns unknowns={answer.unknowns} />}
        <ProvenanceMap />

        {status !== "ambiguous" && <Exhibits evidence={answer.evidence} collapsed={status === "unsupported_request"} showReasons={modelInPlay(status, reason)} />}
      </article>
    </AnswerContextProvider>
  );
});
