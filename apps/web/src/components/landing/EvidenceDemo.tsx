"use client";

import { useState } from "react";

import { ExhibitButton, ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { ProvenanceTrail } from "@/components/primitives/ProvenanceTrail";

import { CLAIMS, EXAMPLE_LABEL, EXHIBITS, type ExampleExhibit } from "./example";
import styles from "./Specimen.module.css";

const BY_NUMBER = new Map<number, ExampleExhibit>([EXHIBITS.decision, EXHIBITS.ticket, EXHIBITS.kickoff].map((e) => [e.n, e]));

/**
 * Claim → exhibit → verified passage, the Ask relationship on its own. Each claim carries
 * its exhibit markers (the production ExhibitButton: aria-pressed, named); choosing one
 * shows that exhibit's provenance trail and the passage the claim quotes, marked. Hovering
 * or focusing a claim's marker links it to the open exhibit when it is that one.
 *
 * Server-rendered with exhibit 2 open (the hero already shows exhibit 1), so the relationship is complete before (or
 * without) script; the only client state is which exhibit is open.
 */
export function EvidenceDemo() {
  const [open, setOpen] = useState(2);
  const [linked, setLinked] = useState<number | null>(null);
  const exhibit = BY_NUMBER.get(open)!;

  return (
    <figure className={styles.evidence} aria-labelledby="evidence-example">
      <figcaption id="evidence-example" className={styles.label}>
        {EXAMPLE_LABEL}
        <span className="visually-hidden">: claims and the exhibit behind each</span>
      </figcaption>
      <div className={styles.evidenceGrid}>
        <ol className={styles.evidenceClaims} aria-label="Claims">
          {CLAIMS.map((c) => {
            const isOpen = c.cites.includes(open);
            return (
              <li key={c.key} className={styles.evidenceClaim} data-open={isOpen || undefined}>
                <p className={c.role === "lead" ? styles.leadText : styles.supportText}>
                  <span className={styles.claimWords} data-open={isOpen || undefined} data-lit={(linked !== null && c.cites.includes(linked)) || undefined}>
                    {c.text}
                  </span>{" "}
                  {c.cites.map((n) => {
                    const e = BY_NUMBER.get(n)!;
                    return (
                      <ExhibitButton
                        key={n}
                        n={n}
                        selected={open === n}
                        linked={linked === n && open !== n}
                        aria-label={`Exhibit ${n}: ${e.ref ?? e.source}, ${e.date}`}
                        aria-controls="evidence-exhibit"
                        onClick={() => setOpen(n)}
                        onMouseEnter={() => setLinked(n)}
                        onMouseLeave={() => setLinked(null)}
                        onFocus={() => setLinked(n)}
                        onBlur={() => setLinked(null)}
                      />
                    );
                  })}
                </p>
              </li>
            );
          })}
        </ol>
        <section id="evidence-exhibit" className={styles.viewer} aria-label={`Exhibit ${exhibit.n}`}>
          <div className={styles.viewerHead}>
            <ExhibitMarker n={exhibit.n} size="lg" state="selected" />
            <p className={styles.viewerTitle}>{exhibit.ref ? `${exhibit.ref} · ${exhibit.title}` : exhibit.title}</p>
          </div>
          <ProvenanceTrail
            label={`Exhibit ${exhibit.n} provenance`}
            segments={[
              { key: "source", kind: "Source", label: exhibit.source },
              { key: "kind", kind: "Kind", label: `${exhibit.kind} · ${exhibit.authority}` },
              { key: "version", label: `Version ${exhibit.version}` },
            ]}
            verified="Quote verified"
          />
          <blockquote key={exhibit.n} className={`${styles.passage} ${styles.viewerPassage}`}>
            <p>
              {exhibit.passage.before}
              <mark className={styles.sweep}>{exhibit.passage.quote}</mark>
              {exhibit.passage.after}
            </p>
          </blockquote>
          <p className={styles.viewerMeta}>
            {exhibit.author} · <time dateTime={exhibit.at}>{exhibit.date}</time> · {exhibit.sensitivity}
          </p>
        </section>
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {`Showing exhibit ${exhibit.n}: ${exhibit.title}`}
      </p>
    </figure>
  );
}
