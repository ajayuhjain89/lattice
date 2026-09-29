"use client";

import { useId } from "react";

import { useEvidence } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { SkeletonBlock, SkeletonLines } from "@/components/primitives/Skeleton";
import { PageHeader } from "@/components/shell/PageHeader";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { isUuid, shortId } from "@/lib/format";

import { EvidenceBody } from "./EvidenceBody";
import styles from "./EvidenceScreen.module.css";
import { SourceTypeTag } from "./SourceTypeTag";

/**
 * A shared evidence link. Everything comes from GET /v1/evidence/{id}; a 404 (does not
 * exist, or not readable by this viewer) and a malformed ID look exactly the same.
 */
export function EvidenceScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  const query = useEvidence(valid ? id : null);
  const headingId = useId();
  const notFound = !valid || (query.isError && isApiError(query.error) && (query.error.isNotFound || query.error.status === 422));

  return (
    <>
      <PageHeader trail={[{ label: "Ask", href: "/ask" }, { label: `Evidence ${valid ? shortId(id) : ""}`.trim() }]} />
      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.column}>
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
            <>
              <p className={styles.eyebrow}>
                <SourceTypeTag sourceType={query.data.source_type} />
                <span className={styles.eyebrowLabel}>Evidence</span>
              </p>
              <h1 className="visually-hidden">Evidence: {query.data.title}</h1>
              <EvidenceBody detail={query.data} context={null} headingId={headingId} fullTitle />
            </>
          ) : (
            <div aria-busy="true">
              <span className="visually-hidden" role="status">
                Loading evidence
              </span>
              <SkeletonLines widths={["24%", "70%", "40%"]} />
              <SkeletonBlock height={220} />
            </div>
          )}
        </div>
      </main>
    </>
  );
}
