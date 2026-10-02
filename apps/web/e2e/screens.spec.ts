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
  EV,
  EVIDENCE_ONLY,
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
import { mockApi } from "./mock-api";

const only = process.env.SCREENS?.split(",");
const widths = process.env.SCREEN_WIDTHS ? process.env.SCREEN_WIDTHS.split(",").map(Number) : [...WIDTHS];

// Overlays (drawers, sheets) are fixed to the viewport: full-page captures distort them.
const OVERLAYS = new Set(["ask-inspector", "access-change"]);

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
  "sign-in": async (page) => {
    await mockApi(page);
    await page.goto("/sign-in");
  },
  "ask-intro": async (page) => {
    await openAsk(page);
  },
  "ask-pending": async (page) => {
    await openAsk(page, { ask: () => ANSWERED, askDelay: 60_000 });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme, and what did we tell them?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByText("Searching, drafting, verifying citations").waitFor();
  },
  "ask-answered": async (page) => {
    await askFor(page, ANSWERED);
  },
  "ask-inspector": async (page) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 }).waitFor();
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
    await page.getByText("Operational view").waitFor();
  },
  "trace-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.dangling}`);
    await page.getByRole("heading", { name: "This trace isn't available" }).waitFor();
  },
  evidence: async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.decision}`);
    await page.getByRole("heading", { name: /Identity roadmap decision/, level: 2 }).waitFor();
  },
  "evidence-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.missing}`);
    await page.getByRole("heading", { name: "This evidence isn't available" }).waitFor();
  },
  providers: async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/settings/providers");
    await page.getByRole("heading", { name: "Providers", exact: true }).waitFor();
  },
  "sources-member": async (page) => {
    await mockApi(page);
    await page.goto("/sources");
    await page.getByRole("heading", { name: "Your sources" }).waitFor();
  },
  "sources-admin": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/sources");
    await page.getByRole("button", { name: "Show records" }).click();
    await page.getByText("rec_3f9a0c1d2e4b5a67").waitFor();
  },
  "activity-member": async (page) => {
    await mockApi(page);
    await page.goto("/activity");
    await page.getByRole("heading", { name: "Your questions" }).waitFor();
  },
  "activity-admin": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/activity");
    await page.getByRole("heading", { name: "Organization activity" }).waitFor();
  },
  "access-members": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/access");
    await page.getByRole("heading", { name: "Members", exact: true }).waitFor();
  },
  "access-member": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByTestId("access-summary").waitFor();
  },
  "access-change": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByRole("button", { name: "Change access" }).click();
    await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
    await page.getByRole("button", { name: "Review change" }).click();
    await page.getByText("Maya Okafor will gain access to Kestrel Bank content.").waitFor();
  },
  "access-audit": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/access/audit");
    await page.getByText("Assigned Kestrel Bank").waitFor();
  },
  "providers-forbidden": async (page) => {
    await mockApi(page, { providers: "forbidden" });
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
