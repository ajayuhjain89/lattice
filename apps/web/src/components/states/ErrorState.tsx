import { AlertTriangle, RotateCw } from "lucide-react";

import type { ApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";

import styles from "./States.module.css";

/**
 * A failure the person can act on: the API's fixed message (never internals), its request
 * reference for support, and a retry.
 */
export function ErrorState({
  error,
  title = "Couldn't load this",
  onRetry,
  retrying = false,
  headingLevel: H = "h2",
}: {
  error: ApiError | Error | null;
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
  headingLevel?: "h1" | "h2" | "h3";
}) {
  const requestId = error && "requestId" in error ? error.requestId : null;
  return (
    <div className={styles.state} role="alert">
      <p className={`${styles.label} ${styles.errorLabel}`}>
        <span className={styles.icon}>
          <Icon as={AlertTriangle} />
        </span>
        Error
      </p>
      <H className={styles.title}>{title}</H>
      <p className={styles.body}>{error?.message || "Something went wrong. Try again."}</p>
      {requestId && <p className={styles.reference}>Reference {requestId}</p>}
      {onRetry && (
        <div className={styles.actions}>
          <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={retrying}>
            Try again
          </Button>
        </div>
      )}
    </div>
  );
}
