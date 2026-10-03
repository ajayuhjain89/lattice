import { expect, test } from "@playwright/test";

import { ME_LENA, PROVIDERS, PROVIDERS_MANY } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, openAsk, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

test("administrators see the read-only provider settings", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "AI providers" }).click();
  await expect(page.getByRole("heading", { name: "AI providers", level: 1 })).toBeVisible();
  await expect(page.getByText("Read-only here.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Recorded AI providers (read-only)" })).toContainText("gemini-3.5-flash-lite");
  // The only buttons are the lattice's cells, which explain; nothing edits.
  const lattice = page.getByRole("table", { name: "Which data levels each AI provider may receive" });
  await expect(page.locator("main").getByRole("button")).toHaveCount(await lattice.getByRole("button").count());
  await expect(page.locator("main input, main select, main textarea")).toHaveCount(0);
});

test("members have no admin navigation and get the permission page", async ({ page }) => {
  await openAsk(page, { providers: "forbidden" });
  await expect(page.getByRole("link", { name: "AI providers" })).toHaveCount(0);
  await page.goto("/settings/providers");
  await expect(page.getByRole("heading", { name: /You need administrator access/ })).toBeVisible();
});

test("the egress lattice is one Tab stop, walked with arrows, every cell named", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/settings/providers");
  const lattice = page.getByRole("table", { name: "Which data levels each AI provider may receive" });
  await expect(lattice.getByRole("button")).toHaveCount(9);
  await expect(lattice.locator("button[tabindex='0']")).toHaveCount(1);
  await lattice.locator("button[tabindex='0']").focus();
  await expect(page.getByTestId("egress-why")).toHaveText(/^Internal data may be sent to ollama/);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(":focus")).toHaveAccessibleName("Restricted data isn't sent to ollama · qwen3.5:4b: it's approved only up to Confidential.");
  await page.keyboard.press("End");
  await expect(page.locator(":focus")).toHaveAccessibleName(/^Restricted data may be sent to local/);
  // Enter pins the explanation; it stays after focus leaves.
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("egress-why")).toHaveText(/^Restricted data may be sent to local/);
});

test("boundary cells are hatched with a dashed edge; filled cells are solid -- not colour alone", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, providers: PROVIDERS });
  await page.goto("/settings/providers");
  const lattice = page.getByRole("table", { name: "Which data levels each AI provider may receive" });
  const kept = lattice.getByRole("button", { name: /^Restricted data isn't sent to ollama/ });
  const sent = lattice.getByRole("button", { name: /^Internal data may be sent to ollama/ });
  await expect(kept).toHaveCSS("border-top-style", "dashed");
  expect(await kept.evaluate((el) => getComputedStyle(el).backgroundImage)).toContain("repeating-linear-gradient");
  await expect(sent).toHaveCSS("border-top-style", "solid");
  await page.emulateMedia({ forcedColors: "active" });
  await expect(kept).toHaveCSS("border-top-style", "dashed");
  expect(await kept.evaluate((el) => getComputedStyle(el).backgroundImage)).toBe("none");
});

for (const width of [...WIDTHS, 320]) {
  test(`providers fit at ${width}px with eight providers; a wide lattice scrolls in a named region`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA, providers: PROVIDERS_MANY });
    await page.goto("/settings/providers");
    const lattice = page.getByRole("table", { name: "Which data levels each AI provider may receive" });
    await expect(lattice.getByRole("button")).toHaveCount(24);
    await lattice.getByRole("button", { name: /^Restricted data isn't sent to voyage/ }).focus();
    await expectNoHorizontalOverflow(page);
    if (width < 1024) await expect(page.getByRole("region", { name: "Which data levels each AI provider may receive" })).toBeVisible();
  });
}
