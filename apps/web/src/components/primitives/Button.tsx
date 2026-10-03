import Link from "next/link";
import { forwardRef, type ButtonHTMLAttributes, type ComponentProps, type ReactNode } from "react";

import styles from "./Button.module.css";

/**
 * primary   ink fill -- one per view region
 * secondary control-boundary edge
 * quiet     no edge; toolbars, row tools, dismissals
 * danger    an action that removes access or data (always confirmed elsewhere)
 *
 * `aria-pressed` turns any variant into a toggle with a visible pressed state.
 */
export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

interface Common {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  /** Icon after the label (e.g. an outbound-link arrow). */
  iconEnd?: ReactNode;
}

function classes(variant: ButtonVariant, size: ButtonSize, iconOnly: boolean, extra?: string) {
  return [styles.button, styles[variant], size !== "md" && styles[size], iconOnly && styles.iconOnly, extra]
    .filter(Boolean)
    .join(" ");
}

function Content({ icon, iconEnd, loading, children }: { icon?: ReactNode; iconEnd?: ReactNode; loading?: boolean; children?: ReactNode }) {
  return (
    <>
      {(icon || loading) && (
        <span className={styles.icon} aria-hidden="true">
          {loading ? <span className={styles.spinner} /> : icon}
        </span>
      )}
      {children}
      {iconEnd && (
        <span className={styles.icon} aria-hidden="true">
          {iconEnd}
        </span>
      )}
    </>
  );
}

export interface ButtonProps extends Common, ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * Busy: the label stays (width doesn't change), the icon becomes a spinner, clicks stop.
   * The button stays focusable (aria-disabled, not `disabled`), so keyboard focus isn't
   * dropped to the page while the request runs.
   */
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, iconEnd, loading = false, className, children, disabled, type, onClick, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={classes(variant, size, !children, className)}
      disabled={disabled}
      aria-busy={loading || undefined}
      aria-disabled={loading || undefined}
      onClick={(event) => {
        if (loading) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
      {...rest}
    >
      <Content icon={icon} iconEnd={iconEnd} loading={loading}>
        {children}
      </Content>
    </button>
  );
});

export interface ButtonLinkProps extends Common, Omit<ComponentProps<typeof Link>, "children"> {
  children?: ReactNode;
}

export function ButtonLink({ variant = "secondary", size = "md", icon, iconEnd, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes(variant, size, !children, className)} {...rest}>
      <Content icon={icon} iconEnd={iconEnd}>
        {children}
      </Content>
    </Link>
  );
}

export interface ExternalLinkButtonProps extends Common {
  href: string;
  children: ReactNode;
}

export function ExternalLinkButton({ variant = "secondary", size = "md", icon, iconEnd, href, children }: ExternalLinkButtonProps) {
  return (
    <a className={classes(variant, size, false)} href={href} target="_blank" rel="noopener noreferrer">
      <Content icon={icon} iconEnd={iconEnd}>
        {children}
        <span className="visually-hidden"> (opens in a new tab)</span>
      </Content>
    </a>
  );
}
