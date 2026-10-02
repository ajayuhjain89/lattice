"use client";

import { Info, RotateCw, XCircle } from "lucide-react";

import type { Answer, AskRequest } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";
import { presentStatus } from "@/lib/answer-status";
import { formatDay, levelLabel } from "@/lib/format";

import styles from "./States.module.css";

const LONG: Record<string, string> = { doc: "documents", ticket: "tickets", chat: "chat messages" };

/** What was searched, from the request that was sent -- never from anything the API withheld. */
function Searched({ request, accountName }: { request: AskRequest; accountName: string | null }) {
  const scope = request.scope ?? {};
  const types = scope.source_types?.length ? scope.source_types.map((t) => LONG[t] ?? t).join(", ") : "Every source type you can read";
  const since = scope.since ? formatDay(`${scope.since}T00:00:00Z`) : null;
  const until = scope.until ? formatDay(`${scope.until}T00:00:00Z`) : null;
  const dates = since && until ? `${since} – ${until}` : since ? `From ${since}` : until ? `Until ${until}` : "All dates";
  return (
    <dl className={styles.searched}>
      <dt>Searched</dt>
      <dd>{types.charAt(0).toUpperCase() + types.slice(1)}</dd>
      <dt>Dates</dt>
      <dd>{dates}</dd>
      {scope.account && (
        <>
          <dt>Account</dt>
          <dd>{accountName ?? scope.account}</dd>
        </>
      )}
      {request.classification && (
        <>
          <dt>Question</dt>
          <dd>Treated as {levelLabel(request.classification)}</dd>
        </>
      )}
    </dl>
  );
}

/**
 * insufficient_evidence / no_relevant_evidence: a quiet, useful state -- not an error.
 * The same whether nothing exists or nothing is readable (SEC-5).
 */
export function NoEvidenceState({ answer, request, accountName }: { answer: Answer; request: AskRequest; accountName: string | null }) {
  const p = presentStatus(answer.status, answer.status_reason);
  return (
    <section className={`${styles.block} ${styles.plain}`} aria-labelledby="state-heading" data-state="insufficient_evidence">
      <h2 className={styles.heading} id="state-heading">
        {p.heading}
      </h2>
      {answer.message && <p className={styles.message}>{answer.message}</p>}
      <Searched request={request} accountName={accountName} />
      <ul className={styles.tips} aria-label="Suggestions">
        <li>Name the customer, product or ticket ID the answer would mention.</li>
        <li>Use the words a source would use — a decision record, a ticket, a call note.</li>
        {(request.scope?.source_types?.length || request.scope?.since || request.scope?.until || request.scope?.account) && (
          <li>Remove a filter to widen the search.</li>
        )}
      </ul>
    </section>
  );
}

/** insufficient_evidence / claims_unverified and evidence_only: evidence without prose. */
export function EvidenceFirstState({ answer }: { answer: Answer }) {
  const p = presentStatus(answer.status, answer.status_reason);
  return (
    <Callout tone="neutral" label={p.reasonLabel ?? p.label} icon={<Icon as={Info} />} labelAs="p" aria-labelledby="state-heading">
      <h2 className={styles.heading} id="state-heading">
        {p.heading}
      </h2>
      {answer.message && <p className={styles.calloutMessage}>{answer.message}</p>}
    </Callout>
  );
}

/** ambiguous: the choices the API offered, and nothing more. */
export function AmbiguousState({
  answer,
  onChoose,
  onEdit,
  pending,
}: {
  answer: Answer;
  onChoose: (key: string, name: string) => void;
  onEdit: () => void;
  pending: boolean;
}) {
  const clarification = answer.clarification;
  const choices = clarification?.choices ?? [];
  return (
    <section className={`${styles.block} ${styles.plain}`} aria-labelledby="state-heading" data-state="ambiguous">
      <h2 className={styles.heading} id="state-heading">
        {clarification?.question ?? answer.message ?? "Lattice needs a little more detail to answer this."}
      </h2>
      {choices.length > 0 ? (
        <>
          <p className={styles.message}>Choose one to search only that account.</p>
          <div className={styles.choices} role="group" aria-label="Choices">
            {choices.map((c) => (
              <Button key={c.key} onClick={() => onChoose(c.key, c.name)} disabled={pending}>
                {c.name}
              </Button>
            ))}
          </div>
        </>
      ) : (
        <div className={styles.actions}>
          <p className={styles.message}>Add the missing detail to your question and ask again.</p>
          <Button onClick={onEdit}>Edit question</Button>
        </div>
      )}
    </section>
  );
}

/** unsupported_request: Lattice answers; it does not act. Nothing was done. */
export function UnsupportedState({ answer }: { answer: Answer }) {
  const p = presentStatus(answer.status, answer.status_reason);
  return (
    <section className={`${styles.block} ${styles.plain}`} aria-labelledby="state-heading" data-state="unsupported_request">
      <h2 className={styles.heading} id="state-heading">
        {p.heading}
      </h2>
      {answer.message && <p className={styles.message}>{answer.message}</p>}
      <ul className={styles.tips} aria-label="Suggestions">
        <li>Ask what the sources say instead — what was agreed, when, and by whom.</li>
      </ul>
    </section>
  );
}

/** failed: a safe message, the trace reference and a retry. Evidence, if any, follows. */
export function FailedState({ answer, onRetry, pending }: { answer: Answer; onRetry: () => void; pending: boolean }) {
  const p = presentStatus(answer.status, answer.status_reason);
  return (
    <Callout tone="fault" label={p.reasonLabel ?? p.label} icon={<Icon as={XCircle} />} role="alert" aria-labelledby="state-heading">
      <h2 className={styles.heading} id="state-heading">
        {p.heading}
      </h2>
      {answer.message && <p className={styles.calloutMessage}>{answer.message}</p>}
      <div className={styles.calloutActions}>
        <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={pending}>
          Try again
        </Button>
        <span className={styles.reference}>Trace {answer.trace_id}</span>
      </div>
    </Callout>
  );
}
