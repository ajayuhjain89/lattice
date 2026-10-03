"use client";

import { useId } from "react";

import { useEvidence } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { RecordPage, record } from "@/components/record/RecordPage";
import { PageTrail } from "@/components/shell/PageTrail";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { isUuid } from "@/lib/format";

import styles from "./EvidenceScreen.module.css";
import { ExhibitViewer } from "./ExhibitViewer";
import { SourceTypeTag } from "./SourceTypeTag";

/**
 * A shared evidence link, in the inspector's grammar: where it came from (the provenance
 * chain), what it is (title), the passage, and its details. Everything comes from GET
 * /v1/evidence/{id}; a 404 (does not exist, or not readable by this viewer) and a
 * malformed ID look exactly the same, and nothing on the page echoes the requested ID.
 */
export function EvidenceScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  const query = useEvidence(valid ? id : null);
  const headingId = useId();
  const notFound = !valid || (query.isError && isApiError(query.error) && (query.error.isNotFound || query.error.status === 422));

  return (
    <RecordPage>
      <div className={`${record.text} ${styles.column}`}>
        <PageTrail trail={[{ label: "Ask", href: "/ask" }, { label: "Evidence" }]} />
        {notFound ? (
          <NotAvailable
            label="Not available"
            title="This evidence isn't available"
            body="It doesn't exist, or you don't have access to it. Lattice doesn't say which."
            action={{ href: "/ask", label: "Back to Ask" }}
          />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Couldn't load this evidence" headingLevel="h1" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : query.data ? (
          <div className={styles.sheet}>
            <p className={styles.eyebrow}>
              <SourceTypeTag sourceType={query.data.source_type} />
              <span>Evidence</span>
            </p>
            <ExhibitViewer detail={query.data} context={null} headingId={headingId} headingLevel="h1" />
          </div>
        ) : (
          <div className={styles.sheet}>
            <RuleSkeleton label="Loading evidence" />
          </div>
        )}
      </div>
    </RecordPage>
  );
}
