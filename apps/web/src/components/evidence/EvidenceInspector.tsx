"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";

import { useEvidence } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SkeletonBlock, SkeletonLines } from "@/components/primitives/Skeleton";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { isUuid } from "@/lib/format";
import type { InspectorContext } from "@/lib/workspace-state";

import { EvidenceBody } from "./EvidenceBody";
import styles from "./EvidenceInspector.module.css";
import { SourceTypeTag } from "./SourceTypeTag";

export interface EvidenceInspectorProps {
  evidenceId: string;
  context: InspectorContext | null;
  onClose: () => void;
  onNavigate: (evidenceId: string) => void;
  /** Move focus into the panel when the evidence changes (docked mode). */
  focusOnOpen: boolean;
}

/**
 * The evidence inspector. Content comes only from GET /v1/evidence/{id}, fetched when
 * opened and never kept after it closes. Loading has one geometry; a 404 -- nonexistent
 * or unreadable, the API does not say which -- has one presentation.
 */
export function EvidenceInspector({ evidenceId, context, onClose, onNavigate, focusOnOpen }: EvidenceInspectorProps) {
  const valid = isUuid(evidenceId);
  const query = useEvidence(valid ? evidenceId : null);
  const panel = useRef<HTMLElement>(null);
  const headingId = useId();
  const number = context?.numberOf.get(evidenceId);
  const order = context?.order ?? [];
  const at = order.indexOf(evidenceId);
  const prev = at > 0 ? order[at - 1] : undefined;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : undefined;

  useEffect(() => {
    if (focusOnOpen) panel.current?.focus({ preventScroll: true });
  }, [evidenceId, focusOnOpen]);

  // Escape closes; [ and ] step through the answer's evidence (while focus is inside).
  // Escape always closes, even when a tooltip layer has also handled it.
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable='true']")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.defaultPrevented) return;
    if (event.key === "[" && prev) {
      event.preventDefault();
      onNavigate(prev);
    } else if (event.key === "]" && next) {
      event.preventDefault();
      onNavigate(next);
    }
  };

  const notFound = !valid || (query.isError && isApiError(query.error) && (query.error.isNotFound || query.error.status === 422));

  return (
    // Keyboard shortcuts of a focusable panel; every control inside is a real button.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <section
      ref={panel}
      className={styles.panel}
      tabIndex={-1}
      aria-labelledby={query.data ? headingId : `${headingId}-label`}
      data-testid="evidence-inspector"
      onKeyDown={onKeyDown}
    >
      <div className={styles.header}>
        <p className={styles.headerLabel} id={`${headingId}-label`}>
          {number !== undefined && (
            <span className={styles.number} aria-label={`Citation ${number}`}>
              {number}
            </span>
          )}
          {query.data ? <SourceTypeTag sourceType={query.data.source_type} /> : <span>Evidence</span>}
        </p>
        <div className={styles.tools}>
          {order.length > 1 && (
            <>
              <Button variant="ghost" size="sm" icon={<Icon as={ChevronLeft} />} aria-label="Previous evidence" aria-keyshortcuts="[" disabled={!prev} onClick={() => prev && onNavigate(prev)} />
              <Button variant="ghost" size="sm" icon={<Icon as={ChevronRight} />} aria-label="Next evidence" aria-keyshortcuts="]" disabled={!next} onClick={() => next && onNavigate(next)} />
            </>
          )}
          <Button variant="ghost" size="sm" icon={<Icon as={X} />} aria-label="Close evidence" aria-keyshortcuts="Escape" onClick={onClose} />
        </div>
      </div>

      <div className={styles.scroll} aria-busy={query.isPending && valid ? true : undefined}>
        {notFound ? (
          <NotAvailable
            headingLevel="h2"
            label="Not available"
            title="This evidence isn't available"
            body="It doesn't exist, or you don't have access to it. Lattice doesn't say which."
          />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Couldn't load this evidence" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : query.data ? (
          <>
            <EvidenceBody detail={query.data} context={context} headingId={headingId} onOpen={onNavigate} />
            <p className={styles.keys} aria-hidden="true">
              {order.length > 1 && (
                <>
                  <kbd>[</kbd> previous · <kbd>]</kbd> next ·{" "}
                </>
              )}
              <kbd>Esc</kbd> close
            </p>
          </>
        ) : (
          <div className={styles.loading}>
            <span className="visually-hidden" role="status">
              Loading evidence
            </span>
            <SkeletonLines widths={["88%", "54%"]} />
            <SkeletonBlock height={196} />
            <SkeletonLines widths={["100%", "96%", "92%", "98%", "64%"]} />
          </div>
        )}
      </div>
    </section>
  );
}
