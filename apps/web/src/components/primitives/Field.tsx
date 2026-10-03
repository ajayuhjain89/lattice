"use client";

/**
 * The field family (design-direction §6.11): one anatomy for every control.
 *
 *   <Field label="Reason" help="Recorded in the audit log." error={problem} required>
 *     {(control) => <TextArea {...control} rows={3} />}
 *   </Field>
 *
 * Field owns the ids and wiring (label → control, help and error → aria-describedby,
 * aria-invalid, required); the controls own only their look. "Required" is the control's
 * own state (announced with it), so it isn't repeated inside the label's name. Errors are an icon plus
 * text below the help, never colour alone. Choices (checkboxes, radios) are grouped
 * in a ChoiceGroup, a fieldset whose legend names the question.
 */
import { AlertCircle, Search, X } from "lucide-react";
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { Icon } from "./icons";
import styles from "./Field.module.css";

export interface ControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  required?: boolean;
}

export interface FieldProps {
  label: ReactNode;
  /** Visually hidden label (the control's purpose is clear from what surrounds it). */
  hideLabel?: boolean;
  help?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  /** Shown after the label in quiet text, e.g. "optional". */
  hint?: string;
  className?: string;
  children: (control: ControlProps) => ReactNode;
}

export function Field({ label, hideLabel = false, help, error, required = false, hint, className, children }: FieldProps) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [error ? errorId : null, help ? helpId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={[styles.field, className].filter(Boolean).join(" ")}>
      <label htmlFor={id} className={hideLabel ? "visually-hidden" : styles.label}>
        {label}
        {hint && (
          <>
            {" "}
            <span className={styles.hint}>{hint}</span>
          </>
        )}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined, required: required || undefined })}
      {help && (
        <p id={helpId} className={styles.help}>
          {help}
        </p>
      )}
      {error && (
        <p id={errorId} className={styles.error}>
          <Icon as={AlertCircle} size={14} />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}

type Size = "sm" | "md";

function control(size: Size, extra?: string) {
  return [styles.control, size === "sm" && styles.sm, extra].filter(Boolean).join(" ");
}

export interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  size?: Size;
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput({ size = "md", className, type = "text", ...rest }, ref) {
  return <input ref={ref} type={type} className={control(size, className)} {...rest} />;
});

export const DateInput = forwardRef<HTMLInputElement, Omit<TextInputProps, "type">>(function DateInput({ size = "md", className, ...rest }, ref) {
  return <input ref={ref} type="date" className={control(size, [styles.date, className].filter(Boolean).join(" "))} {...rest} />;
});

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function TextArea({ className, rows = 3, ...rest }, ref) {
  return <textarea ref={ref} rows={rows} className={[styles.control, styles.textarea, className].filter(Boolean).join(" ")} {...rest} />;
});

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  size?: Size;
}

/** A native select: the platform's own picker on phones and with assistive tech. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({ size = "md", className, children, ...rest }, ref) {
  return (
    <span className={styles.selectWrap}>
      <select ref={ref} className={control(size, [styles.select, className].filter(Boolean).join(" "))} {...rest}>
        {children}
      </select>
      <svg className={styles.chevron} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
        <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
});

export interface SearchInputProps extends Omit<TextInputProps, "type" | "onChange" | "value"> {
  value: string;
  onValueChange: (value: string) => void;
  /** Accessible name of the clear button. */
  clearLabel?: string;
}

/** A search field with a leading glyph and a clear button once there is text. */
export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput(
  { size = "md", className, value, onValueChange, clearLabel = "Clear search", onKeyDown, ...rest },
  ref,
) {
  return (
    <span className={[styles.searchWrap, size === "sm" && styles.searchSm].filter(Boolean).join(" ")}>
      <span className={styles.searchIcon} aria-hidden="true">
        <Icon as={Search} size={14} />
      </span>
      <input
        ref={ref}
        type="search"
        className={control(size, [styles.search, className].filter(Boolean).join(" "))}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) {
            e.preventDefault();
            e.stopPropagation();
            onValueChange("");
          }
          onKeyDown?.(e);
        }}
        {...rest}
      />
      {value && (
        <button type="button" className={styles.clear} aria-label={clearLabel} onClick={() => onValueChange("")}>
          <Icon as={X} size={14} />
        </button>
      )}
    </span>
  );
});

export interface ChoiceProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: ReactNode;
  /** One line under the label, tied to the input with aria-describedby. */
  help?: ReactNode;
}

function Choice({ type, label, help, id, className, ...rest }: ChoiceProps & { type: "checkbox" | "radio" }) {
  const generated = useId();
  const inputId = id ?? generated;
  const helpId = help ? `${inputId}-help` : undefined;
  return (
    <div className={[styles.choice, className].filter(Boolean).join(" ")}>
      <input id={inputId} type={type} className={type === "checkbox" ? styles.checkbox : styles.radio} aria-describedby={helpId} {...rest} />
      <span className={styles.choiceText}>
        <label htmlFor={inputId} className={styles.choiceLabel}>
          {label}
        </label>
        {help && (
          <span id={helpId} className={styles.choiceHelp}>
            {help}
          </span>
        )}
      </span>
    </div>
  );
}

export const Checkbox = (props: ChoiceProps) => <Choice type="checkbox" {...props} />;
export const Radio = (props: ChoiceProps) => <Choice type="radio" {...props} />;

export interface ChoiceGroupProps {
  legend: ReactNode;
  hideLegend?: boolean;
  help?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A fieldset of checkboxes or radios; the legend asks the question. */
export function ChoiceGroup({ legend, hideLegend = false, help, error, children, className }: ChoiceGroupProps) {
  const id = useId();
  return (
    <fieldset
      className={[styles.group, className].filter(Boolean).join(" ")}
      aria-describedby={[error ? `${id}-error` : null, help ? `${id}-help` : null].filter(Boolean).join(" ") || undefined}
      aria-invalid={error ? true : undefined}
    >
      <legend className={hideLegend ? "visually-hidden" : styles.label}>{legend}</legend>
      <div className={styles.choices}>{children}</div>
      {help && (
        <p id={`${id}-help`} className={styles.help}>
          {help}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className={styles.error}>
          <Icon as={AlertCircle} size={14} />
          <span>{error}</span>
        </p>
      )}
    </fieldset>
  );
}
