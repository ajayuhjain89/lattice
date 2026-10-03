"use client";

import { AlertCircle, ArrowUp } from "lucide-react";
import { forwardRef, useId, useImperativeHandle, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { Kbd } from "@/components/primitives/Kbd";

import styles from "./Composer.module.css";
import { MAX_QUESTION, validate, type AskForm, type FormProblem } from "./form";
import { ScopeLine } from "./ScopeLine";

export interface ComposerHandle {
  focus(): void;
}

export interface ComposerProps {
  form: AskForm;
  onChange: (form: AskForm) => void;
  onSubmit: () => void;
  pending: boolean;
  /** Nothing the person can read is indexed (the empty knowledge base). */
  disabled?: boolean;
}

const PROBLEM: Record<FormProblem, string> = {
  empty: "Type a question first.",
  too_long: `Questions can be up to ${MAX_QUESTION.toLocaleString("en-GB")} characters.`,
  dates: "The start date must be on or before the end date.",
};

/**
 * The Ask desk's composer: a research query on the reading sheet, keyboard-first. Enter
 * (or ⌘/Ctrl Enter) asks, Shift+Enter adds a line, and the box grows with the question.
 * The scope sentence in its footer only narrows what may be searched; the API decides
 * what the person can read.
 */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer({ form, onChange, onSubmit, pending, disabled = false }, ref) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [problem, setProblem] = useState<FormProblem | null>(null);
  const problemId = useId();
  const hintId = useId();

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

  // Grow with the text; the stylesheet caps it at eight lines, after which it scrolls.
  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [form.question]);

  const length = form.question.trim().length;
  const nearLimit = length > MAX_QUESTION - 200;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (pending || disabled) return;
    const found = validate(form);
    setProblem(found);
    if (found) {
      textarea.current?.focus();
      return;
    }
    onSubmit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className={styles.composer} onSubmit={submit} aria-label="Ask Lattice" noValidate data-disabled={disabled || undefined}>
      <label className={styles.field}>
        <span className="visually-hidden">Question</span>
        <textarea
          ref={textarea}
          className={styles.textarea}
          name="question"
          rows={2}
          value={form.question}
          onChange={(e) => {
            onChange({ ...form, question: e.target.value });
            if (problem === "empty" || problem === "too_long") setProblem(null);
          }}
          onKeyDown={onKeyDown}
          placeholder="Ask about a customer, a commitment, a policy or a ticket…"
          aria-invalid={problem === "empty" || problem === "too_long" ? true : undefined}
          aria-describedby={[problem ? problemId : null, hintId].filter(Boolean).join(" ")}
          aria-keyshortcuts="/"
          maxLength={MAX_QUESTION + 500}
          disabled={disabled}
          spellCheck
        />
      </label>
      {problem && (
        <p className={styles.problem} id={problemId} role="alert">
          <Icon as={AlertCircle} size={14} />
          {PROBLEM[problem]}
        </p>
      )}
      <div className={styles.footer}>
        <div className={styles.scope}>
          <ScopeLine
            form={form}
            onChange={(next) => {
              onChange(next);
              if (problem === "dates") setProblem(null);
            }}
            datesInvalid={problem === "dates"}
          />
        </div>
        <div className={styles.submitGroup}>
          <span className={`${styles.hint} ${nearLimit ? styles.over : ""}`} id={hintId}>
            {nearLimit ? (
              `${length.toLocaleString("en-GB")} / ${MAX_QUESTION.toLocaleString("en-GB")} characters`
            ) : (
              <>
                <Kbd>↵</Kbd> to ask · <Kbd>⇧↵</Kbd> new line
              </>
            )}
          </span>
          <Button type="submit" variant="primary" icon={<Icon as={ArrowUp} />} loading={pending} disabled={disabled} aria-label={pending ? "Asking" : "Ask"}>
            {pending ? "Asking" : "Ask"}
          </Button>
        </div>
      </div>
    </form>
  );
});
