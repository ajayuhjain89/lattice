import { expect, test } from "@playwright/test";

import { EV, ME_LENA, TRACE } from "../src/test/fixtures";

import { mockApi } from "./mock-api";

test("asker trace: question, pipeline, recorded answer, evidence references", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto(`/traces/${TRACE.answered}`);
  await expect(page.getByRole("heading", { name: "Answered", level: 1 })).toBeVisible();
  await expect(page.getByText("When will SCIM ship for Acme, and what did we tell them?")).toBeVisible();
  const stages = page.getByRole("heading", { name: "Pipeline", exact: true }).locator("xpath=../..").getByRole("listitem");
  await expect(stages.first()).toContainText("Retrieval");
  await expect(page.getByText("quote_not_found")).toBeVisible();
  await expect(page.getByText("ollama · qwen3.5:4b").first()).toBeVisible();
  await page.getByRole("button", { name: "Inspect evidence 1" }).click();
  await expect(page.getByTestId("evidence-inspector").getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();
  expect(log.evidence).toContain(EV.decision);
});

test("administrator view of another member's trace: operational only", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto(`/traces/${TRACE.other}`);
  await expect(page.getByText("Operational view")).toBeVisible();
  await expect(page.getByText(/Content of other members' questions isn't visible/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Question" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Evidence references" })).toHaveCount(0);
});
