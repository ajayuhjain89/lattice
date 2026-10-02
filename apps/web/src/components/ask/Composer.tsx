"use client";

import { AlertCircle, ArrowUp } from "lucide-react";
import { forwardRef, useId, useImperativeHandle, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";

import styles from "./Composer.module.css";
import { MAX_QUESTION, validate, type AskForm, type FormProblem } from "./form";
import { ScopeControls } from "./ScopeControls";

export interface ComposerHandle {
  focus(): void;
}

export interface ComposerProps {
  form: AskForm;
  onChange: (form: AskForm) => void;
  onSubmit: () => void;
  pending: boolean;
  /** Nothing the person can read is indexed (B13 "Empty knowledge base"). */
  disabled?: boolean;
}

const PROBLEM: Record<FormProblem, string> = {
  empty: "Type a question first.",
  too_long: `Questions can be up to ${MAX_QUESTION.toLocaleString("en-GB")} characters.`,
  dates: "The start date must be on or before the end date.",
};

/**
 * The research query box: compact, at the top of Ask, keyboard-first. Enter asks,
 * Shift+Enter adds a line. The scope controls only narrow what may be searched; the API
 * decides what the person can read.
 */
export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer({ form, onChange, onSubmit, pending, disabled = false }, ref) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [problem, setProblem] = useState<FormProblem | null>(null);
  const problemId = useId();
  const hintId = useId();

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

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
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className={styles.composer} onSubmit={submit} aria-label="Ask Lattice" noValidate>
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
          placeholder="Ask about a customer, commitment, policy, or ticket…"
          aria-invalid={problem === "empty" || problem === "too_long" ? true : undefined}
          aria-describedby={[problem ? problemId : null, hintId].filter(Boolean).join(" ")}
          aria-keyshortcuts="/"
          maxLength={MAX_QUESTION + 500}
          disabled={disabled}
          spellCheck
        />
        {problem && (
          <span className={styles.problem} id={problemId} role="alert">
            <Icon as={AlertCircle} size={14} />
            {PROBLEM[problem]}
          </span>
        )}
      </label>
      <div className={styles.footer}>
        <ScopeControls
          form={form}
          onChange={(next) => {
            onChange(next);
            if (problem === "dates") setProblem(null);
          }}
          datesInvalid={problem === "dates"}
        />
        <div className={styles.submitGroup}>
          <span className={`${styles.hint} ${nearLimit ? styles.over : ""}`} id={hintId}>
            {nearLimit ? (
              `${length.toLocaleString("en-GB")} / ${MAX_QUESTION.toLocaleString("en-GB")}`
            ) : (
              <>
                <kbd>↵</kbd> to ask · <kbd>⇧↵</kbd> new line
              </>
            )}
          </span>
          <Button
            type="submit"
            variant="primary"
            className={styles.submit}
            icon={<Icon as={ArrowUp} />}
            loading={pending}
            disabled={disabled}
            aria-label={pending ? "Asking" : "Ask"}
          >
            <span className={styles.submitLabel}>{pending ? "Asking" : "Ask"}</span>
          </Button>
        </div>
      </div>
    </form>
  );
});
