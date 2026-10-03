import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ANSWERED } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * Render failures (D7): an uncaught error while showing a page lands on the Lattice fault
 * state, never the framework's default screen, and shows nothing from the error. The
 * failures are real ones -- an API response the page can't read.
 */
const STACK = /TypeError|ReferenceError|undefined|is not a function|Cannot read|at [A-Za-z]+ \(|\.js:\d+|Application error/;

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

/** An answer whose claims the page can't read: rendering it throws. */
const malformedAnswer = { ask: () => ({ ...ANSWERED, claims: null }) } as unknown as MockOptions;

async function askAndFail(page: Page) {
  await page.goto("/ask");
  const box = page.getByRole("textbox", { name: "Question" });
  await box.fill("When will SCIM ship for Acme?");
  await box.press("Enter");
}

const malformedMe = { me: { id: "x" } } as unknown as MockOptions;

test("a workspace page that fails keeps the shell and shows the fault state", async ({ page }) => {
  page.on("pageerror", () => {}); // the failure is the point
  await mockApi(page, malformedAnswer);
  await askAndFail(page);
  const fault = page.getByTestId("page-fault");
  await expect(fault.getByRole("heading", { level: 1, name: "This page couldn't be shown" })).toBeVisible();
  await expect(fault.getByRole("alert")).toContainText("Error");
  await expect(page.locator("body")).not.toContainText(STACK);
  // The masthead is still there, so the person can go elsewhere.
  await expect(page.getByRole("navigation").getByRole("link", { name: "Ask" }).first()).toBeVisible();
  await expect(fault).toBeFocused();
  await expect(fault.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(fault.getByRole("link", { name: "Go to Ask" })).toHaveAttribute("href", "/ask");
  await audit(page);
});

test("when the workspace frame itself fails, the public frame shows the fault state", async ({ page }) => {
  page.on("pageerror", () => {});
  await mockApi(page, malformedMe);
  await page.goto("/sources");
  const fault = page.getByTestId("page-fault");
  await expect(fault.getByRole("heading", { level: 1, name: "This page couldn't be shown" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Lattice, home" })).toBeVisible();
  await expect(fault.getByRole("link", { name: "Go to the start page" })).toHaveAttribute("href", "/");
  await expect(page.locator("body")).not.toContainText(STACK);
  await audit(page);
});

test("Try again reloads the page", async ({ page }) => {
  page.on("pageerror", () => {});
  await mockApi(page, malformedAnswer);
  await askAndFail(page);
  await expect(page.getByTestId("page-fault")).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
  await expect(page.getByTestId("page-fault")).toHaveCount(0);
});

test("the fault state fits a phone and 200 % zoom", async ({ page }) => {
  page.on("pageerror", () => {});
  await mockApi(page, malformedAnswer);
  for (const [width, height] of [
    [390, 844],
    [320, 640],
    [640, 450],
  ] as const) {
    await page.setViewportSize({ width, height });
    await askAndFail(page);
    await expect(page.getByTestId("page-fault")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
});
