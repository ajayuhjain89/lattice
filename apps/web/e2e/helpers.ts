import { expect, type Page } from "@playwright/test";

import type { components } from "../src/api/schema.gen";

import { mockApi, type MockOptions } from "./mock-api";

type Answer = components["schemas"]["Answer"];

export const WIDTHS = [1440, 1280, 1024, 768, 390] as const;

/** Sign in is mocked: /v1/me answers, so the workspace opens directly. */
export async function openAsk(page: Page, options: MockOptions = {}) {
  const log = await mockApi(page, options);
  await page.goto("/ask");
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
  return log;
}

export async function askFor(page: Page, answer: Answer, question = "When will SCIM ship for Acme, and what did we tell them?", options: MockOptions = {}) {
  const log = await openAsk(page, { ask: () => answer, ...options });
  await page.getByRole("textbox", { name: "Question" }).fill(question);
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect(page.getByTestId("answer")).toBeVisible();
  return log;
}

/** No horizontal page scroll at the current viewport. */
export async function expectNoHorizontalOverflow(page: Page) {
  const { overflow, culprits } = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const culprits = [...document.querySelectorAll("body *")]
      .filter((el) => el.getBoundingClientRect().right > width + 0.5 && !el.closest("[data-radix-popper-content-wrapper]"))
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} → ${Math.round(el.getBoundingClientRect().right)}`);
    return { overflow: document.documentElement.scrollWidth - width, culprits };
  });
  expect(overflow, `horizontal page overflow (px): ${culprits.join(" | ")}`).toBeLessThanOrEqual(0);
}
