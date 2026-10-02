import { RotateCw } from "lucide-react";

import type { ApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";

/**
 * A failure the person can act on: the API's fixed message (never internals), its request
 * reference for support, and a retry. The fault family of StateBlock.
 */
export function ErrorState({
  error,
  title = "Couldn't load this",
  onRetry,
  retrying = false,
  headingLevel = "h2",
}: {
  error: ApiError | Error | null;
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
  headingLevel?: "h1" | "h2" | "h3";
}) {
  const requestId = error && "requestId" in error ? error.requestId : null;
  return (
    <StateBlock
      family="fault"
      role="alert"
      heading={title}
      headingLevel={headingLevel}
      reference={requestId}
      actions={
        onRetry && (
          <Button icon={<Icon as={RotateCw} />} onClick={onRetry} loading={retrying}>
            Try again
          </Button>
        )
      }
    >
      <p>{error?.message || "Something went wrong. Try again."}</p>
    </StateBlock>
  );
}
