/**
 * Visual review harness: renders every screen and state at the five review widths and
 * writes full-page screenshots to e2e/screens/ (git-ignored). Run explicitly:
 *   pnpm exec playwright test --project=screens
 * It asserts only the layout invariants; the images are for human review.
 */
import { test, type Page } from "@playwright/test";

import {
  AMBIGUOUS,
  ANSWERED,
  CONFLICTS,
  MANY_EXHIBITS,
  MANY_TO_MANY,
  RATE_LIMITED,
  auditPages,
  EV,
  EVIDENCE_ONLY,
  FIXTURE_NOW,
  MANY_MEMBERS,
  MEMBER_MAYA,
  MEMBER_WIDE,
  PREVIEW_MIXED,
  PROVIDERS_MANY,
  SOURCES_ADMIN_LONG,
  FAILED,
  ID,
  INSUFFICIENT,
  ME_LENA,
  PARTIAL,
  SUPERSEDED,
  TRACE,
  UNAVAILABLE,
  UNSUPPORTED,
  UNVERIFIED,
} from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow, openAsk, WIDTHS } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

const only = process.env.SCREENS?.split(",");
const widths = process.env.SCREEN_WIDTHS ? process.env.SCREEN_WIDTHS.split(",").map(Number) : [...WIDTHS];

// Overlays (drawers, sheets) are fixed to the viewport: full-page captures distort them.
const OVERLAYS = new Set(["ask-inspector", "ask-inspector-long", "access-change"]);

/** The operational screens read ages and day headings: they render at the fixtures' "now". */
async function at(page: Page, options: MockOptions = {}) {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  return mockApi(page, options);
}

// The development-sign-in-off server (playwright.config.ts): production sign-in.
const PRODUCTION = "http://127.0.0.1:3101";

// SCREENS_DIR writes elsewhere (e.g. a review folder) without touching the harness output.
const DIR = process.env.SCREENS_DIR ?? "e2e/screens";

const shot = async (page: Page, name: string, width: number) => {
  await page.waitForTimeout(250);
  await expectNoHorizontalOverflow(page);
  const fullPage = !(OVERLAYS.has(name) && width < 1280);
  await page.screenshot({ path: `${DIR}/${name}@${width}.png`, fullPage, animations: "disabled" });
};

const SCREENS: Record<string, (page: Page) => Promise<void>> = {
  specimen: async (page) => {
    await page.goto("/specimen");
    await page.evaluate(() => document.fonts.ready);
  },
  "specimen-dark": async (page) => {
    await page.goto("/specimen");
    await page.getByRole("button", { name: "Dark map" }).click();
    await page.evaluate(() => document.fonts.ready);
  },
  landing: async (page) => {
    await page.goto("/");
    await page.evaluate(() => document.fonts.ready);
  },
  "sign-in-dev": async (page) => {
    await mockApi(page);
    await page.goto("/sign-in");
  },
  "sign-in-dev-next": async (page) => {
    await mockApi(page);
    await page.goto(`/sign-in?next=${encodeURIComponent("/access/audit")}`);
  },
  "sign-in-dev-failed": async (page) => {
    await mockApi(page, { devLogin: { status: 401, body: { error: { code: "authentication_failed", message: "Sign-in failed.", request_id: "r" } } } });
    await page.goto("/sign-in");
    await page.getByRole("button", { name: /Maya Okafor/ }).click();
    await page.getByRole("alert").filter({ hasText: "Sign-in failed." }).waitFor();
  },
  "sign-in-production": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in`);
  },
  "sign-in-production-error": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?error=sso_failed`);
  },
  "sign-in-production-rate-limited": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?error=rate_limited`);
  },
  "sign-in-production-next": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?next=${encodeURIComponent("/access/members/40000000-0000-4000-8000-000000000001")}`);
  },
  "ask-intro": async (page) => {
    await openAsk(page);
  },
  "ask-pending": async (page) => {
    await openAsk(page, { ask: () => ANSWERED, askDelay: 60_000 });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme, and what did we tell them?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByTestId("ask-progress").waitFor();
  },
  "ask-answered": async (page) => {
    await askFor(page, ANSWERED);
  },
  "ask-inspector": async (page) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 }).waitFor();
  },
  "ask-many-to-many": async (page) => {
    await askFor(page, MANY_TO_MANY);
  },
  "ask-conflicts": async (page) => {
    await askFor(page, CONFLICTS, "How big is Acme's pilot, and how fast must Support respond?");
  },
  "ask-many-exhibits": async (page) => {
    await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say about scope, dates and support?");
  },
  "ask-inspector-long": async (page) => {
    await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say about scope, dates and support?");
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 }).first().waitFor();
  },
  "ask-rate-limited": async (page) => {
    await openAsk(page, { ask: () => RATE_LIMITED });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByRole("heading", { name: "Too many questions in a row" }).waitFor();
  },
  "ask-partial": async (page) => {
    await askFor(page, PARTIAL);
  },
  "ask-insufficient": async (page) => {
    await askFor(page, INSUFFICIENT, "What did Kestrel agree to in the renewal?");
  },
  "ask-unverified": async (page) => {
    await askFor(page, UNVERIFIED);
  },
  "ask-evidence-only": async (page) => {
    await askFor(page, EVIDENCE_ONLY, "What did Acme's CIO say at the QBR?");
  },
  "ask-unavailable": async (page) => {
    await askFor(page, UNAVAILABLE);
  },
  "ask-ambiguous": async (page) => {
    await askFor(page, AMBIGUOUS, "What did we promise Acme?");
  },
  "ask-unsupported": async (page) => {
    await askFor(page, UNSUPPORTED, "Draft an email to Acme about the SCIM delay");
  },
  "ask-failed": async (page) => {
    await askFor(page, FAILED);
  },
  "ask-superseded": async (page) => {
    await askFor(page, SUPERSEDED, "What is the refund window?");
  },
  "ask-network-error": async (page) => {
    await openAsk(page, { ask: () => "network" });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByRole("heading", { name: "Couldn't reach Lattice" }).waitFor();
  },
  "trace-asker": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.answered}`);
    await page.getByRole("heading", { name: "Pipeline", exact: true }).waitFor();
  },
  "trace-hidden": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.hidden}`);
    await page.getByText("Answer hidden").waitFor();
  },
  "trace-admin": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto(`/traces/${TRACE.other}`);
    await page.getByRole("heading", { name: "Operational trace" }).waitFor();
  },
  "trace-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.dangling}`);
    await page.getByRole("heading", { name: "This trace isn't available" }).waitFor();
  },
  evidence: async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.decision}`);
    await page.getByRole("heading", { name: /Identity roadmap decision/, level: 1 }).waitFor();
  },
  "evidence-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.missing}`);
    await page.getByRole("heading", { name: "This evidence isn't available" }).waitFor();
  },
  providers: async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/settings/providers");
    await page.getByTestId("egress-lattice").waitFor();
  },
  "providers-many": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_MANY });
    await page.goto("/settings/providers");
    await page.getByTestId("egress-lattice").waitFor();
    await page.getByRole("button", { name: /^Confidential data isn't sent to anthropic/ }).focus();
  },
  "sources-member": async (page) => {
    await at(page);
    await page.goto("/sources");
    await page.getByRole("table", { name: "Sources you can read from" }).and(page.locator(":not([aria-busy])")).waitFor();
  },
  "sources-admin": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/sources");
    await page.getByTestId("register-summary").waitFor();
  },
  "sources-failed": async (page) => {
    await at(page, { me: ME_LENA, adminSources: SOURCES_ADMIN_LONG });
    await page.goto("/sources");
    await page.getByRole("button", { name: "Show failed records: Halden Support (Zendesk)" }).click();
    await page.getByText("rec_3f9a0c1d2e4b5a67").waitFor();
  },
  "sources-empty": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [] } });
    await page.goto("/sources");
    await page.getByRole("heading", { name: "No sources are connected" }).waitFor();
  },
  "activity-member": async (page) => {
    await at(page);
    await page.goto("/activity");
    await page.getByRole("heading", { name: /^Today/ }).waitFor();
  },
  "activity-empty": async (page) => {
    await at(page, { activity: { items: [] } });
    await page.goto("/activity");
    await page.getByRole("heading", { name: "You haven't asked anything yet" }).waitFor();
  },
  "activity-admin": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/activity/organization");
    await page.getByRole("table", { name: "Organization runs (operational view)" }).and(page.locator(":not([aria-busy])")).waitFor();
  },
  "access-members": async (page) => {
    await at(page, { me: ME_LENA, members: MANY_MEMBERS });
    await page.goto("/access");
    await page.getByRole("link", { name: "Member 02" }).waitFor();
  },
  "access-member": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-member-wide": async (page) => {
    await at(page, { me: ME_LENA, member: { [MEMBER_WIDE.member.id]: MEMBER_WIDE } });
    await page.goto(`/access/members/${MEMBER_WIDE.member.id}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-suspended": async (page) => {
    await at(page, { me: ME_LENA, member: { [ID.maya]: { ...MEMBER_MAYA, member: { ...MEMBER_MAYA.member, status: "suspended" } } } });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-change": async (page) => {
    await at(page, { me: ME_LENA, preview: PREVIEW_MIXED });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByRole("button", { name: "Change access" }).click();
    await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
    await page.getByRole("checkbox", { name: "Sales" }).uncheck();
    await page.getByRole("radio", { name: "Confidential" }).check();
    await page.getByRole("button", { name: "Review change" }).click();
    await page.getByRole("table", { name: "Now and after this change, rule by rule" }).waitFor();
  },
  "access-applied": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByRole("button", { name: "Change access" }).click();
    await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
    await page.getByRole("button", { name: "Review change" }).click();
    await page.getByRole("textbox", { name: "Reason" }).fill("Covering Kestrel during the renewal");
    await page.getByRole("button", { name: "Apply change" }).click();
    await page.getByTestId("applied-change").waitFor();
  },
  "access-audit": async (page) => {
    await at(page, { me: ME_LENA, audit: (before) => auditPages(before) });
    await page.goto("/access/audit");
    await page.getByTestId("audit-entry").first().waitFor();
  },
  "providers-forbidden": async (page) => {
    await at(page, { providers: "forbidden" });
    await page.goto("/settings/providers");
    await page.getByRole("heading", { name: /administrator access/ }).waitFor();
  },
};

for (const [name, render] of Object.entries(SCREENS)) {
  if (only && !only.includes(name)) continue;
  for (const width of widths) {
    test(`${name} @ ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
      await render(page);
      await shot(page, name, width);
    });
  }
}
