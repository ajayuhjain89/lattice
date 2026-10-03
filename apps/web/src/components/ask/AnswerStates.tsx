"use client";

import { Info, RotateCw } from "lucide-react";

import { retryPhrase, type ApiError } from "@/api/errors";
import type { Answer, AskRequest } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { EgressGlyph } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { record } from "@/components/record/RecordPage";
import { presentStatus } from "@/lib/answer-status";
import { formatDay, levelLabel } from "@/lib/format";

import styles from "./AnswerStates.module.css";

/*
 * Every Ask outcome without answer prose has its own state (design-direction §6.10), in
 * one of four families: quiet (nothing answers this), fault (something failed), wait (a
 * limit) -- and, for readable evidence the AI data policy kept from the model, the
 * boundary treatment: a known boundary that wasn't crossed. The copy under each heading is
 * the API's own fixed message.
 */

const LONG: Record<string, string> = { doc: "documents", ticket: "tickets", chat: "chat messages" };

/** What was searched, from the request that was sent -- never from anything the API withheld. */
export function Searched({ request, accountName }: { request: AskRequest; accountName: string | null }) {
  const scope = request.scope ?? {};
  const types = scope.source_types?.length ? scope.source_types.map((t) => LONG[t] ?? t).join(", ") : "every source type you can read";
  const since = scope.since ? formatDay(`${scope.since}T00:00:00Z`) : null;
  const until = scope.until ? formatDay(`${scope.until}T00:00:00Z`) : null;
  const dates = since && until ? `${since} – ${until}` : since ? `From ${since}` : until ? `Until ${until}` : "Any date";
  return (
    <dl className={styles.searched}>
      <div>
        <dt>Searched</dt>
        <dd>{types.charAt(0).toUpperCase() + types.slice(1)}</dd>
      </div>
      <div>
        <dt>Dates</dt>
        <dd>{dates}</dd>
      </div>
      {scope.account && (
        <div>
          <dt>Account</dt>
          <dd>{accountName ?? scope.account}</dd>
        </div>
      )}
      {request.classification && (
        <div>
          <dt>Question</dt>
          <dd>Treated as {levelLabel(request.classification)}</dd>
        </div>
      )}
    </dl>
  );
}

/**
 * insufficient_evidence / no_relevant_evidence: quiet, useful, not an error. The same
 * whether nothing exists or nothing is readable (SEC-5).
 */
export function NoEvidenceState({ answer, request, accountName }: { answer: Answer; request: AskRequest; accountName: string | null }) {
  const p = presentStatus(answer.status, answer.status_reason);
  const narrowed = Boolean(request.scope?.source_types?.length || request.scope?.since || request.scope?.until || request.scope?.account);
  return (
    <div className={record.text} data-state="insufficient_evidence">
      <StateBlock family="quiet" label="No answer" heading={p.heading} compact>
        {answer.message && <p>{answer.message}</p>}
        <Searched request={request} accountName={accountName} />
        <ul className={styles.tips} aria-label="Suggestions">
          <li>Name the customer, product or ticket ID the answer would mention.</li>
          <li>Use the words a source would use — a decision record, a ticket, a call note.</li>
          {narrowed && <li>Remove a filter to widen the search.</li>}
        </ul>
      </StateBlock>
    </div>
  );
}

/**
 * Evidence without prose: claims_unverified, no model available, or the AI data policy.
 * Only the policy case is a boundary (the viewer may read the sources; policy kept them
 * from the model); its hatch is drawn once, on the group of withheld sources below, so
 * this statement stays plain.
 */
export function EvidenceFirstState({ answer }: { answer: Answer }) {
  const p = presentStatus(answer.status, answer.status_reason);
  const policy = answer.status === "evidence_only" && answer.status_reason === "egress_policy";
  return (
    <div className={record.text}>
      <Callout tone={policy ? "plain" : "neutral"} label={p.reasonLabel ?? p.label} icon={<Icon as={policy ? EgressGlyph : Info} />} labelAs="p" aria-labelledby="state-heading">
        <h2 className={styles.heading} id="state-heading">
          {p.heading}
        </h2>
        {answer.message && <p className={styles.message}>{answer.message}</p>}
      </Callout>
    </div>
  );
}

/** ambiguous: the choices the API offered, and nothing more. */
export function AmbiguousState({ answer, onChoose, onEdit, pending }: { answer: Answer; onChoose: (key: string, name: string) => void; onEdit: () => void; pending: boolean }) {
  const clarification = answer.clarification;
  const choices = clarification?.choices ?? [];
  return (
    <section className={`${record.text} ${styles.ambiguous}`} aria-labelledby="state-heading" data-state="ambiguous">
      <h2 className={styles.question} id="state-heading">
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
        <div className={styles.choices}>
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
    <div className={record.text} data-state="unsupported_request">
      <StateBlock family="quiet" label="Not a knowledge question" heading={p.heading} compact>
        {answer.message && <p>{answer.message}</p>}
        <ul className={styles.tips} aria-label="Suggestions">
          <li>Ask what the sources say instead — what was agreed, when, and by whom.</li>
        </ul>
      </StateBlock>
    </div>
  );
}

/** failed: a safe message, the trace reference and a retry. Evidence, if any, follows. */
export function FailedState({ answer, onRetry, pending }: { answer: Answer; onRetry: () => void; pending: boolean }) {
  const p = presentStatus(answer.status, answer.status_reason);
  return (
    <div className={record.text}>
      <StateBlock
        family="fault"
        role="alert"
        label={p.reasonLabel ?? "Error"}
        heading={p.heading}
        compact
        reference={answer.trace_id}
        actions={
          <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={pending}>
            Try again
          </Button>
        }
      >
        {answer.message && <p>{answer.message}</p>}
      </StateBlock>
    </div>
  );
}

/**
 * The Ask request itself failed (no Answer). The question stays in the composer. A rate
 * limit is a wait, not a fault; everything else is a fault with the request reference.
 */
export function RequestError({ error, onRetry, onClearAccount, pending }: { error: ApiError; onRetry: () => void; onClearAccount?: () => void; pending: boolean }) {
  const network = error.kind === "network";
  const invalid = error.status === 422;
  const limited = error.isRateLimited;
  const actions =
    invalid && onClearAccount ? (
      <Button onClick={onClearAccount}>Remove account filter</Button>
    ) : !invalid ? (
      <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={pending}>
        Try again
      </Button>
    ) : null;
  if (limited) {
    return (
      <div className={record.text}>
        <StateBlock family="wait" role="status" heading="Too many questions in a row" compact reference={error.requestId} actions={actions}>
          <p>Lattice limits how often each person can ask. Try again {retryPhrase(error.retryAfter)}; your question is still in the box above.</p>
        </StateBlock>
      </div>
    );
  }
  return (
    <div className={record.text}>
      <StateBlock
        family="fault"
        role="alert"
        label={network ? "Connection" : "Error"}
        heading={network ? "Couldn't reach Lattice" : invalid ? "Lattice couldn't run this question" : "The question couldn't be answered"}
        compact
        reference={error.requestId}
        actions={actions}
      >
        <p>
          {network
            ? "Check your connection and try again. Your question is still in the box above."
            : invalid && onClearAccount
              ? "The account filter can't be used. Remove it and ask again."
              : error.message}
        </p>
      </StateBlock>
    </div>
  );
}
