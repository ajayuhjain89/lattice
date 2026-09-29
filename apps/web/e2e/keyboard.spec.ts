import { expect, test } from "@playwright/test";

import { ANSWERED, EV } from "../src/test/fixtures";

import { askFor, openAsk } from "./helpers";

test("keyboard only: skip link, ask, open a citation, step with ], close with Esc, focus returns", async ({ page }) => {
  await openAsk(page, { ask: () => ANSWERED });
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("/");
  await expect(page.getByRole("textbox", { name: "Question" })).toBeFocused();
  await page.keyboard.type("When will SCIM ship for Acme?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("answer-prose")).toBeVisible();

  const chip = page.getByRole("button", { name: /^Citation 1:/ }).first();
  await chip.focus();
  await expect(chip).toBeFocused();
  await page.keyboard.press("Enter");
  const inspector = page.getByTestId("evidence-inspector");
  await expect(inspector).toBeFocused();
  await expect(inspector.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();

  await page.keyboard.press("]");
  await expect(inspector.getByRole("heading", { name: "Acme kickoff — call notes" })).toBeVisible();
  await page.keyboard.press("[");
  await expect(inspector.getByRole("heading", { name: /Identity roadmap decision/ })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
  await expect(chip).toBeFocused();
});

test("drawer mode (1024 px): focus is trapped and Escape returns focus to the chip", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 });
  await askFor(page, ANSWERED);
  const chip = page.getByRole("button", { name: /^Citation 2:/ }).first();
  await chip.click();
  const dialog = page.getByRole("dialog", { name: "Evidence inspector" });
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(chip).toBeFocused();
});

test("evidence rows are buttons reachable in order after the prose", async ({ page }) => {
  await askFor(page, ANSWERED);
  const row = page.locator(`[data-evidence="${EV.kickoff}"]`);
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("evidence-inspector").getByRole("heading", { name: "Acme kickoff — call notes" })).toBeVisible();
  await expect(row).toHaveAttribute("aria-current", "true");
});

test("scope popovers work from the keyboard and close with Escape", async ({ page }) => {
  await openAsk(page);
  const trigger = page.getByRole("button", { name: "Set dates" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("From")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("From")).toBeHidden();
  await expect(trigger).toBeFocused();
});
