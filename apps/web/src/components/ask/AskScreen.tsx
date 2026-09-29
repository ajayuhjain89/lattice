"use client";

import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useAsk } from "@/api/queries";
import { isApiError, unexpectedError } from "@/api/errors";
import type { AskRequest } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { PageHeader } from "@/components/shell/PageHeader";
import { TOPBAR_ACTION_ID } from "@/components/shell/TopBar";
import { useWorkspace } from "@/components/shell/workspace-context";
import { presentStatus } from "@/lib/answer-status";
import { useStore } from "@/lib/store";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";
import { HISTORY_LIMIT, takeDraft } from "@/lib/workspace-state";

import { AnswerView } from "./AnswerView";
import { AskIntro } from "./AskIntro";
import styles from "./AskPage.module.css";
import { AskProgress } from "./AskProgress";
import { Composer, type ComposerHandle } from "./Composer";
import { EMPTY_FORM, fromRequest, toRequest, type AskForm } from "./form";
import { RequestError } from "./RequestError";

export function AskScreen() {
  const { me, state } = useWorkspace();
  const ask = useStore(state.ask, (s) => s);
  const mutation = useAsk();
  const composer = useRef<ComposerHandle>(null);
  const composerBox = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState<AskForm>(() => {
    const last = state.ask.get();
    const draft = takeDraft();
    const base = last.request ? fromRequest(last.request, last.accountName) : EMPTY_FORM;
    return draft ? { ...base, question: draft } : base;
  });

  // Keep the draft where a sign-in redirect can find it.
  useEffect(() => state.draft.set(form.question), [form.question, state.draft]);

  const run = useCallback(
    (request: AskRequest, accountName: string | null) => {
      const startedAt = Date.now();
      state.inspector.set({ evidenceId: null, returnFocus: null, context: null });
      state.ask.set({ request, accountName, answer: null, error: null, pending: true, startedAt });
      mutation.mutate(request, {
        onSuccess: (answer) => {
          state.ask.set({ request, accountName, answer, error: null, pending: false, startedAt, elapsedMs: Date.now() - startedAt });
          state.history.set((h) =>
            [{ traceId: answer.trace_id, question: request.question, status: answer.status }, ...h.filter((x) => x.traceId !== answer.trace_id)].slice(0, HISTORY_LIMIT),
          );
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

  const hasResult = ask.pending || ask.answer || ask.error;

  return (
    <>
      <PageHeader trail={[{ label: "Ask" }]} />
      <NewQuestionButton target={composerBox} onFocus={() => composer.current?.focus()} />
      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.column}>
          {!hasResult && <AskIntro organization={me.organization.name} />}
          <div ref={composerBox}>
            <Composer ref={composer} form={form} onChange={setForm} onSubmit={submit} pending={ask.pending} />
          </div>
          <p className="visually-hidden" role="status" aria-live="polite">
            {announce(ask)}
          </p>
          <div className={styles.result} key={ask.startedAt ?? 0} aria-busy={ask.pending || undefined}>
            {ask.pending && ask.startedAt !== null && ask.request ? (
              <AskProgress startedAt={ask.startedAt} />
            ) : ask.error ? (
              <RequestError
                error={ask.error}
                onRetry={retry}
                pending={ask.pending}
                onClearAccount={ask.error.status === 422 && ask.request?.scope?.account ? clearAccount : undefined}
              />
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
                onEdit={() => composer.current?.focus()}
              />
            ) : null}
          </div>
        </div>
      </main>
    </>
  );
}

function announce(ask: { pending: boolean; answer: { status: string } | null; error: unknown }): string {
  if (ask.pending) return "Asking Lattice…";
  if (ask.error) return "The question couldn't be answered.";
  if (ask.answer) return `Answer ready: ${presentStatus(ask.answer.status as Parameters<typeof presentStatus>[0], null).label}.`;
  return "";
}

/**
 * Phones: the composer is not sticky (it would steal reading space); once it scrolls
 * away, a "New question" button in the top bar brings it back (design-system §B14).
 */
function NewQuestionButton({ target, onFocus }: { target: React.RefObject<HTMLDivElement | null>; onFocus: () => void }) {
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
      variant="ghost"
      icon={<Icon as={Plus} />}
      onClick={() => {
        target.current?.scrollIntoView({ block: "start" });
        onFocus();
      }}
    >
      New question
    </Button>,
    slot,
  );
}
