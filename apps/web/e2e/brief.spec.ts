/**
 * D4: the Ask desk, the brief, the claim ↔ exhibit relationship, the provenance lattice,
 * the inspector, disagreement and the boundary states (design-direction §5, §6.5).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  ANSWERED,
  CONFLICTS,
  DETAILS,
  EV,
  EVIDENCE_ONLY,
  INSUFFICIENT,
  MANY_EXHIBITS,
  MANY_TO_MANY,
  PARTIAL,
  RATE_LIMITED,
  SUPERSEDED,
  TRACE,
  TRACE_ASKER,
  UNAVAILABLE,
} from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow, openAsk, settled, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

const row = (page: Page, id: string) => page.locator(`[data-testid='evidence'] [data-evidence="${id}"]`);
const litClaims = (page: Page) =>
  page.locator("[data-testid='answer-prose'] [data-claim]").evaluateAll((els) => els.filter((e) => e.querySelector("[data-lit]")).map((e) => e.getAttribute("data-claim")));

test.describe("desk", () => {
  test("before a question: the scope sentence, the person's reach and recent questions -- no lattice, no chat", async ({ page }) => {
    await openAsk(page);
    await expect(page.getByRole("heading", { name: "Ask what Halden knows.", level: 1 })).toBeVisible();
    const scope = page.getByRole("group", { name: "Search scope" });
    await expect(scope.getByRole("button")).toHaveText(["all my sources", "any account", "any date", "default sensitivity"]);
    const reach = page.getByTestId("your-reach");
    await expect(reach).toContainText("Halden Drive");
    await expect(reach).toContainText("41 documents you can read");
    await expect(reach).toContainText("Acme Corp, Northpeak Logistics");
    await expect(page.getByTestId("recent-questions").getByRole("link")).toHaveCount(2);
    await expect(page.getByTestId("provenance-lattice")).toHaveCount(0);
    await audit(page);
  });

  test("the scope sentence works from the keyboard and narrows only what is sent", async ({ page }) => {
    const log = await openAsk(page, { ask: () => ANSWERED });
    const sources = page.getByRole("button", { name: "Sources: all my sources. Change" });
    await sources.focus();
    await page.keyboard.press("Enter");
    const tickets = page.getByRole("checkbox", { name: "Tickets" });
    await expect(tickets).toBeVisible();
    await tickets.focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("Escape");
    const narrowed = page.getByRole("button", { name: "Sources: tickets. Change" });
    await expect(narrowed).toBeFocused();
    await expect(page.getByRole("button", { name: "Remove sources filter" })).toBeVisible();
    await page.keyboard.press("/");
    await page.keyboard.type("When will SCIM ship?");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("answer")).toBeVisible();
    expect(log.asks[0]).toEqual({ question: "When will SCIM ship?", scope: { source_types: ["ticket"] } });
  });

  test("an invalid date range is caught before sending, in words, and the question is kept", async ({ page }) => {
    const log = await openAsk(page, { ask: () => ANSWERED });
    await page.getByRole("button", { name: "Dates: any date. Change" }).click();
    await page.getByLabel("From", { exact: true }).fill("2026-09-30");
    await page.getByLabel("To", { exact: true }).fill("2026-09-01");
    await page.keyboard.press("Escape");
    const box = page.getByRole("textbox", { name: "Question" });
    await box.fill("What changed?");
    await box.press("Enter");
    await expect(page.locator("main").getByRole("alert")).toHaveText("The start date must be on or before the end date.");
    await expect(page.getByRole("button", { name: /^Dates: .*needs a fix/ })).toBeVisible();
    await expect(box).toHaveValue("What changed?");
    expect(log.asks).toEqual([]);
  });
});

test.describe("states", () => {
  test("partial: the keyword, the API's notice once, and what the evidence doesn't establish", async ({ page }) => {
    await askFor(page, PARTIAL);
    await expect(page.getByTestId("status-line")).toContainText("Partial answer");
    await expect(page.getByText("Some statements couldn't be verified against sources and were removed.")).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "What the evidence doesn't establish" })).toBeVisible();
  });

  test("insufficient evidence: a quiet state with what was searched -- not an error, nothing hatched", async ({ page }) => {
    await askFor(page, INSUFFICIENT, "What did Kestrel agree to?");
    await expect(page.locator("[data-state-family='quiet']")).toContainText("Searched");
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    await expect(page.getByTestId("evidence")).toHaveCount(0);
  });

  test("withheld from the model (AI data policy): one hatched boundary, readable evidence listed", async ({ page }) => {
    await askFor(page, EVIDENCE_ONLY, "What did Acme's CIO say?");
    await expect(page.getByRole("heading", { name: "Sources found — not sent to the AI model" })).toBeVisible();
    const group = page.getByRole("group", { name: /Not sent to the AI model/ });
    await expect(group.locator("[data-evidence]")).toHaveCount(2);
    const hatch = await group.evaluate((el) => getComputedStyle(el, "::before").backgroundImage);
    expect(hatch).toContain("repeating-linear-gradient");
  });

  test("no model available: evidence listed, no AI-policy boundary drawn (none was crossed)", async ({ page }) => {
    await askFor(page, UNAVAILABLE);
    await expect(page.getByRole("heading", { name: "Sources found — no AI model available" })).toBeVisible();
    await expect(page.getByRole("group", { name: /Not sent to the AI model/ })).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText("AI data policy");
  });

  test("rate limited: a wait, not a fault; the question is kept and retry works", async ({ page }) => {
    let n = 0;
    const log = await openAsk(page, { ask: () => (n++ === 0 ? RATE_LIMITED : ANSWERED) });
    const box = page.getByRole("textbox", { name: "Question" });
    await box.fill("When will SCIM ship?");
    await box.press("Enter");
    const wait = page.locator("[data-state-family='wait']");
    await expect(wait).toContainText("Too many questions in a row");
    await expect(wait).toContainText("Try again in 30 seconds");
    await expect(wait).toHaveAttribute("role", "status");
    await expect(page.locator("main [role='alert']")).toHaveCount(0);
    await expect(box).toHaveValue("When will SCIM ship?");
    await wait.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByTestId("answer-prose")).toBeVisible();
    expect(log.asks).toHaveLength(2);
  });

  test("explicit supersession: neutral, recorded by the source, with the stale notice -- not a conflict", async ({ page }) => {
    await askFor(page, SUPERSEDED, "What is the refund window?");
    const block = page.getByRole("region", { name: /^Superseded/ });
    await expect(block).toHaveAttribute("data-kind", "superseded");
    await expect(block).toContainText("as recorded by its source");
    await expect(block.getByRole("listitem")).toHaveText([/2 Nov 2025.*Earlier document/, /3 Mar 2026.*Replacement/]);
    await expect(page.getByRole("region", { name: /^Potential conflict/ })).toHaveCount(0);
    await expect(page.getByTestId("notices")).toContainText("The most recent evidence available to you is from 3 Mar 2026.");
    await block.getByRole("button", { name: "Inspect earlier" }).click();
    await expect(page.getByTestId("evidence-inspector").getByRole("heading", { name: "Refund policy v1" })).toBeVisible();
    await expect(page.getByTestId("evidence-inspector")).toContainText("Replaced by Refund policy v2, as recorded by the source.");
  });

  test("potential conflicts: same-day, undated and years-apart positions, equal weight, no verdict", async ({ page }) => {
    await askFor(page, CONFLICTS);
    const pilot = page.getByRole("region", { name: /Pilot group size/ });
    await expect(pilot.getByRole("listitem")).toHaveCount(3);
    await expect(pilot).toContainText("Same day");
    await expect(pilot).toContainText("Undated");
    await expect(page.getByRole("region", { name: /Priority ticket response time/ })).toContainText("1,308 days later");
    const sizes = await pilot.getByRole("listitem").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)));
    expect(Math.max(...sizes) - Math.min(...sizes)).toBeLessThanOrEqual(1);
    await expect(page.locator("main")).not.toContainText(/is correct|is current\b(?!\.)|winner/i);
  });
});

test.describe("claims and exhibits", () => {
  test("one-to-one citations: no lattice -- the margin markers say it all", async ({ page }) => {
    await askFor(page, ANSWERED);
    await expect(page.getByTestId("provenance-lattice")).toHaveCount(0);
    await expect(page.getByRole("table")).toHaveCount(0);
  });

  test("many-to-many: the lattice appears; arrow keys walk its cells and light claim and exhibit together", async ({ page }) => {
    await askFor(page, MANY_TO_MANY);
    const table = page.getByRole("table", { name: "Which claims cite which exhibits" });
    await expect(table).toBeVisible();
    const first = table.getByRole("button", { name: "Claim 1 cites exhibit 1, DR-112" });
    await first.focus();
    await expect(row(page, EV.decision)).toHaveAttribute("data-lit", "true");
    expect(await litClaims(page)).toEqual(["claim-0"]);
    await page.keyboard.press("ArrowDown");
    await expect(table.getByRole("button", { name: /^Claim 2 cites exhibit 2/ })).toBeFocused();
    await expect(row(page, EV.kickoff)).toHaveAttribute("data-lit", "true");
    await expect(row(page, EV.decision)).not.toHaveAttribute("data-lit");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("End");
    await expect(table.getByRole("button", { name: /^Claim 3 cites exhibit 4/ })).toBeFocused();
    await page.keyboard.press("Enter");
    const inspector = page.getByTestId("evidence-inspector");
    await expect(inspector.getByRole("heading", { name: "#acme-account — 3 Sep 2026" })).toBeVisible();
    await expect(table.getByRole("button", { name: /^Claim 3 cites exhibit 4/ })).toHaveAttribute("aria-pressed", "true");
    await audit(page);
  });

  test("claim → exhibit: hovering or focusing a marker lights its claim and its exhibit row only", async ({ page }) => {
    await askFor(page, MANY_TO_MANY);
    const marker = page.locator("[data-claim='claim-3'] [data-citation='2']");
    await marker.focus();
    await expect(row(page, EV.kickoff)).toHaveAttribute("data-lit", "true");
    await expect(row(page, EV.decision)).not.toHaveAttribute("data-lit");
    expect(await litClaims(page)).toEqual(["claim-3"]);
    await page.mouse.move(0, 0);
    await page.locator("[data-claim='claim-2'] p span").first().hover();
    await expect(row(page, EV.ticket)).toHaveAttribute("data-lit", "true");
    await expect(row(page, EV.slack)).toHaveAttribute("data-lit", "true");
  });

  test("exhibit → claims: one exhibit lights every claim that cites it, and its markers", async ({ page }) => {
    await askFor(page, MANY_TO_MANY);
    await row(page, EV.decision).hover();
    expect(await litClaims(page)).toEqual(["claim-0", "claim-3"]);
    await expect(page.locator("[data-claim='claim-3'] [data-citation='1']")).toHaveClass(/linked/);
    await expect(page.locator("[data-claim='claim-3'] [data-citation='2']")).not.toHaveClass(/linked/);
    await row(page, EV.decision).focus();
    expect(await litClaims(page)).toEqual(["claim-0", "claim-3"]);
  });

  test("inspector: keyboard steps through exhibits, Show returns to the claim, Escape restores focus, reading position kept", async ({ page }) => {
    await askFor(page, MANY_TO_MANY);
    const marker = page.locator("[data-claim='claim-3'] [data-citation='1']");
    await marker.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => window.scrollY);
    await marker.focus();
    await page.keyboard.press("Enter");
    const inspector = page.getByTestId("evidence-inspector");
    await expect(inspector).toBeFocused();
    expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThanOrEqual(2);
    await expect(inspector.getByRole("list", { name: "Provenance" })).toContainText("quote verified");
    const citedBy = inspector.getByRole("heading", { name: /Cited by/ }).locator("xpath=following-sibling::ol[1]").getByRole("listitem");
    await expect(citedBy).toHaveCount(2);
    await page.keyboard.press("]");
    await expect(inspector.getByRole("heading", { name: "Acme kickoff — call notes" })).toBeVisible();
    await page.keyboard.press("[");
    await expect(inspector.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
    await inspector.getByRole("button", { name: /^Show in answer: SCIM general availability moves to December/ }).click();
    await expect(page.locator("[data-claim='claim-0'] [data-citation='1']")).toBeFocused();
    await expect(inspector).toBeVisible();
    await inspector.focus();
    await page.keyboard.press("Escape");
    await expect(inspector).toBeHidden();
    await expect(page.locator("[data-claim='claim-0'] [data-citation='1']")).toBeFocused();
  });

  test("no marker, cell or row for evidence the response doesn't contain", async ({ page }) => {
    await askFor(page, MANY_TO_MANY);
    const ids = await page.locator("[data-evidence]").evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute("data-evidence")))]);
    expect(ids.every((id) => MANY_TO_MANY.evidence.some((e) => e.evidence_id === id))).toBe(true);
  });
});

test.describe("long and dense answers", () => {
  for (const width of [...WIDTHS, 320]) {
    test(`many exhibits, a long title and a long quote fit at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say about scope, dates and support?");
      await expect(page.locator("[data-testid='evidence'] ol").first().locator("> li")).toHaveCount(10);
      await expectNoHorizontalOverflow(page);
      const title = page.getByTestId("evidence").getByText(/^Acme Corp — SCIM provisioning rollout plan.*security review$/);
      const lines = await title.evaluate((el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight)));
      expect(lines).toBeLessThanOrEqual(2);
    });
  }

  test("the long exhibit opens with its quote in view inside the inspector", async ({ page }) => {
    await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say?");
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    const inspector = page.getByTestId("evidence-inspector");
    const mark = inspector.locator("mark").first();
    await expect(mark).toBeInViewport();
    await expectNoHorizontalOverflow(page);
  });
});

test.describe("modes", () => {
  test("200 % zoom (640 CSS px): the answer and its inspector reflow without page scroll", async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 450 });
    await askFor(page, MANY_TO_MANY);
    await expectNoHorizontalOverflow(page);
    await page.locator("[data-claim='claim-0'] [data-citation='1']").click();
    await expect(page.getByRole("dialog", { name: "Evidence inspector" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("reduced motion: the brief arrives without movement", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await askFor(page, ANSWERED);
    const names = await page.locator("[data-testid='answer'] > *").evaluateAll((els) => els.map((e) => getComputedStyle(e).animationName));
    expect(new Set(names)).toEqual(new Set(["none"]));
  });

  test("forced colours: hatching yields to its dashed edge; the open exhibit stays marked without colour", async ({ page }) => {
    await page.emulateMedia({ forcedColors: "active" });
    await askFor(page, ANSWERED);
    const group = page.getByRole("group", { name: /Not sent to the AI model/ });
    expect(await group.evaluate((el) => getComputedStyle(el, "::before").backgroundImage)).toBe("none");
    expect(await group.evaluate((el) => getComputedStyle(el, "::before").borderRightStyle)).toBe("dashed");
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await expect(page.getByRole("button", { name: /^Citation 1:/ }).first()).toHaveAttribute("aria-pressed", "true");
    await expectNoHorizontalOverflow(page);
  });
});

test.describe("trace and evidence", () => {
  test("trace: the pipeline as numbered stops, the recorded answer with markers, exhibits by title", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.answered}`);
    const pipeline = page.getByRole("region", { name: "Pipeline" });
    await expect(pipeline.locator("ol > li")).toHaveCount(5);
    await expect(pipeline.getByRole("list", { name: "Rejection reasons" })).toContainText("quote_not_found");
    await expect(page.getByTestId("trace-scope")).toContainText("every source type you could read");
    const exhibits = page.getByTestId("trace-exhibits");
    await expect(exhibits.getByRole("button", { name: /^Inspect evidence 1: DR-112 · Identity roadmap decision/ })).toBeVisible();
    await expect(exhibits.getByRole("button")).toHaveCount(TRACE_ASKER.evidence_ids.length);
    await page.getByRole("button", { name: /^Citation 2/ }).first().click();
    await expect(page.getByTestId("evidence-inspector").getByRole("heading", { name: "Acme kickoff — call notes" })).toBeVisible();
    await audit(page);
  });

  test("trace: evidence revoked since the answer shows one uniform row, nothing of what it was", async ({ page }) => {
    const { [EV.kickoff]: _revoked, ...rest } = DETAILS;
    await mockApi(page, { evidence: rest });
    await page.goto(`/traces/${TRACE.answered}`);
    const exhibits = page.getByTestId("trace-exhibits");
    await expect(exhibits.getByText("This evidence isn't available now.")).toHaveCount(1);
    await expect(exhibits).not.toContainText("Acme kickoff");
    await expect(exhibits).not.toContainText("Maya Okafor");
  });

  test("evidence page: the provenance chain, the passage and a way back", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.decision}`);
    await expect(page.getByRole("heading", { level: 1, name: "DR-112 · Identity roadmap decision" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Provenance" })).toContainText("Halden Drive");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Ask" })).toHaveAttribute("href", "/ask");
    await expect(page.getByTestId("passage")).toContainText("SCIM GA moves to December");
    // No answer on screen: nothing is marked as a verified quote.
    await expect(page.getByTestId("passage").locator("mark")).toHaveCount(0);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await expectNoHorizontalOverflow(page);
    }
  });

  test("D-8: no account in the inspector when the API omits it, whatever the text mentions", async ({ page }) => {
    const decision = { ...DETAILS[EV.decision]!, account: null, text: `${DETAILS[EV.decision]!.text}\n\nAcme Corp is mentioned here.` };
    await askFor(page, ANSWERED, undefined, { evidence: { ...DETAILS, [EV.decision]: decision } });
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    const inspector = page.getByTestId("evidence-inspector");
    await expect(inspector.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
    await expect(inspector.getByText("Account", { exact: true })).toHaveCount(0);
    await expect(inspector.getByRole("list", { name: "Provenance" })).not.toContainText("Acme");
  });
});
