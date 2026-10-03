import { ArrowRight, GitFork, Lock } from "lucide-react";
import type { ReactNode } from "react";

import { Wordmark } from "@/components/brand/Wordmark";
import { PublicMasthead, type PublicLink } from "@/components/public/PublicMasthead";
import { BoundaryEdge } from "@/components/primitives/Boundary";
import { ButtonLink } from "@/components/primitives/Button";
import { DisagreementAxis } from "@/components/primitives/DisagreementAxis";
import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { EgressGlyph } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { LevelMeter } from "@/components/primitives/LevelMeter";
import { NoticeLine } from "@/components/primitives/NoticeLine";

import { EgressExample } from "./EgressExample";
import { EvidenceDemo } from "./EvidenceDemo";
import { CONFLICT_POSITIONS, CONFLICT_SUMMARY, CONFLICT_TOPIC, ESCALATION, EXAMPLE_LABEL, EXHIBITS, READERS } from "./example";
import { HeroBrief } from "./HeroBrief";
import styles from "./Landing.module.css";
import specimen from "./Specimen.module.css";

const SECTIONS: readonly PublicLink[] = [
  { href: "#how", label: "How it works" },
  { href: "#boundaries", label: "Boundaries" },
  { href: "#administrators", label: "For administrators" },
];

const arrow = <Icon as={ArrowRight} />;

/**
 * The public landing page (design-direction §6.1). Static: no API call, no session probe,
 * no organization but the labelled synthetic example. It explains Lattice with the
 * product's own grammar -- the brief, exhibit markers, provenance trails, the
 * disagreement axis, boundary hatching, the egress lattice and the access ledger -- in
 * the order an answer is made. Every section reads without hover or script; the evidence
 * specimen is the one interactive island.
 */
export function Landing() {
  return (
    <div className={styles.page}>
      <PublicMasthead
        links={SECTIONS}
        action={
          <ButtonLink href="/sign-in" size="sm" variant="secondary" prefetch={false}>
            Sign in
          </ButtonLink>
        }
      />
      <main id="main" tabIndex={-1} className={styles.main}>
        <Hero />
        <HowItWorks />
        <Evidence />
        <Disagreement />
        <Boundaries />
        <Administrators />
        <Closing />
      </main>
      <Footer />
    </div>
  );
}

function Hero() {
  return (
    <section className={styles.hero} aria-labelledby="hero-title">
      <div className={styles.heroText}>
        <h1 id="hero-title" className={styles.heroTitle}>
          Answers you can follow back to the page.
        </h1>
        <p className={styles.heroDeck}>
          Ask what your organization knows. Lattice searches only what you&apos;re allowed to read, and ties every statement to the passage it
          came from.
        </p>
        <div className={styles.heroActions}>
          <ButtonLink href="/sign-in" variant="primary" size="lg" iconEnd={arrow} prefetch={false}>
            Sign in
          </ButtonLink>
          <a href="#how" className={styles.textLink}>
            How an answer is made
          </a>
        </div>
        <p className={styles.heroNote}>Sign-in is through your organization&apos;s identity provider.</p>
      </div>
      <div className={styles.heroSpecimen}>
        <HeroBrief />
      </div>
      <ul className={styles.key} aria-label="How to read a brief">
        <li>
          <span className={styles.keyMark} aria-hidden="true">
            <ExhibitMarker n={1} />
          </span>
          An exhibit: the source a claim rests on
        </li>
        <li>
          <span className={styles.keyMark} aria-hidden="true">
            <mark className={styles.keyPassage}>Aa</mark>
          </span>
          The verified passage, word for word
        </li>
        <li>
          <span className={`${styles.keyMark} ${styles.keyConflict}`} aria-hidden="true">
            <Icon as={GitFork} />
          </span>
          A disagreement, shown and not resolved
        </li>
      </ul>
    </section>
  );
}

function SectionHead({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <div className={styles.head}>
      <h2 id={id} className={styles.title}>
        {title}
      </h2>
      <div className={styles.deck}>{children}</div>
    </div>
  );
}

const STOPS = [
  { name: "Your access", body: "Who you are in your organization: clearance, accounts, departments, groups and direct grants." },
  { name: "Search inside it", body: "Permissions are part of the search itself. A document you can't read is never retrieved." },
  {
    name: "AI data policy",
    body: "Your organization decides which sensitivity levels may reach which model. What it holds back stays readable to you.",
    boundary: true,
  },
  { name: "Draft", body: "The model writes claims from the passages it was given, citing each one." },
  { name: "Verify every quote", body: "Each quote is checked word for word against its source. A claim that fails is removed, and the brief says so." },
] as const;

function HowItWorks() {
  return (
    <section id="how" className={`${styles.section} ${styles.wide}`} aria-labelledby="how-title">
      <SectionHead id="how-title" title="It searches only what you can read">
        <p>
          Authorization comes first. Lattice applies your permissions before anything is retrieved, so the model never sees a document you
          can&apos;t open — and an answer can&apos;t hint that one exists.
        </p>
      </SectionHead>
      <ol className={specimen.path} aria-label="How an answer is made">
        {STOPS.map((s, i) => (
          <li key={s.name} className={specimen.stop} data-boundary={"boundary" in s || undefined}>
            <span className={specimen.stopMark}>
              <ExhibitMarker n={i + 1} size="lg" />
            </span>
            <span className={specimen.stopName}>{s.name}</span>
            <span className={specimen.stopBody}>{s.body}</span>
          </li>
        ))}
      </ol>
      <p className={styles.after}>
        Every answer keeps these steps as its <strong>trace</strong>: what was searched, what the policy held back and which quotes were
        checked. You can open it from the brief.
      </p>

      <div className={styles.compare}>
        <h3 className={styles.subTitle}>Same question, two readers</h3>
        <p className={styles.subDeck}>
          Both ask about Acme&apos;s Directory Sync date. Each gets evidence from their own access — nothing more, and no sign of anything
          else.
        </p>
        <div>
          <p className={specimen.label}>{EXAMPLE_LABEL}</p>
          <div className={specimen.readers}>
            <Reader who={READERS.maya} />
            <Reader who={READERS.grace} withheld />
          </div>
        </div>
        <p className={specimen.readersNote}>
          Grace can read the executive escalation notes, so Lattice finds them — and, because Halden&apos;s AI data policy keeps Confidential
          content from external models, shows them to her without sending them to the model. Maya can&apos;t read them, so for her they
          don&apos;t exist: no gap, no count, no hint.
        </p>
      </div>
    </section>
  );
}

function Reader({ who, withheld = false }: { who: (typeof READERS)[keyof typeof READERS]; withheld?: boolean }) {
  const found = [EXHIBITS.decision, EXHIBITS.ticket, EXHIBITS.kickoff];
  return (
    <section className={specimen.reader} aria-label={`Evidence found for ${who.name}`}>
      <div className={specimen.readerHead}>
        <p className={specimen.readerName}>
          {who.name} <span className={specimen.readerRole}>{who.role}</span>
        </p>
        <p className={specimen.readerFacts}>{who.facts.join(" · ")}</p>
      </div>
      <ol className={specimen.found} aria-label="Exhibits">
        {found.map((e) => (
          <li key={e.n} className={specimen.foundRow}>
            <ExhibitMarker n={e.n} />
            <span className={specimen.foundTitle}>{e.ref ?? e.title}</span>
            <span className={specimen.foundMeta}>{e.date}</span>
          </li>
        ))}
      </ol>
      {withheld && (
        <BoundaryEdge className={specimen.withheld}>
          <p className={specimen.withheldHead}>
            <Icon as={EgressGlyph} />
            Not sent to the AI model <span className={specimen.withheldWhy}>· AI data policy · you can read it</span>
          </p>
          <p className={specimen.foundRow}>
            <ExhibitMarker n={null} />
            <span className={specimen.foundTitle}>
              {ESCALATION.title}{" "}
              <span className={specimen.sensitivity}>
                <Icon as={Lock} size={12} />
                {ESCALATION.sensitivity}
              </span>
            </span>
            <span className={specimen.foundMeta}>{ESCALATION.date}</span>
          </p>
        </BoundaryEdge>
      )}
    </section>
  );
}

function Evidence() {
  return (
    <section id="evidence" className={`${styles.section} ${styles.wide}`} aria-labelledby="evidence-title">
      <SectionHead id="evidence-title" title="Every claim keeps its evidence">
        <p>
          Each statement carries numbered exhibits. Follow one and you reach the exact passage it rests on, checked word for word against
          the source — not a link to a whole document, and not a citation added afterwards.
        </p>
        <p className={styles.hint}>Choose an exhibit number to open its passage.</p>
      </SectionHead>
      <div className={styles.body}>
        <EvidenceDemo />
      </div>
    </section>
  );
}

function Disagreement() {
  return (
    <section id="disagreement" className={`${styles.section} ${styles.wide}`} aria-labelledby="disagreement-title">
      <SectionHead id="disagreement-title" title="Disagreement stays on the page">
        <p>
          When sources conflict, Lattice sets their positions out by date, with equal weight, and names what kind of source each one is. It
          doesn&apos;t pick a winner: newer isn&apos;t the same as right.
        </p>
      </SectionHead>
      <figure className={specimen.axisFigure} aria-labelledby="disagreement-example">
        <figcaption id="disagreement-example" className={specimen.label}>
          {EXAMPLE_LABEL}
        </figcaption>
        <div className={specimen.axisSheet}>
          <DisagreementAxis
            topic={CONFLICT_TOPIC}
            positions={CONFLICT_POSITIONS.map(({ exhibit: e, claim }) => ({
              key: String(e.n),
              at: e.at,
              dateLabel: e.date,
              source: `${e.kind} · ${e.authority}`,
              title: e.ref ? `${e.ref} · ${e.source}` : e.source,
              children: (
                <>
                  {claim} <ExhibitMarker n={e.n} label={`Exhibit ${e.n}`} />
                </>
              ),
            }))}
            conclusion={CONFLICT_SUMMARY}
          />
        </div>
      </figure>
      <p className={styles.after}>
        Here the newest position is an unverified chat message, and the approved decision record is a week older. Lattice shows both with
        their sources and leaves the judgement to you.
      </p>
    </section>
  );
}

function Boundaries() {
  return (
    <section id="boundaries" className={`${styles.section} ${styles.split}`} aria-labelledby="boundaries-title">
      <SectionHead id="boundaries-title" title="Boundaries are drawn, not hidden">
        <p>
          Authorization decides what you can read. A separate AI data policy decides what may be sent to a model. Where the policy stops
          something, Lattice draws the edge and says why.
        </p>
        <p>
          By default only Internal content may go to an approved external model. Confidential and Restricted content is never sent
          externally unless your organization explicitly opts in, and a model on your own infrastructure receives nothing until an
          administrator sets its ceiling and approves it.
        </p>
      </SectionHead>
      <div className={`${styles.body} ${specimen.boundaries}`}>
        <figure className={specimen.egressFigure} aria-labelledby="egress-example">
          <figcaption id="egress-example" className={specimen.label}>
            Example AI data policy · synthetic company Halden
          </figcaption>
          <div className={specimen.egressSheet}>
            <EgressExample />
          </div>
        </figure>
        <div className={specimen.notice}>
          <p className={specimen.noticeLabel}>What Grace sees in her brief</p>
          <NoticeLine tone="boundary" icon={<Icon as={EgressGlyph} />}>
            1 source you can access wasn&apos;t sent to the AI model because of your organization&apos;s AI data policy. Review them below.
          </NoticeLine>
          <p className={specimen.noticeNote}>
            This policy is about models, not people: each person still sees only what they may read. Hatching marks an edge around something
            you&apos;re entitled to see. It never stands in for something you can&apos;t — that leaves no trace at all.
          </p>
        </div>
      </div>
    </section>
  );
}

function Administrators() {
  return (
    <section id="administrators" className={`${styles.section} ${styles.split}`} aria-labelledby="administrators-title">
      <SectionHead id="administrators-title" title="Access you can explain">
        <p>
          Administrators see why a member can read what they can, preview every change as Now → After before it applies, and leave an audit
          entry that can&apos;t be edited or deleted.
        </p>
        <p>Managing access gives them no access to content: questions, answers and evidence aren&apos;t visible to administrators.</p>
      </SectionHead>
      <figure className={`${styles.body} ${specimen.access}`} aria-labelledby="access-example">
        <figcaption id="access-example" className={specimen.label}>
          {EXAMPLE_LABEL}
        </figcaption>
        <div className={specimen.accessSheet}>
          <div className={specimen.member}>
            <p className={specimen.memberName}>
              Maya Okafor <span className={specimen.readerRole}>Account Manager</span>
            </p>
            <p className={specimen.memberEmail}>maya.okafor@halden.example</p>
          </div>
          <p className={specimen.accessSentence}>
            <strong>Maya Okafor</strong> can see <strong>Acme Corp</strong> and <strong>Northpeak Logistics</strong> content (assigned
            accounts), up to <strong>Internal</strong> sensitivity, including content restricted to <strong>Sales</strong>.
          </p>
          <dl className={specimen.ledger} aria-label="How this access is made up">
            <div className={specimen.ledgerRow}>
              <dt>Clearance</dt>
              <dd>
                <LevelMeter level="internal" />
              </dd>
              <dd className={specimen.ledgerEffect}>Internal content the other rules allow.</dd>
            </div>
            <div className={specimen.ledgerRow}>
              <dt>Accounts</dt>
              <dd>Acme Corp and Northpeak Logistics</dd>
              <dd className={specimen.ledgerEffect}>Content for these accounts, and content that isn&apos;t tied to an account.</dd>
            </div>
            <div className={specimen.ledgerRow}>
              <dt>Departments</dt>
              <dd>Sales</dd>
              <dd className={specimen.ledgerEffect}>Also content restricted to these departments.</dd>
            </div>
            <div className={specimen.ledgerRow}>
              <dt>Direct grants</dt>
              <dd>None</dd>
              <dd className={specimen.ledgerEffect}>Each shares one document, whatever the rules above say.</dd>
            </div>
          </dl>
          <div className={specimen.review}>
            <p className={specimen.reviewHead}>Review change</p>
            <div className={specimen.diff} data-direction="gains">
              <span className={specimen.diffRule}>Accounts</span>
              <span className={specimen.diffNow}>
                <span className={specimen.diffLabel}>Now</span> Acme Corp, Northpeak Logistics
              </span>
              <span className={specimen.diffAfter}>
                <span className={specimen.diffLabel}>After</span> Acme Corp, Northpeak Logistics,{" "}
                <ins className={specimen.added}>+ Brightwater Health</ins>
              </span>
              <span className={specimen.diffWord}>Gains</span>
            </div>
          </div>
          <div className={specimen.audit}>
            <p className={specimen.auditHead}>Audit log</p>
            <p className={specimen.auditTime}>
              <time dateTime="2026-09-28T10:42">28 Sep 2026 · 10:42</time>
            </p>
            <p className={specimen.auditWhat}>
              <strong>Access changed</strong> · Maya Okafor · Accounts <span className={specimen.added}>+ Brightwater Health</span>
            </p>
            <p className={specimen.auditWho}>
              By Lena Brandt · “Covering Brightwater while Daniel Reyes is on leave.”
            </p>
          </div>
        </div>
      </figure>
    </section>
  );
}

function Closing() {
  return (
    <section className={styles.closing} aria-labelledby="closing-title">
      <h2 id="closing-title" className={styles.closingTitle}>
        Sign in through your organization.
      </h2>
      <div className={styles.closingAction}>
        <p className={styles.closingDeck}>Lattice uses your organization&apos;s identity provider. Your administrator decides who has access.</p>
        <ButtonLink href="/sign-in" variant="primary" size="lg" iconEnd={arrow} prefetch={false}>
          Sign in
        </ButtonLink>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.footerInner}>
        <Wordmark size={16} />
        <p className={styles.footerNote}>
          Examples on this page use Halden, a synthetic company. Its people, customers, documents and dates are invented.
        </p>
        <nav aria-label="Footer" className={styles.footerNav}>
          <ul>
            {SECTIONS.map((l) => (
              <li key={l.href}>
                <a href={l.href}>{l.label}</a>
              </li>
            ))}
            <li>
              <a href="/sign-in">Sign in</a>
            </li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}

