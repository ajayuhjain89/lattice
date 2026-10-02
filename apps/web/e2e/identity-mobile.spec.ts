import { expect, test } from "@playwright/test";

import { ME_LENA, MEMBER_PAT_MANAGED } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, openAsk } from "./helpers";
import { mockApi } from "./mock-api";

test("sign-in errors fit a phone", async ({ page }) => {
  await mockApi(page);
  await page.goto("/sign-in?error=sso_failed");
  await expect(page.getByRole("alert").filter({ hasText: "Sign-in didn't complete." })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("the rate-limit callout fits a phone", async ({ page }) => {
  await openAsk(page, {
    ask: () => ({
      status: 429,
      body: { error: { code: "rate_limited", message: "Too many requests.", request_id: "r" } },
      headers: { "retry-after": "12" },
    }),
  });
  const box = page.getByRole("textbox", { name: "Question" });
  await box.fill("What changed?");
  await box.press("Enter");
  await expect(page.getByRole("alert").filter({ hasText: "Try again in 12 seconds" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("a provisioned member's page fits a phone", async ({ page }) => {
  const id = MEMBER_PAT_MANAGED.member.id;
  await mockApi(page, { me: ME_LENA, member: { [id]: MEMBER_PAT_MANAGED } });
  await page.goto(`/access/members/${id}`);
  await expect(page.getByTestId("managed-note")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
