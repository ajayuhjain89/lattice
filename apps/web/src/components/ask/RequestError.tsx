"use client";

import { AlertTriangle, Hourglass, RotateCw, WifiOff } from "lucide-react";

import { retryPhrase, type ApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";

import styles from "./States.module.css";

/**
 * The Ask request itself failed (no Answer). The question stays in the composer. Copy is
 * the API's fixed message or our own; the request reference helps support, nothing else
 * about the failure is shown.
 */
export function RequestError({
  error,
  onRetry,
  onClearAccount,
  pending,
}: {
  error: ApiError;
  onRetry: () => void;
  onClearAccount?: () => void;
  pending: boolean;
}) {
  const network = error.kind === "network";
  const invalid = error.status === 422;
  const limited = error.isRateLimited;
  return (
    <Callout
      tone="fault"
      label={network ? "Connection" : limited ? "Limit" : "Error"}
      icon={<Icon as={network ? WifiOff : limited ? Hourglass : AlertTriangle} />}
      role="alert"
      aria-labelledby="request-error-heading"
    >
      <h2 className={styles.heading} id="request-error-heading">
        {network
          ? "Couldn't reach Lattice"
          : limited
            ? "Too many questions in a row"
            : invalid
              ? "Lattice couldn't run this question"
              : "The question couldn't be answered"}
      </h2>
      <p className={styles.calloutMessage}>
        {network
          ? "Check your connection and try again. Your question is still in the box above."
          : limited
            ? `Lattice limits how often each person can ask. Try again ${retryPhrase(error.retryAfter)}; your question is still in the box above.`
            : invalid && onClearAccount
              ? "The account filter can't be used. Remove it and ask again."
              : error.message}
      </p>
      <div className={styles.calloutActions}>
        {invalid && onClearAccount ? (
          <Button onClick={onClearAccount}>Remove account filter</Button>
        ) : !invalid ? (
          <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={pending}>
            Try again
          </Button>
        ) : null}
        {error.requestId && <span className={styles.reference}>Reference {error.requestId}</span>}
      </div>
    </Callout>
  );
}
