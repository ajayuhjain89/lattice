import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { AMBIGUOUS, ANSWERED, EV, EVIDENCE_ONLY, FAILED, ID, INSUFFICIENT, ME_LENA, PARTIAL, SUPERSEDED, TRACE, UNSUPPORTED } from "../src/test/fixtures";

import { askFor, openAsk, settled } from "./helpers";
import { mockApi } from "./mock-api";

async function audit(page: Page) {
  await settled(page);
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
  await expect(page.getByRole("heading", { name: "Operational trace" })).toBeVisible();
  await audit(page);
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  await audit(page);
});

test("evidence page and its not-available state", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/evidence/${EV.decision}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
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

test("sources (member and administrator) and activity", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sources");
  await expect(page.getByRole("table", { name: "Sources you can read from" })).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  await audit(page);
  await page.goto("/activity");
  await expect(page.getByRole("link", { name: /When will SCIM ship for Acme/ })).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  await audit(page);
});

test("sources and activity (administrator)", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/sources");
  await page.getByRole("button", { name: "Show records" }).click();
  await expect(page.getByText("rec_3f9a0c1d2e4b5a67")).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  await audit(page);
  await page.goto("/activity");
  await expect(page.getByRole("table", { name: "Organization activity (operational view)" })).toBeVisible();
  await expect(page.getByRole("link", { name: /When will SCIM ship for Acme/ })).toBeVisible();
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  await audit(page);
});

test("access: members, a member, the change dialog and the audit log", async ({ page }) => {
  // Audited at rest, not mid-fade (the dialog's entrance animation lowers contrast briefly).
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page, { me: ME_LENA });
  await page.goto("/access");
  await expect(page.getByRole("table", { name: "Members and their access" })).toBeVisible();
  await audit(page);
  await page.goto(`/access/members/${ID.maya}`);
  await expect(page.getByTestId("access-summary")).toBeVisible();
  await audit(page);
  await page.getByRole("button", { name: "Change access" }).click();
  await expect(page.getByRole("checkbox", { name: "Kestrel Bank" })).toBeVisible();
  await audit(page);
  await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
  await page.getByRole("button", { name: "Review change" }).click();
  await expect(page.getByText("Maya Okafor will gain access to Kestrel Bank content.")).toBeVisible();
  await audit(page);
  await page.keyboard.press("Escape");
  await page.goto("/access/audit");
  await expect(page.getByText("Assigned Kestrel Bank")).toBeVisible();
  await audit(page);
});

test("access (not an administrator)", async ({ page }) => {
  await mockApi(page);
  await page.goto("/access");
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
  await audit(page);
});
