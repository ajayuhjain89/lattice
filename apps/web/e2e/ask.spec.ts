import { expect, test } from "@playwright/test";

import { AMBIGUOUS, ANSWERED, EV, EVIDENCE_ONLY, FAILED, INSUFFICIENT, UNSUPPORTED } from "../src/test/fixtures";

import { askFor, openAsk } from "./helpers";

test("Ask → answer: claims, markers, the ledger line, trace link", async ({ page }) => {
  const log = await askFor(page, ANSWERED);
  expect(log.asks).toEqual([{ question: "When will SCIM ship for Acme, and what did we tell them?", scope: {} }]);
  expect(log.headers.find((h) => h["x-lattice-request"])?.["x-lattice-request"]).toBe("1");
  await expect(page.getByTestId("answer-prose")).toContainText("SCIM general availability to December");
  await expect(page.getByTestId("status-line")).toContainText("3 claims");
  await expect(page.getByTestId("status-line")).toContainText("3 exhibits");
  await expect(page.getByRole("link", { name: "Trace", exact: true })).toHaveAttribute("href", `/traces/${ANSWERED.trace_id}`);
});

test("citation → evidence: fetched on open, never kept after close", async ({ page }) => {
  const log = await askFor(page, ANSWERED);
  const chip = page.getByRole("button", { name: /^Citation 1:/ }).first();
  await chip.click();
  const inspector = page.getByTestId("evidence-inspector");
  await expect(inspector.getByRole("heading", { name: "DR-112 · Identity roadmap decision" })).toBeVisible();
  await expect(inspector.locator("mark")).toContainText(["SCIM GA moves to December"]);
  await expect(inspector.getByRole("heading", { name: /Cited by/ })).toBeVisible();
  await inspector.getByRole("button", { name: "Close evidence" }).click();
  await expect(inspector).toBeHidden();
  await chip.click();
  await expect(inspector.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
  expect(log.evidence.filter((id) => id === EV.decision)).toHaveLength(2);
});

test("conflicting sources: both positions, chronological, no verdict", async ({ page }) => {
  await askFor(page, ANSWERED);
  const conflict = page.getByRole("region", { name: "Potential conflict" });
  const rows = conflict.getByRole("listitem");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("14 Aug 2026");
  await expect(rows.nth(1)).toContainText("18 Sep 2026");
  await expect(conflict).toContainText("Lattice can't confirm which is current.");
  await expect(conflict).not.toContainText(/supersedes|is correct|resolved/i);
});

test("ambiguous → the chosen account is sent by key and shown in the scope sentence", async ({ page }) => {
  const log = await openAsk(page, { ask: (b) => (b.scope?.account ? ANSWERED : AMBIGUOUS) });
  await page.getByRole("textbox", { name: "Question" }).fill("What did we promise Acme?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Which account do you mean?" })).toBeVisible();
  await page.getByRole("group", { name: "Choices" }).getByRole("button", { name: "Acme Corp" }).click();
  await expect(page.getByTestId("answer-prose")).toBeVisible();
  expect(log.asks[1]).toEqual({ question: "What did we promise Acme?", scope: { account: "acme-corp" } });
  await expect(page.getByRole("group", { name: "Search scope" }).getByRole("button", { name: "Account: Acme Corp. Change" })).toBeVisible();
});

test("evidence_only: no prose, the readable sources listed", async ({ page }) => {
  await askFor(page, EVIDENCE_ONLY, "What did Acme's CIO say at the QBR?");
  await expect(page.getByRole("heading", { name: "Sources found — not sent to the AI model" })).toBeVisible();
  await expect(page.getByTestId("answer-prose")).toHaveCount(0);
  await expect(page.getByTestId("evidence").getByRole("button", { name: /Open in inspector/ })).toHaveCount(2);
});

test("insufficient_evidence: a quiet state, not an error", async ({ page }) => {
  await askFor(page, INSUFFICIENT, "What did Kestrel agree to?");
  await expect(page.getByRole("heading", { name: "Nothing you can access answers this" })).toBeVisible();
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
});

test("unsupported_request: explains that Lattice doesn't act", async ({ page }) => {
  await askFor(page, UNSUPPORTED, "Draft an email to Acme");
  await expect(page.getByRole("heading", { name: "Lattice doesn't take actions" })).toBeVisible();
  await expect(page.getByTestId("answer-prose")).toHaveCount(0);
});

test("failed → Try again resends exactly the same request", async ({ page }) => {
  let n = 0;
  const log = await openAsk(page, { ask: () => (n++ === 0 ? FAILED : ANSWERED) });
  await page.getByRole("button", { name: "Sources: all my sources. Change" }).click();
  await page.getByRole("checkbox", { name: "Tickets" }).check();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship?");
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await page.getByRole("alert").getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("answer-prose")).toBeVisible();
  expect(log.asks).toHaveLength(2);
  expect(log.asks[1]).toEqual(log.asks[0]);
  expect(log.asks[0]).toEqual({ question: "When will SCIM ship?", scope: { source_types: ["ticket"] } });
});

test("network failure keeps the question and offers a retry", async ({ page }) => {
  await openAsk(page, { ask: () => "network" });
  const box = page.getByRole("textbox", { name: "Question" });
  await box.fill("When will SCIM ship?");
  await box.press("Enter");
  await expect(page.getByRole("heading", { name: "Couldn't reach Lattice" })).toBeVisible();
  await expect(box).toHaveValue("When will SCIM ship?");
  await expect(page.getByRole("button", { name: "Try again" })).toBeEnabled();
});

test("pending: the composer is busy and a second submit is ignored", async ({ page }) => {
  const log = await openAsk(page, { ask: () => ANSWERED, askDelay: 1500 });
  const box = page.getByRole("textbox", { name: "Question" });
  await box.fill("When will SCIM ship?");
  await box.press("Enter");
  await expect(page.getByRole("button", { name: "Asking" })).toBeDisabled();
  await box.press("Enter");
  await expect(page.getByTestId("answer")).toBeVisible();
  expect(log.asks).toHaveLength(1);
});

test("the question sensitivity can only be raised, and is sent", async ({ page }) => {
  const log = await openAsk(page, { ask: () => EVIDENCE_ONLY });
  await page.getByRole("button", { name: "Sensitivity: default sensitivity. Change" }).click();
  await page.getByRole("radio", { name: "Confidential" }).check();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "Question" }).fill("What did Acme's CIO say?");
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect(page.getByTestId("answer")).toBeVisible();
  expect(log.asks[0]?.classification).toBe("confidential");
  await expect(page.getByText("Question Confidential")).toBeVisible();
});

test("an expired session returns to sign-in and keeps the draft", async ({ page }) => {
  await openAsk(page, { ask: () => ({ status: 401, body: { error: { code: "authentication_required", message: "Sign in to continue.", request_id: "r" } } }) });
  await page.getByRole("textbox", { name: "Question" }).fill("Draft that must survive");
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect(page).toHaveURL(/\/sign-in\?next=%2Fask/);
  await page.getByRole("button", { name: /Maya Okafor/ }).click();
  await expect(page.getByRole("textbox", { name: "Question" })).toHaveValue("Draft that must survive");
});
