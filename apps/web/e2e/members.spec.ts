import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  AUDIT_MEMBER_ADDED,
  FIXTURE_NOW,
  ID,
  IDENTITY_NOT_CONNECTED,
  IDENTITY_REQUIREMENT_EMAIL,
  ME_LENA,
  MEMBER_GRACE_ADMIN,
  MEMBER_LENA_SELF,
  MEMBER_MAYA,
  MEMBER_NEW,
  MEMBER_RAE_REMOVED,
  PREVIEW_SUSPEND,
} from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated, settled } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * Members and administrators in the product (Phase 12, P12.2): adding a member bound to
 * their exact identity-provider identifier, the administrator capability (Now → After,
 * content access unchanged), the last-administrator and own-membership refusals,
 * suspension and reinstatement with least access -- against the mocked API, whose
 * `lifecycle` decides every action offered.
 */

const GRACE = MEMBER_GRACE_ADMIN.member.id;
const RAE = MEMBER_RAE_REMOVED.member.id;
const MEMBERS = {
  [ID.maya]: MEMBER_MAYA,
  [GRACE]: MEMBER_GRACE_ADMIN,
  [RAE]: MEMBER_RAE_REMOVED,
  [ID.lena]: MEMBER_LENA_SELF,
};

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

async function openAccess(page: Page, options: MockOptions = {}) {
  const log = await mockApi(page, { me: ME_LENA, member: MEMBERS, ...options });
  await page.goto("/access");
  await expect(page.getByRole("table", { name: "Members and their access" })).toBeVisible();
  return log;
}

async function openAdd(page: Page) {
  const button = page.getByRole("button", { name: "Add member" });
  await hydrated(button);
  await button.click();
  return page.getByRole("dialog", { name: "Add a member" });
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

/* --- add a member ------------------------------------------------------------------------ */

test("add member: the trust model in plain words, validation, then the new member's page", async ({ page }) => {
  const log = await openAccess(page);
  const dialog = await openAdd(page);
  await expect(dialog).toContainText("this is not an invitation");
  await expect(dialog).toContainText("which must send exactly the identifier below for them. Lattice never matches people by e-mail.");
  await expect(dialog).toContainText("They start with least access");
  await expect(dialog).toContainText("You give them access on their page afterwards.");
  const identifier = dialog.getByRole("textbox", { name: "Identifier from your identity provider" });
  await expect(identifier).toHaveAccessibleDescription(/The exact value halden\.okta\.example sends in the sub claim/);

  // Every problem is said at once; focus goes to the first.
  await dialog.getByRole("button", { name: "Add member" }).click();
  await expect(dialog.getByText("Enter their name.")).toBeVisible();
  await expect(dialog.getByText("Enter their e-mail address.")).toBeVisible();
  await expect(dialog.getByText(/Enter the value your identity provider sends in the “sub” claim/)).toBeVisible();
  await expect(dialog.getByText("Say why: the reason is recorded in the audit log.")).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeFocused();
  expect(log.added).toEqual([]);

  await dialog.getByRole("textbox", { name: "Name" }).fill("Sam Achebe");
  await dialog.getByRole("textbox", { name: "E-mail" }).fill("sam.achebe@halden.example");
  await identifier.fill("  00u9sam-achebe ");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Joined the support team");
  await dialog.getByRole("button", { name: "Add member" }).click();
  await expect(page).toHaveURL(`/access/members/${MEMBER_NEW.member.id}`);
  expect(log.added).toEqual([{ name: "Sam Achebe", email: "sam.achebe@halden.example", identifier: "00u9sam-achebe", reason: "Joined the support team" }]);
  // Least access, bound to the exact identifier.
  await expect(page.getByRole("heading", { level: 1, name: "Sam Achebe" })).toBeVisible();
  await expect(page.getByTestId("sign-in-identifier")).toHaveText("Signs in as 00u9sam-achebe");
  await expect(page.getByTestId("access-summary")).toContainText("Sam Achebe can see content that isn't tied to an account, up to Internal sensitivity.");
  await expect(page.getByRole("button", { name: "Make administrator" })).toBeVisible();
});

test("add member: an email claim is still an exact identifier", async ({ page }) => {
  await openAccess(page, { identity: IDENTITY_REQUIREMENT_EMAIL });
  const dialog = await openAdd(page);
  await expect(dialog.getByRole("textbox", { name: "Identifier from your identity provider" })).toHaveAccessibleDescription(
    /it's compared exactly, and it can differ from the e-mail above/,
  );
});

test("add member: a taken identifier is marked on its field and focused", async ({ page }) => {
  const log = await openAccess(page, {
    addMember: () => ({
      status: 409,
      body: { error: { code: "conflict", message: "Taken.", request_id: "r", details: [{ field: "identifier", issue: "taken" }] } },
    }),
  });
  const dialog = await openAdd(page);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Sam Achebe");
  await dialog.getByRole("textbox", { name: "E-mail" }).fill("sam.achebe@halden.example");
  await dialog.getByRole("textbox", { name: "Identifier from your identity provider" }).fill("00u1lena-brandt");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Joined");
  await dialog.getByRole("button", { name: "Add member" }).click();
  const identifier = dialog.getByRole("textbox", { name: "Identifier from your identity provider" });
  await expect(identifier).toBeFocused();
  await expect(identifier).toHaveAttribute("aria-invalid", "true");
  await expect(dialog.getByText("A member of this organization already has this identifier.")).toBeVisible();
  // Nothing about any other organization: the sentence is the same for every conflict.
  await expect(dialog).not.toContainText(/another organization|veridian/i);
  expect(log.added).toHaveLength(1);
});

test("add member: loading, blocked without an identity provider, and refusals", async ({ page }) => {
  await openAccess(page, { identityDelay: 600 });
  let dialog = await openAdd(page);
  await expect(dialog.getByRole("status").filter({ hasText: "Checking your identity provider connection…" })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await openAccess(page, { identity: IDENTITY_NOT_CONNECTED });
  dialog = await openAdd(page);
  await expect(dialog.getByRole("heading", { name: "Connect an identity provider first" })).toBeVisible();
  await expect(dialog.getByRole("textbox")).toHaveCount(0);
  await page.keyboard.press("Escape");

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await openAccess(page, {
    addMember: () => ({ status: 403, body: { error: { code: "permission_denied", message: "No.", request_id: "r" } } }),
  });
  dialog = await openAdd(page);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Sam");
  await dialog.getByRole("textbox", { name: "E-mail" }).fill("sam@halden.example");
  await dialog.getByRole("textbox", { name: "Identifier from your identity provider" }).fill("00u9sam");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Joined");
  await dialog.getByRole("button", { name: "Add member" }).click();
  await expect(dialog.getByRole("alert")).toContainText("You no longer have administrator access.");
});

test("add member: keyboard only, and focus returns to Add member", async ({ page }) => {
  await openAccess(page);
  const button = page.getByRole("button", { name: "Add member" });
  await hydrated(button);
  await button.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Add a member" });
  await expect(dialog.getByRole("textbox", { name: "Name" })).toBeVisible();
  // Tab moves through the fields in reading order.
  await dialog.getByRole("textbox", { name: "Name" }).focus();
  await page.keyboard.type("Sam Achebe");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: "E-mail" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: "Identifier from your identity provider" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: "Reason" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(button).toBeFocused();
});

test("the audit log records who was added, with least access and the identifier they were bound to", async ({ page }) => {
  await mockApi(page, { me: ME_LENA, audit: AUDIT_MEMBER_ADDED });
  await page.goto("/access/audit");
  const entry = page.getByTestId("audit-entry");
  await expect(entry).toHaveCount(1);
  await expect(entry).toContainText("Added by an administrator, with least access");
  await expect(entry).toContainText("Sign in identifier");
  await expect(entry).toContainText("00u9sam-achebe");
  await expect(entry).toContainText("“Joined the support team”");
});

/* --- the administrator capability ---------------------------------------------------------- */

test("grant administrator: Now → After, content unchanged, a reason, applied and audited", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA, member: MEMBERS });
  await page.goto(`/access/members/${ID.maya}`);
  const grant = page.getByRole("button", { name: "Make administrator" });
  await hydrated(grant);
  await grant.click();
  const dialog = page.getByRole("dialog", { name: "Make Maya Okafor an administrator" });
  const row = dialog.getByRole("row", { name: /Administrator/ });
  await expect(row).toContainText("Not an administrator");
  await expect(row).toContainText("Gains");
  await expect(dialog.getByTestId("content-unchanged")).toContainText("Content access doesn't change.");
  // No typed confirmation: a reason is the whole ceremony.
  await expect(dialog.getByRole("textbox")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Make administrator" }).click();
  await expect(dialog.getByText("Give a reason for this change.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Second administrator for cover");
  await dialog.getByRole("button", { name: "Make administrator" }).click();
  await expect(dialog).toBeHidden();
  expect(log.administrators).toEqual([{ id: ID.maya, body: { administrator: true, version: MEMBER_MAYA.version, reason: "Second administrator for cover" } }]);
  await expect(page.getByTestId("applied-change")).toContainText("Maya Okafor is an administrator");
  await expect(page.getByTestId("applied-change")).toContainText("Content access unchanged.");
  // The same control, now the reverse action, keeps focus.
  await expect(page.getByRole("button", { name: "Remove administrator capability" })).toBeFocused();
});

test("revoke administrator: the last-administrator refusal is explained", async ({ page }) => {
  await mockApi(page, {
    me: ME_LENA,
    member: MEMBERS,
    administrator: () => ({
      status: 422,
      body: { error: { code: "validation_failed", message: "Invalid.", request_id: "r", details: [{ field: "membership", issue: "last_administrator" }] } },
    }),
  });
  await page.goto(`/access/members/${GRACE}`);
  const revoke = page.getByRole("button", { name: "Remove administrator capability" });
  await hydrated(revoke);
  await revoke.click();
  const dialog = page.getByRole("dialog", { name: "Remove Grace Whitfield's administrator capability" });
  await expect(dialog.getByRole("row", { name: /Administrator/ })).toContainText("Loses");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Rotation");
  await dialog.getByRole("button", { name: "Remove capability" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Grace Whitfield is the only active administrator left");
});

test("a backend block replaces the action: the last active administrator", async ({ page }) => {
  const sole = {
    ...MEMBER_GRACE_ADMIN,
    lifecycle: {
      ...MEMBER_GRACE_ADMIN.lifecycle,
      revoke_administrator: { available: false, blocked_by: "last_administrator" as const },
      suspend: { available: false, blocked_by: "last_administrator" as const },
    },
  };
  await mockApi(page, { me: ME_LENA, member: { [GRACE]: sole } });
  await page.goto(`/access/members/${GRACE}`);
  await expect(page.getByTestId("blocked-last_administrator")).toHaveText(
    "Grace Whitfield is the only active administrator. Make another member an administrator first.",
  );
  await expect(page.locator("main").getByRole("button", { name: /Remove administrator capability|Suspend/ })).toHaveCount(0);
});

test("your own membership offers no lifecycle or capability change", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA, member: MEMBERS });
  await page.goto(`/access/members/${ID.lena}`);
  await expect(page.getByTestId("no-change-note")).toHaveText("This is you. Another administrator must change your access or administrator capability.");
  await expect(page.locator("main").getByRole("button", { name: /Change access|Suspend|administrator|Reinstate/ })).toHaveCount(0);
  expect(log.administrators).toEqual([]);
});

/* --- suspension and reinstatement --------------------------------------------------------- */

test("suspend: the last-administrator refusal from the server is explained", async ({ page }) => {
  await mockApi(page, {
    me: ME_LENA,
    member: MEMBERS,
    preview: PREVIEW_SUSPEND,
    change: () => ({
      status: 422,
      body: { error: { code: "validation_failed", message: "Invalid.", request_id: "r", details: [{ field: "membership", issue: "last_administrator" }] } },
    }),
  });
  await page.goto(`/access/members/${GRACE}`);
  await page.getByRole("button", { name: "Suspend" }).click();
  const dialog = page.getByRole("dialog", { name: "Suspend Grace Whitfield" });
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Leave");
  await dialog.getByRole("button", { name: "Suspend" }).click();
  await expect(dialog.getByRole("alert")).toContainText("is the only active administrator left");
});

test("reinstate: least access, nothing restored, a reason; focus lands on the member", async ({ page }) => {
  const log = await mockApi(page, { me: ME_LENA, member: MEMBERS });
  await page.goto(`/access/members/${RAE}`);
  await expect(page.getByTestId("no-change-note")).toContainText(
    "Reinstating makes them active again on the same sign-in identity, starting from least access.",
  );
  await expect(page.getByRole("button", { name: "Change access" })).toHaveCount(0);
  const reinstate = page.getByRole("button", { name: "Reinstate" });
  await hydrated(reinstate);
  await reinstate.click();
  const dialog = page.getByRole("dialog", { name: "Reinstate Rae Lindqvist" });
  await expect(dialog).toContainText("what they had before removal is shown below and is not restored");
  await expect(dialog).toContainText("Their earlier sessions stay ended.");
  const compare = dialog.getByRole("table", { name: "Now and after this change, rule by rule" });
  await expect(compare.getByRole("row", { name: /Status/ })).toContainText("Removed");
  await expect(compare.getByRole("row", { name: /Clearance/ })).toContainText("Loses");
  await expect(compare.getByRole("row", { name: /Departments/ })).toContainText("Sales (removed)");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Re-hired");
  await dialog.getByRole("button", { name: "Reinstate" }).click();
  await expect(dialog).toBeHidden();
  expect(log.reinstates).toEqual([{ id: RAE, body: { version: MEMBER_RAE_REMOVED.version, reason: "Re-hired" } }]);
  await expect(page.getByTestId("applied-change")).toContainText("Rae Lindqvist reinstated");
  await expect(page.getByTestId("access-summary")).toContainText("up to Internal sensitivity");
  // The Reinstate button is gone; focus is on the member's heading, not lost.
  await expect(page.getByRole("heading", { level: 1, name: "Rae Lindqvist" })).toBeFocused();
});

test("reinstate: blocked when the identity can't be the same one", async ({ page }) => {
  const replaced = {
    ...MEMBER_RAE_REMOVED,
    lifecycle: { ...MEMBER_RAE_REMOVED.lifecycle, reinstate: { available: false, blocked_by: "identity_not_current" as const } },
  };
  await mockApi(page, { me: ME_LENA, member: { [RAE]: replaced } });
  await page.goto(`/access/members/${RAE}`);
  await expect(page.getByTestId("blocked-identity_not_current")).toContainText("an identity provider connection that's no longer active");
  await expect(page.getByRole("button", { name: "Reinstate" })).toHaveCount(0);
});

/* --- accessibility and reflow ---------------------------------------------------------------- */

test("axe passes on the add, administrator and reinstate dialogs", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openAccess(page);
  const dialog = await openAdd(page);
  await dialog.getByRole("button", { name: "Add member" }).click();
  await expect(dialog.getByText("Enter their name.")).toBeVisible();
  await audit(page);
  await page.keyboard.press("Escape");
  await page.goto(`/access/members/${ID.maya}`);
  await page.getByRole("button", { name: "Make administrator" }).click();
  await expect(page.getByTestId("content-unchanged")).toBeVisible();
  await audit(page);
  await page.goto(`/access/members/${RAE}`);
  await page.getByRole("button", { name: "Reinstate" }).click();
  await expect(page.getByRole("table", { name: "Now and after this change, rule by rule" })).toBeVisible();
  await audit(page);
});

for (const [label, width, height] of [
  ["320 px", 320, 640],
  ["200 % zoom (640 CSS px)", 640, 450],
] as const) {
  test(`${label}: the add-member and reinstate dialogs reflow without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openAccess(page);
    await expectNoHorizontalOverflow(page);
    const dialog = await openAdd(page);
    await dialog.getByRole("button", { name: "Add member" }).click();
    await expect(dialog.getByText("Enter their name.")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const box = await dialog.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 0.5).toBe(true);
    await page.keyboard.press("Escape");
    await page.goto(`/access/members/${RAE}`);
    await page.getByRole("button", { name: "Reinstate" }).click();
    await expect(page.getByRole("table", { name: "Now and after this change, rule by rule" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

test("forced colours and reduced motion: the trust note keeps its edge, nothing animates", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await openAccess(page);
  const dialog = await openAdd(page);
  const trust = dialog.getByText(/which must send exactly the identifier below/).locator("xpath=..");
  await expect(trust).toHaveCSS("border-left-style", "solid");
  expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length)).toBe(0);
  await page.keyboard.press("Escape");
  await page.goto(`/access/members/${ID.maya}`);
  await page.getByRole("button", { name: "Make administrator" }).click();
  await expect(page.getByTestId("content-unchanged")).toHaveCSS("border-left-style", "solid");
});
