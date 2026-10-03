"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";

import { useEvidence } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { Button } from "@/components/primitives/Button";
import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { Icon } from "@/components/primitives/icons";
import { Kbd } from "@/components/primitives/Kbd";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import type { RenderClaim } from "@/lib/answer-model";
import { isUuid } from "@/lib/format";
import type { InspectorContext } from "@/lib/workspace-state";

import styles from "./EvidenceInspector.module.css";
import { ExhibitViewer } from "./ExhibitViewer";
import { SourceTypeTag } from "./SourceTypeTag";

export interface EvidenceInspectorProps {
  evidenceId: string;
  context: InspectorContext | null;
  onClose: () => void;
  onNavigate: (evidenceId: string) => void;
  /** Take the reader to a claim of the answer that cites this exhibit. */
  onShowClaim?: (claim: RenderClaim) => void;
  /** Move focus into the panel when the evidence changes. */
  focusOnOpen: boolean;
}

/**
 * The evidence inspector: the exhibit viewer in a panel, with its number, previous / next
 * through the answer's exhibits ([ and ]) and close (Esc). Content comes only from GET
 * /v1/evidence/{id}, fetched when opened and never kept after it closes. A 404 --
 * nonexistent or unreadable, the API does not say which -- has one presentation.
 */
export function EvidenceInspector({ evidenceId, context, onClose, onNavigate, onShowClaim, focusOnOpen }: EvidenceInspectorProps) {
  const valid = isUuid(evidenceId);
  const query = useEvidence(valid ? evidenceId : null);
  const panel = useRef<HTMLElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const headingId = useId();
  const number = context?.numberOf.get(evidenceId);
  const order = context?.order ?? [];
  const at = order.indexOf(evidenceId);
  const prev = at > 0 ? order[at - 1] : undefined;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : undefined;

  useEffect(() => {
    if (focusOnOpen) panel.current?.focus({ preventScroll: true });
  }, [evidenceId, focusOnOpen]);

  // Bring the first verified quote into view inside the panel (never scrolls the page).
  useEffect(() => {
    const box = scroller.current;
    if (!box || !query.data) return;
    box.scrollTop = 0;
    const mark = box.querySelector("mark");
    if (!mark) return;
    const offset = mark.getBoundingClientRect().top - box.getBoundingClientRect().top;
    if (offset > box.clientHeight * 0.6) box.scrollTop = offset - box.clientHeight / 3;
  }, [query.data]);

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
          {number !== undefined && <ExhibitMarker n={number} size="lg" state="selected" label={`Exhibit ${number}`} />}
          {query.data && !notFound ? <SourceTypeTag sourceType={query.data.source_type} /> : <span>Evidence</span>}
          {order.length > 1 && at >= 0 && (
            <span className={styles.position}>
              {at + 1} of {order.length}
            </span>
          )}
        </p>
        <div className={styles.tools}>
          {order.length > 1 && (
            <>
              <Button variant="quiet" size="sm" icon={<Icon as={ChevronLeft} />} aria-label="Previous evidence" aria-keyshortcuts="[" disabled={!prev} onClick={() => prev && onNavigate(prev)} />
              <Button variant="quiet" size="sm" icon={<Icon as={ChevronRight} />} aria-label="Next evidence" aria-keyshortcuts="]" disabled={!next} onClick={() => next && onNavigate(next)} />
            </>
          )}
          <Button variant="quiet" size="sm" icon={<Icon as={X} />} aria-label="Close evidence" aria-keyshortcuts="Escape" onClick={onClose} />
        </div>
      </div>

      <div ref={scroller} className={styles.scroll} aria-busy={query.isPending && valid ? true : undefined}>
        {notFound ? (
          <NotAvailable headingLevel="h2" label="Not available" title="This evidence isn't available" body="It doesn't exist, or you don't have access to it. Lattice doesn't say which." />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Couldn't load this evidence" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : query.data ? (
          <>
            <ExhibitViewer detail={query.data} context={context} headingId={headingId} onOpen={onNavigate} onShowClaim={onShowClaim} />
            <p className={styles.keys} aria-hidden="true">
              {order.length > 1 && (
                <>
                  <Kbd>[</Kbd> previous · <Kbd>]</Kbd> next ·{" "}
                </>
              )}
              <Kbd>Esc</Kbd> close
            </p>
          </>
        ) : (
          <div className={styles.loading}>
            <RuleSkeleton label="Loading evidence" rows={4} />
          </div>
        )}
      </div>
    </section>
  );
}
