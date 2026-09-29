import Link from "next/link";
import { forwardRef, type ButtonHTMLAttributes, type ComponentProps, type ReactNode } from "react";

import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "ghost";
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

export interface ButtonProps extends Common, ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, iconEnd, loading = false, className, children, disabled, type, ...rest },
  ref,
) {
  const iconOnly = !children;
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={classes(variant, size, iconOnly, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
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
    </button>
  );
});

export interface ButtonLinkProps extends Common, Omit<ComponentProps<typeof Link>, "children"> {
  children?: ReactNode;
}

export function ButtonLink({ variant = "secondary", size = "md", icon, iconEnd, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes(variant, size, !children, className)} {...rest}>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
      {iconEnd && (
        <span className={styles.icon} aria-hidden="true">
          {iconEnd}
        </span>
      )}
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
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
      {iconEnd && (
        <span className={styles.icon} aria-hidden="true">
          {iconEnd}
        </span>
      )}
      <span className="visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}
