"use client";

import { AlertCircle } from "lucide-react";
import { useId } from "react";

import { Icon } from "@/components/primitives/icons";

import styles from "./Access.module.css";

export const MAX_REASON = 500;

/** The required reason of every access change: labelled by "Reason" alone, help and error described. */
export function ReasonField({
  value,
  onChange,
  invalid,
  onBlur,
  placeholder,
  help = "Recorded in the audit log with your name. Every administrator can read it; don't paste document content.",
  error = "Give a reason for this change.",
}: {
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  onBlur?: () => void;
  placeholder: string;
  help?: string;
  error?: string;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        Reason
      </label>
      <textarea
        id={id}
        className={`${styles.textarea} ${invalid ? styles.invalid : ""}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        maxLength={MAX_REASON}
        aria-invalid={invalid || undefined}
        aria-describedby={`${id}-help${invalid ? ` ${id}-error` : ""}`}
        placeholder={placeholder}
      />
      <span className={styles.help} id={`${id}-help`}>
        {help}
      </span>
      {invalid && (
        <span className={styles.problem} id={`${id}-error`} role="alert">
          <Icon as={AlertCircle} size={14} /> {error}
        </span>
      )}
    </div>
  );
}
