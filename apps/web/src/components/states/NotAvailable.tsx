import { EyeOff } from "lucide-react";

import { ButtonLink } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";

import styles from "./States.module.css";

export interface NotAvailableProps {
  label: string;
  title: string;
  body: string;
  action?: { href: string; label: string };
  headingLevel?: "h1" | "h2";
}

/**
 * The one "not available" state. A resource that does not exist and one the viewer may
 * not read render identically (SEC-5): same copy, same layout, same timing path.
 */
export function NotAvailable({ label, title, body, action, headingLevel: H = "h1" }: NotAvailableProps) {
  return (
    <div className={styles.state}>
      <p className={styles.label}>
        <span className={styles.icon}>
          <Icon as={EyeOff} />
        </span>
        {label}
      </p>
      <H className={styles.title}>{title}</H>
      <p className={styles.body}>{body}</p>
      {action && (
        <div className={styles.actions}>
          <ButtonLink href={action.href}>{action.label}</ButtonLink>
        </div>
      )}
    </div>
  );
}
