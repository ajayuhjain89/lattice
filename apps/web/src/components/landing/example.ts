/**
 * The landing page's one example: Halden, the synthetic company of Lattice's evaluation
 * corpus (data/synthetic/halden), and its "acme-scim-commitment" storyline. Every person,
 * customer, document and date here is invented, and the page labels it so wherever it is
 * shown. Passages are quoted from the synthetic documents (markdown emphasis removed);
 * the brief's wording follows the product's own (the API's conflict sentence, the access
 * sentence, the AI-data-policy notes). Nothing here comes from a signed-in organization,
 * and nothing is fetched: the page is static.
 */

export const EXAMPLE_LABEL = "Example · synthetic company Halden";

export const QUESTION = "When will Directory Sync (SCIM) be available for Acme, and what did we tell them?";

export interface ExamplePassage {
  /** The source text around the quote, as the inspector shows it. */
  before: string;
  /** The verified quote: checked word for word against the source. */
  quote: string;
  after: string;
}

export interface ExampleExhibit {
  n: number;
  title: string;
  /** A display reference, where the source has one. */
  ref?: string;
  source: string;
  kind: string;
  authority: string;
  date: string;
  /** ISO date, for <time>. */
  at: string;
  author: string;
  version: number;
  sensitivity: "Internal" | "Confidential";
  passage: ExamplePassage;
}

export const EXHIBITS: Record<"decision" | "ticket" | "kickoff" | "speculation", ExampleExhibit> = {
  decision: {
    n: 1,
    title: "Decision: Directory Sync (SCIM) general availability moves to December 2026",
    source: "Halden Drive",
    kind: "Document",
    authority: "System of record",
    date: "18 Sep 2026",
    at: "2026-09-18",
    author: "Tom Lindqvist",
    version: 1,
    sensitivity: "Internal",
    passage: {
      before: "Directory Sync (SCIM 2.0 provisioning for Okta and Entra ID) ",
      quote: "will be generally available in the 2026.12 release, targeted for 8 December 2026",
      after: ". A beta starts in November for two customers: Acme Corp from 9 November (Okta, up to 500 users) and Brightwater Health from 16 November (Entra ID).",
    },
  },
  ticket: {
    n: 2,
    title: "Users not deprovisioned after Okta deactivation - need SCIM",
    ref: "HAL-4821",
    source: "Halden Support",
    kind: "Ticket",
    authority: "Working",
    date: "14 Aug 2026",
    at: "2026-08-14",
    author: "Priya Natarajan",
    version: 1,
    sensitivity: "Internal",
    passage: {
      before: "Hi Derek — thanks for the detail, and I understand the audit pressure. ",
      quote: "Automated provisioning via SCIM 2.0 (we call it Directory Sync) is expected in October",
      after: ". Until then, you can deactivate users in bulk through our Admin API; …",
    },
  },
  kickoff: {
    n: 3,
    title: "Acme Corp — Okta migration kickoff call notes",
    source: "Halden Drive",
    kind: "Document",
    authority: "Working",
    date: "12 Jun 2026",
    at: "2026-06-12",
    author: "Maya Okafor",
    version: 1,
    sensitivity: "Internal",
    passage: {
      before: "",
      quote: "Maya told them Directory Sync (SCIM 2.0 provisioning) is planned for Q3 on the current roadmap",
      after: " and that Acme would be among the first customers on it.",
    },
  },
  speculation: {
    n: 4,
    title: "#sales — 25 Sep 2026",
    source: "Halden Slack",
    kind: "Slack conversation",
    authority: "Conversational",
    date: "25 Sep 2026",
    at: "2026-09-25",
    author: "Marcus Feld",
    version: 1,
    sensitivity: "Internal",
    passage: {
      before: "heads up — talked to Wei on the identity squad, the rewrite is going faster than planned. ",
      quote: "sounds like Directory Sync could be GA early November for everyone",
      after: ", not just the beta …",
    },
  },
};

/** Readable by Grace (Leadership, Confidential clearance), not by Maya; kept from the model by policy. */
export const ESCALATION = {
  title: "Acme Corp — executive escalation notes",
  source: "Halden Drive",
  date: "22 Sep 2026",
  at: "2026-09-22",
  sensitivity: "Confidential" as const,
};

export interface ExampleClaim {
  key: string;
  role: "lead" | "supporting";
  text: string;
  /** Exhibit numbers, in citation order. */
  cites: readonly number[];
}

/** The brief. The lead is attributed to its source; it doesn't declare the plan true. */
export const CLAIMS: readonly ExampleClaim[] = [
  {
    key: "lead",
    role: "lead",
    text: "An approved decision record dated 18 Sep 2026 targets Directory Sync general availability for 8 December, with a beta for Acme from 9 November.",
    cites: [1],
  },
  {
    key: "c2",
    role: "supporting",
    text: "On 14 Aug, Support told Acme that SCIM was expected in October.",
    cites: [2],
  },
  {
    key: "c3",
    role: "supporting",
    text: "At the June kickoff, Acme was told Directory Sync was planned for Q3.",
    cites: [3],
  },
];

/** The brief's ledger line, as the product writes it. */
export const STATUS_FACTS = ["3 claims", "3 exhibits", "Newest 18 Sep 2026"] as const;

/** The API's conflict sentence for these positions (answering/assemble.py), unchanged. */
export const CONFLICT_TOPIC = "Directory Sync availability";
export const CONFLICT_SUMMARY = `These sources may disagree. The most recent is the Slack conversation "${EXHIBITS.speculation.title}" (${EXHIBITS.speculation.date}). Lattice can't confirm which is current.`;

export const CONFLICT_POSITIONS = [
  { exhibit: EXHIBITS.ticket, claim: "SCIM is expected in October." },
  { exhibit: EXHIBITS.decision, claim: "General availability is targeted for 8 December, with an Acme beta from 9 November." },
  { exhibit: EXHIBITS.speculation, claim: "Directory Sync could be generally available in early November." },
] as const;

/** Two seeded personas and the facts of their access that the comparison needs. */
export const READERS = {
  maya: {
    name: "Maya Okafor",
    role: "Account Manager",
    facts: ["Internal clearance", "Acme Corp, Northpeak Logistics", "Sales"],
  },
  grace: {
    name: "Grace Whitfield",
    role: "VP Customer Success",
    facts: ["Confidential clearance", "All accounts", "Leadership, Customer Success"],
  },
} as const;
