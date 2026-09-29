"use client";

import { memo, useCallback, useMemo } from "react";

import type { Answer, AskRequest } from "@/api/types";
import { Info } from "lucide-react";

import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";
import { useWorkspace } from "@/components/shell/workspace-context";
import { buildAnswerModel, groupEvidence } from "@/lib/answer-model";

import { AnswerContextProvider } from "./answer-context";
import { Conflicts, Notices, Prose, StatusLine, Unknowns } from "./Brief";
import styles from "./Brief.module.css";
import { EvidenceList } from "./EvidenceList";
import { AmbiguousState, EvidenceFirstState, FailedState, NoEvidenceState, UnsupportedState } from "./StateBlocks";

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
 * One Ask response. Every status has its own presentation; the view model is derived once
 * and the inspector's selection never re-renders it (only chips and rows subscribe).
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

  const open = useCallback(
    (evidenceId: string, from: HTMLElement | null) =>
      state.inspector.set({
        evidenceId,
        returnFocus: from,
        context: { numberOf: model.numberOf, citedBy: model.citedBy, order, items },
      }),
    [state.inspector, model, order, items],
  );
  const context = useMemo(() => ({ model, items, open }), [model, items, open]);

  const cited = answer.evidence.filter((e) => model.numberOf.has(e.evidence_id));
  const hasProse = model.lead.length + model.supporting.length > 0;
  const status = answer.status;
  const reason = answer.status_reason;
  const asked = request.question.trim();
  const showAsked = draft.trim() !== asked;
  // A partial answer's own message is shown unless a notice already says the same.
  const partialNote =
    status === "partial" && answer.message && !answer.notices.some((n) => n.message === answer.message) ? answer.message : null;

  return (
    <AnswerContextProvider value={context}>
      <article aria-labelledby="brief-heading" data-status={status} data-testid="answer">
        <h2 id="brief-heading" className="visually-hidden">
          Answer
        </h2>
        {showAsked && (
          <p className={styles.askedAs}>
            Answer to <strong>“{asked}”</strong>
          </p>
        )}
        <StatusLine answer={answer} elapsedMs={elapsedMs} traceHref={`/traces/${answer.trace_id}`} />
      </article>

      {status === "insufficient_evidence" && reason !== "claims_unverified" && (
        <NoEvidenceState answer={answer} request={request} accountName={accountName} />
      )}
      {(status === "evidence_only" || (status === "insufficient_evidence" && reason === "claims_unverified")) && (
        <EvidenceFirstState answer={answer} />
      )}
      {status === "ambiguous" && <AmbiguousState answer={answer} onChoose={onChoose} onEdit={onEdit} pending={pending} />}
      {status === "unsupported_request" && <UnsupportedState answer={answer} />}
      {status === "failed" && <FailedState answer={answer} onRetry={onRetry} pending={pending} />}

      {/* Under evidence_only the state block already says why nothing was sent. */}
      <Notices notices={status === "evidence_only" ? answer.notices.filter((n) => n.code !== "egress_excluded") : answer.notices} cited={cited} />
      {partialNote && <p className={styles.support}>{partialNote}</p>}

      {(status === "answered" || status === "partial") &&
        (hasProse ? (
          <Prose />
        ) : (
          <Callout tone="info" label="Answer withheld" icon={<Icon as={Info} />}>
            <p>
              Lattice couldn&apos;t match this answer&apos;s statements to the evidence it returned, so none are shown.
              Review the evidence below.
            </p>
          </Callout>
        ))}
      <Conflicts />
      {(status === "answered" || status === "partial") && <Unknowns unknowns={answer.unknowns} />}

      {status !== "ambiguous" && (
        <EvidenceList
          evidence={answer.evidence}
          collapsed={status === "unsupported_request"}
          showReasons={modelInPlay(status, reason)}
        />
      )}
    </AnswerContextProvider>
  );
});
