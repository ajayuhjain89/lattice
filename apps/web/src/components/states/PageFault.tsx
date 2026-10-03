"use client";

import { RotateCw } from "lucide-react";
import { useEffect, useRef } from "react";

import { Button, ButtonLink } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";

/**
 * The page failed to render (an error boundary caught it): the fault family, with nothing
 * from the error itself -- no message, no stack. The only identifier shown is Next.js's
 * digest, an opaque hash it creates for exactly this (a server-side failure, logged on the
 * server under the same value); a failure in the browser has none, and none is invented.
 * Focus moves to the state, which replaced whatever had it. *Try again* reloads the page:
 * the failure may come from data already in memory, which re-rendering alone would reuse.
 */
export function PageFault({
  digest,
  onRetry,
  home,
  headingLevel = "h1",
}: {
  digest?: string;
  onRetry: () => void;
  /** Where to start again: the link's label and the place it names in the sentence. */
  home: { href: string; label: string; place: string };
  headingLevel?: "h1" | "h2";
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.focus(), []);
  return (
    <div ref={box} tabIndex={-1} data-testid="page-fault">
      {/* React hoists this into <head>: the tab says what happened too. */}
      <title>This page couldn&apos;t be shown · Lattice</title>
      <StateBlock
        family="fault"
        role="alert"
        heading="This page couldn't be shown"
        headingLevel={headingLevel}
        reference={digest ?? null}
        actions={
          <>
            <Button icon={<Icon as={RotateCw} />} onClick={onRetry}>
              Try again
            </Button>
            <ButtonLink href={home.href} variant="quiet" prefetch={false}>
              {home.label}
            </ButtonLink>
          </>
        }
      >
        <p>Something went wrong in Lattice while showing it. Try again; if it keeps happening, start again from {home.place}.</p>
      </StateBlock>
    </div>
  );
}
