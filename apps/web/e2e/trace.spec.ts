import { expect, test } from "@playwright/test";

import { EV, ME_LENA, TRACE } from "../src/test/fixtures";

import { mockApi } from "./mock-api";

test("asker trace: question, pipeline, recorded answer, exhibits", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "When will SCIM ship for Acme, and what did we tell them?", level: 1 })).toBeVisible();
  await expect(page.locator("main")).toContainText("Answered");
  const stages = page.getByRole("region", { name: /^Pipeline/ }).getByRole("listitem");
  await expect(stages.first()).toContainText("Retrieval");
  await expect(page.getByText("quote_not_found")).toBeVisible();
  await expect(page.getByText("ollama · qwen3.5:4b").first()).toBeVisible();
  await page.getByRole("button", { name: /^Inspect evidence 1: DR-112/ }).click();
  await expect(page.getByTestId("evidence-inspector").getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
  expect(log.evidence).toContain(EV.decision);
});

test("administrator view of another member's trace: operational only", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto(`/traces/${TRACE.other}`);
  await expect(page.getByRole("heading", { name: "Operational trace", level: 1 })).toBeVisible();
  await expect(page.getByText(/Question, answer and evidence aren't visible to administrators/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Question" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Exhibits|Answer as recorded/ })).toHaveCount(0);
  expect(log.evidence).toEqual([]);
});
