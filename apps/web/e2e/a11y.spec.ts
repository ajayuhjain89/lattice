import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { AMBIGUOUS, ANSWERED, EV, EVIDENCE_ONLY, FAILED, INSUFFICIENT, ME_LENA, PARTIAL, SUPERSEDED, TRACE, UNSUPPORTED } from "../src/test/fixtures";

import { askFor, openAsk } from "./helpers";
import { mockApi } from "./mock-api";

async function audit(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(summary).toEqual([]);
}

test("sign-in", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sign-in");
  await audit(page);
});

test("ask intro", async ({ page }) => {
  await openAsk(page);
  await audit(page);
});

for (const [name, answer] of Object.entries({ ANSWERED, PARTIAL, INSUFFICIENT, EVIDENCE_ONLY, AMBIGUOUS, UNSUPPORTED, FAILED, SUPERSEDED })) {
  test(`answer state ${name}`, async ({ page }) => {
    await askFor(page, answer);
    await audit(page);
  });
}

test("answer with the docked inspector open", async ({ page }) => {
  await askFor(page, ANSWERED);
  await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
  await expect(page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 })).toBeVisible();
  await audit(page);
});

test("trace (asker) and trace (operational)", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto(`/traces/${TRACE.other}`);
  await expect(page.getByText("Operational view")).toBeVisible();
  await audit(page);
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  await audit(page);
});

test("evidence page and its not-available state", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/evidence/${EV.decision}`);
  await expect(page.getByRole("heading", { level: 2 })).toBeVisible();
  await audit(page);
  await page.goto(`/evidence/${EV.missing}`);
  await expect(page.getByRole("heading", { name: "This evidence isn't available" })).toBeVisible();
  await audit(page);
});

test("provider settings", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/settings/providers");
  await expect(page.getByRole("table")).toBeVisible();
  await audit(page);
});
