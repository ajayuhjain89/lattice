import { GitFork } from "lucide-react";

import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { ProvenanceTrail } from "@/components/primitives/ProvenanceTrail";

import { CLAIMS, CONFLICT_TOPIC, EXAMPLE_LABEL, EXHIBITS, QUESTION, STATUS_FACTS, type ExampleExhibit } from "./example";
import styles from "./Specimen.module.css";

/**
 * The hero's product specimen: a brief as Ask renders it -- question, ledger line, claims
 * with their exhibit markers in the margin, the disagreement note, and the exhibit list
 * with the first exhibit open at its verified passage. Static and server-rendered from the
 * production primitives and type roles; it reads the same with script off.
 */
export function HeroBrief() {
  const open = EXHIBITS.decision;
  const rest: ExampleExhibit[] = [EXHIBITS.ticket, EXHIBITS.kickoff];
  return (
    <figure className={styles.hero} aria-labelledby="hero-example">
      <figcaption id="hero-example" className={styles.label}>
        {EXAMPLE_LABEL}
        <span className="visually-hidden">: a brief as Lattice renders one</span>
      </figcaption>
      <div className={styles.sheet}>
        <p className={styles.question}>
          <span className={styles.questionLabel}>Question</span>
          <span className={styles.questionText}>{QUESTION}</span>
        </p>
        <p className={styles.status}>
          <span className={styles.keyword}>Answered</span>
          {STATUS_FACTS.map((f) => (
            <span key={f} className={styles.fact}>
              {f}
            </span>
          ))}
        </p>
        <ol className={styles.claims} aria-label="Claims">
          {CLAIMS.map((c) => (
            <li key={c.key} className={styles.claimRow} data-role={c.role}>
              <p className={c.role === "lead" ? styles.leadText : styles.supportText}>
                <span className={styles.claimWords} data-open={c.cites.includes(open.n) || undefined}>
                  {c.text}
                </span>
                <span className="visually-hidden"> (exhibit {c.cites.join(", ")})</span>
              </p>
              <span className={styles.claimMargin}>
                {c.cites.map((n) => (
                  <ExhibitMarker key={n} n={n} state={n === open.n ? "selected" : "rest"} />
                ))}
              </span>
            </li>
          ))}
        </ol>
        <div className={styles.inset}>
          <NoticeLine tone="disagreement" icon={<Icon as={GitFork} />}>
            Potential conflict · {CONFLICT_TOPIC}. Lattice can&apos;t confirm which is current.
          </NoticeLine>
        </div>
        <div className={styles.exhibits}>
          <p className={styles.exhibitsHead}>Exhibits</p>
          <ol className={styles.exhibitList}>
            <li className={styles.exhibitOpen}>
              <span className={styles.exhibitNumber}>
                <ExhibitMarker n={open.n} state="selected" />
              </span>
              <div className={styles.exhibitBody}>
                <p className={styles.exhibitTitle}>{open.title}</p>
                <ProvenanceTrail
                  label="Exhibit 1 provenance"
                  segments={[
                    { key: "source", kind: "Source", label: open.source },
                    { key: "authority", kind: "Authority", label: open.authority },
                    { key: "version", label: `Version ${open.version}` },
                  ]}
                  verified="Quote verified"
                />
                <blockquote className={styles.passage}>
                  <p>
                    {open.passage.before}
                    <mark>{open.passage.quote}</mark>
                    {open.passage.after}
                  </p>
                </blockquote>
              </div>
            </li>
            {rest.map((e) => (
              <li key={e.n} className={styles.exhibitRow}>
                <span className={styles.exhibitNumber}>
                  <ExhibitMarker n={e.n} />
                </span>
                <p className={styles.exhibitLine}>
                  <span className={styles.exhibitName}>{e.ref ? `${e.ref} · ${e.title}` : e.title}</span>
                  <span className={styles.exhibitMeta}>
                    {e.source} · <time dateTime={e.at}>{e.date}</time>
                  </span>
                </p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </figure>
  );
}
