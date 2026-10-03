import { expect, test } from "@playwright/test";

import { ID, ME_LENA, MEMBER_MAYA, PREVIEW_SUSPEND } from "../src/test/fixtures";

import { expectNoHorizontalOverflow, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

const MAYA = `/access/members/${ID.maya}`;

test("an administrator reviews a member's access from the Access page", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Access" }).click();
  await expect(page.getByRole("heading", { name: "Access", level: 1 })).toBeVisible();
  const table = page.getByRole("table", { name: "Members and their access" });
  await expect(table.getByRole("row")).toHaveCount(4);
  await table.getByRole("link", { name: "Maya Okafor" }).click();
  await expect(page).toHaveURL(MAYA);
  await expect(page.getByTestId("access-summary")).toContainText(
    "Maya Okafor can see Acme Corp and Northpeak Logistics content (assigned accounts), up to Internal sensitivity",
  );
  await expect(page.getByTestId("access-summary")).toContainText("is also in private channels you aren't in");
  await expect(page.locator("main")).not.toContainText("rec_");
});

test("a change is previewed, given a reason and applied against the reviewed version", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto(MAYA);
  await page.getByRole("button", { name: "Change access" }).click();
  const dialog = page.getByRole("dialog", { name: "Change Maya Okafor's access" });
  await dialog.getByRole("checkbox", { name: "Kestrel Bank" }).check();
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByText("Maya Okafor will gain access to Kestrel Bank content.")).toBeVisible();
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(dialog.getByText("Give a reason for this change.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Covering Kestrel during the renewal");
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(dialog).toBeHidden();
  expect(log.changes).toEqual([
    {
      accounts: [ID.acme, ID.northpeak, ID.kestrel],
      version: MEMBER_MAYA.version,
      reason: "Covering Kestrel during the renewal",
    },
  ]);
  // Focus returns to the control that opened the dialog.
  await expect(page.getByRole("button", { name: "Change access" })).toBeFocused();
});

test("a stale view is refused and says so", async ({ page }) => {
  await mockApi(page, {
    me: ME_LENA,
    preview: PREVIEW_SUSPEND,
    change: () => ({ status: 409, body: { error: { code: "conflict", message: "This changed since you opened it.", request_id: "r" } } }),
  });
  await page.goto(MAYA);
  await page.getByRole("button", { name: "Suspend" }).click();
  const dialog = page.getByRole("dialog", { name: "Suspend Maya Okafor" });
  await expect(dialog.getByText("Maya Okafor will immediately lose access to everything in Lattice.")).toBeVisible();
  await expect(dialog.getByText("2", { exact: true })).toBeVisible();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Left the account team");
  await dialog.getByRole("button", { name: "Suspend" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Someone changed Maya Okafor's access after you opened this");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Suspend" })).toBeFocused();
});

test("grants: explain, grant and revoke documents the administrator can read", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA });
  await page.goto(MAYA);
  await page.getByRole("button", { name: "Grant a document" }).click();
  const dialog = page.getByRole("dialog", { name: "Grant Maya Okafor a document" });
  await dialog.getByRole("searchbox", { name: "Document to grant" }).fill("handbook");
  await dialog.getByRole("radio", { name: /Employee handbook/ }).check();
  await expect(dialog.getByText("Maya Okafor can't read “Employee handbook”")).toBeVisible();
  // Only the clause that blocks is given as the reason.
  await expect(dialog.getByRole("listitem")).toHaveText(["it's restricted to departments Maya Okafor isn't in"]);
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Onboarding cover");
  await dialog.getByRole("button", { name: "Grant", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(log.grants).toEqual([{ document_id: ID.doc, reason: "Onboarding cover" }]);

  await page.getByRole("button", { name: "Revoke grant: Employee handbook" }).click();
  const confirm = page.getByRole("dialog", { name: "Revoke this grant?" });
  await expect(confirm.getByRole("button", { name: "Revoke" })).toBeDisabled();
  await confirm.getByRole("textbox", { name: "Reason" }).fill("No longer needed");
  await confirm.getByRole("button", { name: "Revoke" }).click();
  await expect(confirm).toBeHidden();
  expect(log.revokes).toEqual([ID.grant]);
});

test("the audit log shows recorded changes, with documents the viewer can read", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/access/audit");
  const events = page.getByRole("listitem").filter({ hasText: "By " });
  // Two changes by people and tools, two by the identity provider (SCIM, Phase 10).
  await expect(events).toHaveCount(4);
  await expect(events.nth(0)).toContainText("Access changed");
  await expect(events.nth(0)).toContainText("Assigned Kestrel Bank");
  await expect(events.nth(0)).toContainText("“Covering Kestrel during the renewal”");
  await expect(events.nth(1)).toContainText("Document granted");
  await expect(events.nth(1)).toContainText("Employee handbook");
  await expect(events.nth(1)).toContainText("By admin-grants.yaml");
  await expect(page.locator("main")).not.toContainText("rec_");
});

test("members who aren't administrators see one page, and the API is never asked", async ({ page }) => {
  const log = await mockApi(page);
  await page.goto(MAYA);
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Access" })).toHaveCount(0);
  expect(log.changes).toEqual([]);
});

test("a revoked capability shows the same page", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, access: "forbidden" });
  await page.goto(MAYA);
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
  await page.goto("/access");
  await expect(page.getByRole("heading", { name: "You need administrator access to view this page" })).toBeVisible();
});

test("the Ask account picker lists only the member's accounts", async ({ page }) => {
  const log = await mockApi(page, { ask: () => ({ status: 422, body: { error: { code: "validation_failed", message: "The request is invalid.", request_id: "r" } } }) });
  await page.goto("/ask");
  await expect(page.getByTestId("your-reach")).toContainText("Acme Corp, Northpeak Logistics");
  await page.getByRole("button", { name: "Account: any account. Change" }).click();
  await expect(page.getByRole("radio")).toHaveCount(2);
  await page.getByRole("radio", { name: "Northpeak Logistics" }).check();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "Question" }).fill("What changed for Northpeak?");
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect.poll(() => log.asks.map((a) => a.scope?.account)).toEqual(["northpeak-logistics"]);
});

for (const width of WIDTHS) {
  test(`access pages fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { me: ME_LENA });
    for (const path of ["/access", MAYA, "/access/audit"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
      await expectNoHorizontalOverflow(page);
    }
  });
}
