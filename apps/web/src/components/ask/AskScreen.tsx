"use client";

import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useAsk, useSources } from "@/api/queries";
import { isApiError, unexpectedError } from "@/api/errors";
import type { AskRequest } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { RecordPage, record } from "@/components/record/RecordPage";
import { TOPBAR_ACTION_ID } from "@/components/shell/PhoneBars";
import { useWorkspace } from "@/components/shell/workspace-context";
import { presentStatus } from "@/lib/answer-status";
import { formatCount } from "@/lib/format";
import { useStore } from "@/lib/store";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";
import { takeDraft, type AskState } from "@/lib/workspace-state";

import { RequestError } from "./AnswerStates";
import { AnswerView } from "./AnswerView";
import { DeskContext, DeskHeading } from "./AskDesk";
import { AskProgress } from "./AskProgress";
import styles from "./AskScreen.module.css";
import { Composer, type ComposerHandle } from "./Composer";
import { EMPTY_FORM, fromRequest, toRequest, type AskForm } from "./form";

/**
 * Ask: the desk before a question (heading, composer with its scope sentence, the
 * person's reach and recent questions), then -- under the same composer -- the honest
 * progress line and the brief. One question at a time; nothing about it is kept beyond
 * this tab (past questions come back from /v1/activity, re-authorized).
 */
export function AskScreen() {
  const { me, state, isAdmin } = useWorkspace();
  const sources = useSources();
  // Only an answer from the API can make the knowledge base "empty"; a failed listing doesn't.
  const empty = sources.data?.sources.length === 0;
  const ask = useStore(state.ask, (s) => s);
  const mutation = useAsk();
  const composer = useRef<ComposerHandle>(null);
  const composerBox = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState<AskForm>(() => {
    const last = state.ask.get();
    const draft = takeDraft();
    const prefilled = state.prefill.get();
    const base = last.request ? fromRequest(last.request, last.accountName) : EMPTY_FORM;
    if (prefilled !== null) return { ...base, question: prefilled };
    return draft ? { ...base, question: draft } : base;
  });

  // Keep the draft where a sign-in redirect can find it.
  useEffect(() => state.draft.set(form.question), [form.question, state.draft]);

  // "Ask “…”" chosen in Jump: the question goes into the composer (not sent), with focus.
  // Arriving from another page it was read by the initializer above; on this page it
  // arrives through the store.
  useEffect(() => {
    let frame = 0;
    const take = (arriving: boolean) => {
      const question = state.prefill.get();
      if (question === null) return;
      state.prefill.set(null);
      if (!arriving) setForm((f) => ({ ...f, question }));
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => composer.current?.focus());
    };
    take(true);
    const off = state.prefill.subscribe(() => take(false));
    return () => {
      off();
      cancelAnimationFrame(frame);
    };
  }, [state.prefill]);

  const run = useCallback(
    (request: AskRequest, accountName: string | null) => {
      const startedAt = Date.now();
      state.inspector.set({ evidenceId: null, returnFocus: null, context: null });
      state.ask.set({ request, accountName, answer: null, error: null, pending: true, startedAt });
      mutation.mutate(request, {
        onSuccess: (answer) => {
          state.ask.set({ request, accountName, answer, error: null, pending: false, startedAt, elapsedMs: Date.now() - startedAt });
        },
        onError: (error) => {
          state.ask.set({ request, accountName, answer: null, error: isApiError(error) ? error : unexpectedError(), pending: false, startedAt });
        },
      });
    },
    [mutation, state],
  );

  const submit = () => run(toRequest(form), form.account?.name ?? null);
  const retry = () => ask.request && run(ask.request, ask.accountName);
  const choose = (key: string, name: string) => {
    const next = { ...form, question: ask.request?.question ?? form.question, account: { key, name } };
    setForm(next);
    run(toRequest(next), name);
  };
  const clearAccount = () => setForm((f) => ({ ...f, account: null }));
  const focusComposer = useCallback(() => composer.current?.focus(), []);
  const askAgain = useCallback((question: string) => {
    setForm((f) => ({ ...f, question }));
    requestAnimationFrame(() => composer.current?.focus());
  }, []);

  // "/" focuses the composer from anywhere on Ask (not while typing elsewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable='true'], [role='dialog']")) return;
      e.preventDefault();
      composer.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const hasResult = Boolean(ask.pending || ask.answer || ask.error);

  return (
    <RecordPage className={styles.page}>
      <NewQuestionButton
        target={composerBox}
        onNew={() => {
          setForm((f) => ({ ...f, question: "" }));
          focusComposer();
        }}
      />
      {hasResult ? <h1 className="visually-hidden">Ask</h1> : <DeskHeading organization={me.organization.name} />}
      <div ref={composerBox} className={`${record.text} ${styles.composer}`}>
        <Composer ref={composer} form={form} onChange={setForm} onSubmit={submit} pending={ask.pending} disabled={empty} />
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {announce(ask)}
      </p>
      {!hasResult && <DeskContext isAdmin={isAdmin} onAskAgain={askAgain} />}
      {hasResult && (
        <div className={styles.result} key={ask.startedAt ?? 0} aria-busy={ask.pending || undefined}>
          {ask.pending && ask.startedAt !== null && ask.request ? (
            <AskProgress startedAt={ask.startedAt} />
          ) : ask.error ? (
            <RequestError error={ask.error} onRetry={retry} pending={ask.pending} onClearAccount={ask.error.status === 422 && ask.request?.scope?.account ? clearAccount : undefined} />
          ) : ask.answer && ask.request ? (
            <AnswerView
              answer={ask.answer}
              request={ask.request}
              accountName={ask.accountName}
              elapsedMs={ask.elapsedMs}
              draft={form.question}
              pending={ask.pending}
              onRetry={retry}
              onChoose={choose}
              onEdit={focusComposer}
            />
          ) : null}
        </div>
      )}
    </RecordPage>
  );
}

function announce(ask: AskState): string {
  if (ask.pending) return "Asking Lattice…";
  // A failed request announces itself (its block is an alert, or a status for a limit).
  if (ask.error) return "";
  if (ask.answer) {
    const label = presentStatus(ask.answer.status, ask.answer.status_reason).label;
    const claims = ask.answer.claims.length;
    return `Answer ready: ${label}${claims ? `, ${formatCount(claims, "claim", "claims")}` : ""}.`;
  }
  return "";
}

/**
 * Phones: the composer is not sticky (it would steal reading space); once it scrolls
 * away, *New question* in the top bar clears it, brings it back and focuses it.
 */
function NewQuestionButton({ target, onNew }: { target: React.RefObject<HTMLDivElement | null>; onNew: () => void }) {
  const phone = !useMinWidth(BREAKPOINTS.mobile);
  const [away, setAway] = useState(false);
  useEffect(() => {
    const el = target.current;
    if (!el || !phone) return;
    const observer = new IntersectionObserver(([entry]) => setAway(!entry?.isIntersecting), { rootMargin: "-52px 0px 0px 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [target, phone]);

  const slot = phone && away ? document.getElementById(TOPBAR_ACTION_ID) : null;
  if (!slot) return null;
  return createPortal(
    <Button
      size="lg"
      variant="quiet"
      icon={<Icon as={Plus} />}
      onClick={() => {
        target.current?.scrollIntoView({ block: "start" });
        onNew();
      }}
    >
      New question
    </Button>,
    slot,
  );
}
