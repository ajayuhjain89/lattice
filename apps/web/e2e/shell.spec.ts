/**
 * D3 application shell: the masthead (≥768 px), the phone bars (<768 px), Jump, the
 * account menu, focus after navigation, and the canvas geometry at the review widths.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ANSWERED, ME_LENA, ME_MAYA, TRACE } from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow, jumpChord, openAsk, settled, WIDTHS } from "./helpers";
import { mockApi } from "./mock-api";

const LONG_ORG = "Halden Logistics & Northern Freight Holdings International (Europe) Ltd";
const LENA_LONG = { ...ME_LENA, organization: { ...ME_LENA.organization, name: LONG_ORG } };

const box = async (page: Page, selector: string) => {
  const b = await page.locator(selector).first().boundingBox();
  if (!b) throw new Error(`no box for ${selector}`);
  return b;
};

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

/** Every request the page made to an administrator endpoint. */
function adminRequests(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith("/api/v1/admin")) seen.push(path);
  });
  return seen;
}

const primary = (page: Page) => page.getByRole("navigation", { name: "Primary" });
const main = (page: Page) => page.locator("main#main");

test.describe("masthead", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("replaces the sidebar: banner, Primary navigation and main are the landmarks", async ({ page }) => {
    await openAsk(page);
    const banner = page.getByRole("banner");
    await expect(banner).toHaveCount(1);
    await expect(banner.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByRole("main")).toHaveCount(1);
    // Nothing of the v1 shell: no rail, sheet trigger, session history or page-header crumb.
    await expect(page.getByRole("button", { name: "Open navigation" })).toHaveCount(0);
    await expect(page.getByText("This session")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveCount(0);
    // The page begins under the masthead and spans the full width (no column reserved).
    const masthead = await box(page, "header[data-shell-nav]");
    expect(masthead.height).toBe(52); // the ink rule included
    const canvas = await box(page, "main#main");
    expect(canvas.x).toBe(0);
    expect(Math.round(canvas.y)).toBe(52);
  });

  test("current page: aria-current on exactly one destination, marked by weight and a rule, not colour alone", async ({ page }) => {
    await mockApi(page, { me: ME_LENA });
    for (const [path, label] of [
      ["/ask", "Ask"],
      ["/activity", "Activity"],
      ["/sources", "Sources"],
      ["/access", "Access"],
      ["/settings/providers", "AI providers"],
      [`/traces/${TRACE.answered}`, "Activity"],
      ["/access/audit", "Access"],
    ] as const) {
      await page.goto(path);
      const current = primary(page).locator("[aria-current='page']");
      await expect(current).toHaveCount(1);
      await expect(current).toHaveText(label);
      const style = await current.evaluate((el) => {
        const after = getComputedStyle(el, "::after");
        return { weight: getComputedStyle(el).fontWeight, rule: after.borderTopWidth, ruleStyle: after.borderTopStyle };
      });
      expect(style).toEqual({ weight: "600", rule: "2px", ruleStyle: "solid" });
    }
  });

  test("sections: members see the workspace only; administrators see a separate Administration group", async ({ page }) => {
    await mockApi(page, { me: ME_MAYA });
    await page.goto("/ask");
    await expect(primary(page).getByRole("list")).toHaveCount(1);
    await expect(primary(page).getByRole("link")).toHaveText(["Ask", "Activity", "Sources"]);
    await expect(page.getByRole("link", { name: "Access" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "AI providers" })).toHaveCount(0);

    await mockApi(page, { me: ME_LENA });
    await page.goto("/ask");
    await expect(primary(page).getByRole("list", { name: "Workspace" }).getByRole("link")).toHaveText(["Ask", "Activity", "Sources"]);
    await expect(primary(page).getByRole("list", { name: "Administration" }).getByRole("link")).toHaveText(["Access", "AI providers"]);
  });

  test("a long organization name truncates in the bar, stays whole in the account menu, and never crowds out destinations", async ({ page }) => {
    await mockApi(page, { me: LENA_LONG });
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto("/sources");
      await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      const row = page.locator("header[data-shell-nav] > div");
      expect(await row.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      // Every destination is either inline or one menu away -- never clipped.
      for (const name of ["Ask", "Activity", "Sources"]) await expect(primary(page).getByRole("link", { name, exact: true })).toBeVisible();
      const org = page.locator("header[data-shell-nav]").getByText(LONG_ORG);
      if (await org.count()) await expect(org).toHaveAttribute("title", LONG_ORG);
    }
    await page.getByRole("button", { name: "Account: Lena Brandt" }).click();
    const menu = page.getByRole("menu", { name: "Account" });
    await expect(menu.getByText(LONG_ORG)).toBeVisible();
    await expect(menu.getByText("lena.brandt@halden.example")).toBeVisible();
    await expect(menu.getByText("IT Administrator")).toBeVisible();
  });

  test("when space runs out, later sections fold into a named menu that carries the current page", async ({ page }) => {
    await mockApi(page, { me: LENA_LONG });
    await page.setViewportSize({ width: 768, height: 700 });
    await page.goto("/access");
    await expect(page.getByRole("heading", { name: "Access", level: 1 })).toBeVisible();
    // Stand-in for longer translated labels: widen every destination, then let the
    // masthead re-fit (it does on any change of width).
    await page.addStyleTag({ content: "nav[aria-label='Primary'] a, nav[aria-label='Primary'] button { letter-spacing: 0.6em; }" });
    await page.setViewportSize({ width: 769, height: 700 });
    await expect.poll(async () => Number(await page.locator("header[data-level]").getAttribute("data-level"))).toBeGreaterThanOrEqual(2);
    await expectNoHorizontalOverflow(page);
    const admin = primary(page).getByRole("button", { name: /^(Admin|Menu)/ });
    await expect(admin).toHaveAccessibleName(/current page: Access/);
    await admin.click();
    const link = page.getByRole("menuitem", { name: "AI providers" });
    await expect(page.getByRole("menuitem", { name: "Access" })).toHaveAttribute("aria-current", "page");
    await link.click();
    await expect(page.getByRole("heading", { name: "AI providers", level: 1 })).toBeVisible();
  });

  test("keyboard: skip link, destinations, Jump and account in order; a destination moves focus into the page", async ({ page }) => {
    await openAsk(page, { me: ME_LENA });
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    for (const name of ["Lattice — Ask", "Ask", "Activity", "Sources", "Access", "AI providers", "Jump", "Account: Lena Brandt"]) {
      await page.keyboard.press("Tab");
      await expect(page.locator(":focus")).toHaveAccessibleName(name);
    }

    await primary(page).getByRole("link", { name: "Sources" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
    await expect(main(page)).toBeFocused();
    await expect(primary(page).getByRole("link", { name: "Sources" })).toHaveAttribute("aria-current", "page");
    // The next Tab continues inside the new page, not back in the masthead.
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest("main")))).toBe(true);

    // The link to the page already open still hands focus to the page.
    await primary(page).getByRole("link", { name: "Sources" }).focus();
    await page.keyboard.press("Enter");
    await expect(main(page)).toBeFocused();
  });

  test("the skip link lands on the page", async ({ page }) => {
    await openAsk(page);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(main(page)).toBeFocused();
  });

  test("account menu: identity from /v1/me, keyboard operable, sign out", async ({ page }) => {
    await openAsk(page);
    const trigger = page.getByRole("button", { name: "Account: Maya Okafor" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "Account" });
    await expect(menu).toContainText("Maya Okafor");
    await expect(menu).toContainText("maya.okafor@halden.example");
    await expect(menu).toContainText("Account Manager");
    await expect(menu).toContainText("Halden");
    await expect(menu.getByRole("menuitem")).toHaveText(["Sign out"]);
    await audit(page);
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(trigger).toBeFocused();
    await trigger.click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
  });

  test("axe: the shell on Ask, Sources and Access, with Jump open", async ({ page }) => {
    await mockApi(page, { me: ME_LENA });
    for (const path of ["/ask", "/sources", "/access"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await audit(page);
    }
    await page.getByRole("button", { name: "Jump" }).click();
    await expect(page.getByRole("combobox", { name: "Jump to" })).toBeFocused();
    await audit(page);
  });
});

test.describe("Jump", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("opens from the shortcut and the masthead control; Escape returns focus to where it was", async ({ page }) => {
    await openAsk(page);
    const composer = page.getByRole("textbox", { name: "Question" });
    await composer.focus();
    await page.keyboard.press(await jumpChord(page));
    const input = page.getByRole("combobox", { name: "Jump to" });
    await expect(input).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(input).toBeHidden();
    await expect(composer).toBeFocused();

    const control = page.getByRole("button", { name: "Jump" });
    await expect(control).toHaveAttribute("aria-keyshortcuts", /^(Meta|Control)\+K$/);
    await control.click();
    await expect(input).toBeFocused();
    // The shortcut toggles it closed again.
    await page.keyboard.press(await jumpChord(page));
    await expect(input).toBeHidden();
    await expect(control).toBeFocused();
  });

  test("the other platform's chord is left alone (Ctrl K keeps its text-editing meaning on a Mac)", async ({ page }) => {
    await openAsk(page);
    const other = (await jumpChord(page)) === "Meta+k" ? "Control+k" : "Meta+k";
    await page.getByRole("textbox", { name: "Question" }).focus();
    await page.keyboard.press(other);
    await expect(page.getByRole("combobox", { name: "Jump to" })).toHaveCount(0);
  });

  test("keyboard selection goes to the page, and focus lands on the page, not back on the opener", async ({ page }) => {
    await openAsk(page);
    await page.keyboard.press(await jumpChord(page));
    const input = page.getByRole("combobox", { name: "Jump to" });
    await input.fill("sour");
    await expect(page.getByRole("option").first()).toHaveText("Ask “sour”");
    await expect(page.getByRole("status").filter({ hasText: /results?/ })).toHaveText(/2 results/);
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("option", { name: "Sources" })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
    await expect(main(page)).toBeFocused();
  });

  test("Ask “…” puts the question in the composer, focused and not sent", async ({ page }) => {
    const log = await mockApi(page);
    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
    await page.keyboard.press(await jumpChord(page));
    await page.getByRole("combobox", { name: "Jump to" }).fill("What did Kestrel agree to?");
    await page.keyboard.press("Enter");
    const composer = page.getByRole("textbox", { name: "Question" });
    await expect(composer).toHaveValue("What did Kestrel agree to?");
    await expect(composer).toBeFocused();
    expect(log.asks).toEqual([]);

    // Already on Ask: the composer takes the new text and keeps focus.
    await page.keyboard.press(await jumpChord(page));
    await page.getByRole("combobox", { name: "Jump to" }).fill("Who owns the Acme renewal?");
    await page.keyboard.press("Enter");
    await expect(composer).toHaveValue("Who owns the Acme renewal?");
    await expect(composer).toBeFocused();
  });

  test("recent questions come from /v1/activity and open their trace; long titles stay on one line", async ({ page }) => {
    await openAsk(page);
    await page.getByRole("button", { name: "Jump" }).click();
    const group = page.getByRole("group", { name: "Your recent questions" });
    const recent = group.getByRole("option", { name: /When will SCIM ship for Acme/ });
    await expect(recent).toBeVisible();
    await expect(recent).toContainText("Answered");
    const label = recent.locator("span[title]");
    expect(await label.evaluate((el) => getComputedStyle(el).textOverflow)).toBe("ellipsis");
    const height = (await recent.boundingBox())!.height;
    expect(height).toBeLessThanOrEqual(40);
    await recent.click();
    await expect(page).toHaveURL(new RegExp(`/traces/${TRACE.answered}`));
    await expect(primary(page).getByRole("link", { name: "Activity" })).toHaveAttribute("aria-current", "page");
  });

  test("no results: says so, and offers only to ask", async ({ page }) => {
    await openAsk(page);
    await page.keyboard.press(await jumpChord(page));
    const input = page.getByRole("combobox", { name: "Jump to" });
    await input.fill("zzqx");
    await expect(page.getByRole("option")).toHaveText(["Ask “zzqx”"]);
    await expect(page.getByRole("status").filter({ hasText: /result/ })).toHaveText(/1 result/);
  });

  test("members never see administration in Jump, and Jump makes no administrator request", async ({ page }) => {
    const admin = adminRequests(page);
    await openAsk(page);
    await page.keyboard.press(await jumpChord(page));
    const input = page.getByRole("combobox", { name: "Jump to" });
    for (const q of ["access", "providers", "audit", "grace", "maya"]) {
      await input.fill(q);
      await page.waitForTimeout(300);
      await expect(page.getByRole("option")).toHaveText([`Ask “${q}”`]);
    }
    await expect(page.getByRole("group", { name: "Members" })).toHaveCount(0);
    expect(admin).toEqual([]);
  });

  test("administrators: admin destinations, the audit log, and members found by the Access search", async ({ page }) => {
    const admin = adminRequests(page);
    await mockApi(page, { me: ME_LENA });
    await page.goto("/ask");
    await page.keyboard.press(await jumpChord(page));
    await expect(page.getByRole("group", { name: "Go to" }).getByRole("option")).toHaveText(["Ask", "Activity", "Sources", "Access", "AI providers", "Organization runs, Activity", "Audit log, Access"]);
    // No member is listed, or requested, before anything is typed.
    expect(admin).toEqual([]);
    const input = page.getByRole("combobox", { name: "Jump to" });
    await input.fill("grace");
    const grace = page.getByRole("group", { name: "Members" }).getByRole("option", { name: /Grace Whitfield/ });
    await expect(grace).toBeVisible();
    expect(admin).toContain("/api/v1/admin/access/members");
    await grace.click();
    await expect(page).toHaveURL(/\/access\/members\//);
    await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Access" })).toBeVisible();
  });

  test("the shortcut is ignored while a modal dialog is open (the inspector sheet at 1024 px)", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    const dialog = page.getByRole("dialog", { name: "Evidence inspector" });
    await expect(dialog).toBeVisible();
    await page.keyboard.press(await jumpChord(page));
    await expect(page.getByRole("combobox", { name: "Jump to" })).toHaveCount(0);
    await expect(dialog).toBeVisible();
  });

  test("reduced motion: Jump appears without animation", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openAsk(page);
    await page.getByRole("button", { name: "Jump" }).click();
    const panel = page.locator("[data-jump]");
    await expect(panel).toBeVisible();
    expect(await panel.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  });
});

test.describe("canvas geometry", () => {
  test.skip(({ isMobile }) => isMobile, "desktop only");

  test("the docked inspector sits under the masthead", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await expect(page.getByTestId("evidence-inspector")).toBeVisible();
    const inspector = await box(page, "aside[aria-label='Evidence inspector']");
    expect(Math.round(inspector.y)).toBe(52);
    expect(Math.round(inspector.y + inspector.height)).toBe(900);
  });

  test("composer, ledger line, claims and exhibits share the record's two edges", async ({ page }) => {
    await askFor(page, ANSWERED);
    const composer = await box(page, "form[aria-label='Ask Lattice']");
    const status = await box(page, "[data-testid='status-line']");
    const heading = await box(page, "#evidence-label");
    const claim = await box(page, "[data-testid='answer-prose'] [data-claim] p");
    const evidence = await box(page, "[data-testid='evidence'] ol");
    // One text edge: the composer, the ledger line, the claims and the exhibits heading.
    for (const b of [status, heading, claim]) expect(Math.abs(b.x - composer.x)).toBeLessThanOrEqual(1);
    // One far edge for the full-width blocks; exhibit rows start at the margin column.
    expect(Math.abs(status.x + status.width - (composer.x + composer.width))).toBeLessThanOrEqual(1);
    expect(Math.abs(evidence.x + evidence.width - (composer.x + composer.width))).toBeLessThanOrEqual(1);
    expect(composer.x - evidence.x).toBeGreaterThanOrEqual(56);
    // Claims keep a reading measure.
    expect(claim.width).toBeLessThanOrEqual(600);
  });

  test("the docked inspector keeps the reading column at least 520 px wide", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await expect(page.getByTestId("evidence-inspector")).toBeVisible();
    const prose = await box(page, "[data-testid='answer-prose']");
    expect(prose.width).toBeGreaterThanOrEqual(520);
    await expectNoHorizontalOverflow(page);
  });

  for (const width of WIDTHS) {
    test(`no horizontal overflow at ${width} px across key screens`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await askFor(page, ANSWERED, undefined, { me: ME_LENA });
      await expectNoHorizontalOverflow(page);
      for (const path of [`/traces/${TRACE.answered}`, "/sources", "/access", "/settings/providers"]) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await expectNoHorizontalOverflow(page);
      }
    });
  }

  test("200 % zoom (1280 px at 200 % = 640 CSS px): the phone navigation takes over, nothing overflows", async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 400 });
    await mockApi(page, { me: ME_LENA });
    await page.goto("/sources");
    await expect(page.getByRole("heading", { name: "Sources", level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expect(primary(page).getByRole("link")).toHaveText(["Ask", "Activity", "Sources"]);
    await page.getByRole("button", { name: /^More/ }).click();
    await expect(page.getByRole("dialog", { name: "More" }).getByRole("link", { name: "AI providers" })).toBeVisible();
  });
});

test.describe("phone", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile only");

  test("top bar and tab bar: identity, current page, 44 px targets, no overlap with the page", async ({ page }) => {
    await askFor(page, ANSWERED);
    await expectNoHorizontalOverflow(page);
    const top = page.getByRole("banner");
    await expect(top.getByText("Halden")).toBeVisible();
    await expect(top.getByText("Ask", { exact: true })).toBeVisible();
    await expect(primary(page).getByRole("link")).toHaveText(["Ask", "Activity", "Sources"]);
    await expect(primary(page).getByRole("link", { name: "Ask" })).toHaveAttribute("aria-current", "page");
    for (const control of [top.getByRole("button", { name: "Jump" }), top.getByRole("link", { name: "Lattice — Ask" }), ...(await primary(page).getByRole("link").all()), page.getByRole("button", { name: "More" })]) {
      const b = (await control.boundingBox())!;
      expect(b.width).toBeGreaterThanOrEqual(44);
      expect(b.height).toBeGreaterThanOrEqual(44);
    }
    // Scrolled to the end, the last of the page clears the fixed tab bar.
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const bar = (await primary(page).boundingBox())!;
    const last = await page.locator("main#main").evaluate((m) => {
      const all = [...m.querySelectorAll("*")].filter((el) => el.getBoundingClientRect().height > 0);
      return Math.max(...all.map((el) => el.getBoundingClientRect().bottom));
    });
    expect(last).toBeLessThanOrEqual(bar.y + 0.5);
    expect(Math.round(bar.y + bar.height)).toBe(page.viewportSize()!.height);
  });

  test("tabs navigate and move focus to the page", async ({ page }) => {
    await openAsk(page);
    await primary(page).getByRole("link", { name: "Activity" }).click();
    await expect(page.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
    await expect(primary(page).getByRole("link", { name: "Activity" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("banner").getByText("Activity", { exact: true })).toBeVisible();
    await expect(main(page)).toBeFocused();
  });

  test("More holds administration and the account for administrators; focus returns to More on dismiss", async ({ page }) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto("/ask");
    const more = page.getByRole("button", { name: "More" });
    await more.click();
    const sheet = page.getByRole("dialog", { name: "More" });
    await expect(sheet.getByRole("heading", { name: "Administration" })).toBeVisible();
    await expect(sheet.getByRole("link")).toHaveText(["Access", "AI providers"]);
    await expect(sheet).toContainText("lena.brandt@halden.example");
    await expect(sheet.getByRole("button", { name: "Sign out" })).toBeVisible();
    await audit(page);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(more).toBeFocused();

    await more.click();
    await sheet.getByRole("link", { name: "Access" }).click();
    await expect(page.getByRole("heading", { name: "Access", level: 1 })).toBeVisible();
    await expect(sheet).toBeHidden();
    await expect(main(page)).toBeFocused();
    // The current page lives under More: the tab says so, in words as well as the mark.
    await expect(page.getByRole("button", { name: "More, current page: Access" })).toBeVisible();
    await expect(primary(page).locator("[aria-current='page']")).toHaveCount(0);
  });

  test("members: More holds only the account", async ({ page }) => {
    await openAsk(page);
    await page.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    await expect(sheet.getByRole("link")).toHaveCount(0);
    await expect(sheet.getByRole("heading", { name: "Administration" })).toHaveCount(0);
    await expect(sheet).toContainText("maya.okafor@halden.example");
    await sheet.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
  });

  test("a long organization name truncates in the top bar without pushing Jump off screen", async ({ page }) => {
    await mockApi(page, { me: LENA_LONG });
    await page.goto("/sources");
    await expectNoHorizontalOverflow(page);
    const org = page.getByRole("banner").getByText(LONG_ORG);
    await expect(org).toHaveAttribute("title", LONG_ORG);
    const jump = (await page.getByRole("banner").getByRole("button", { name: "Jump" }).boundingBox())!;
    expect(jump.x + jump.width).toBeLessThanOrEqual(390);
  });

  test("320 px (reflow): tab labels stay whole, nothing scrolls sideways", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await mockApi(page, { me: ME_LENA });
    await page.goto("/access");
    await expectNoHorizontalOverflow(page);
    for (const label of await primary(page).locator("a span, button span").all()) {
      expect(await label.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    }
  });

  test("Jump from the top bar", async ({ page }) => {
    await openAsk(page);
    const jump = page.getByRole("banner").getByRole("button", { name: "Jump" });
    await jump.click();
    const input = page.getByRole("combobox", { name: "Jump to" });
    await expect(input).toBeFocused();
    await input.fill("activ");
    await page.getByRole("option", { name: "Activity" }).click();
    await expect(page.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
  });

  test("evidence opens as a bottom sheet over the tab bar, with a visible close button", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    const sheet = page.getByRole("dialog", { name: "Evidence inspector" });
    await expect(sheet).toBeVisible();
    const vp = page.viewportSize()!;
    await expect.poll(async () => {
      const b = await sheet.boundingBox();
      return b ? [Math.round(b.x), Math.round(b.y + b.height)] : null;
    }).toEqual([0, vp.height]);
    await sheet.getByRole("button", { name: "Close evidence" }).click();
    await expect(sheet).toBeHidden();
  });

  test("once the composer scrolls away, New question brings it back", async ({ page }) => {
    await askFor(page, ANSWERED);
    await page.mouse.wheel(0, 1600);
    const again = page.getByRole("button", { name: "New question" });
    await expect(again).toBeVisible();
    await again.click();
    await expect(page.getByRole("textbox", { name: "Question" })).toBeFocused();
  });

  test("trace page fits the phone", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.answered}`);
    await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
