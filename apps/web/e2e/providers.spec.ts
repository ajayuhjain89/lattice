import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  EGRESS_DEFAULT,
  FIXTURE_NOW,
  ME_LENA,
  PREVIEW_APPROVE_GEMINI,
  PROVIDER_GEMINI_NEW,
  PROVIDER_ID,
  PROVIDERS,
  PROVIDERS_EMPTY,
  PROVIDERS_GEMINI_NEW,
  PROVIDERS_MANY,
  providerSettings,
  STEP_UP_GRANTED,
  STEP_UP_NOT_FRESH,
} from "../src/test/fixtures";

import { expectNoHorizontalOverflow, hydrated, openAsk, settled, WIDTHS } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

/**
 * AI providers (Phase 12, P12.5) at /settings/providers, against the mocked API: the
 * server's egress lattice, provider records with their facts kept apart, API keys typed
 * into empty masked fields and sent once, policy changes previewed by the server (Now →
 * After), and an escalation confirmed by a fresh sign-in at the identity provider -- the
 * browser leaves and comes back to `?step_up=<id>`, where the change is applied once.
 */

const LATTICE = "Which data levels each AI provider may receive";
const KEY = "AIzaSy-playwright-key-0123456789-WXYZ";

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
});

async function openProviders(page: Page, options: MockOptions = {}, query = "") {
  const log = await mockApi(page, { me: ME_LENA, ...options });
  await page.goto(`/settings/providers${query}`);
  await expect(page.getByRole("heading", { name: "AI providers", level: 1 })).toBeVisible();
  return log;
}

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

async function menuAction(page: Page, provider: string, action: string) {
  const trigger = page.getByRole("button", { name: `Actions for ${provider}` });
  await hydrated(trigger);
  await trigger.click();
  await page.getByRole("menuitem", { name: action }).click();
}

test("administrators reach the provider settings and see the server's lattice and records", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/ask");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "AI providers" }).click();
  await expect(page.getByRole("heading", { name: "AI providers", level: 1 })).toBeVisible();
  await expect(page.getByRole("table", { name: LATTICE }).getByRole("button")).toHaveCount(9);
  await expect(page.getByTestId("provider-record")).toHaveCount(2);
  await expect(page.getByRole("table", { name: "Embedding providers recorded for this organization" })).toContainText("mxbai-embed-large-v1");
  await expect(page.getByRole("button", { name: "Change policy" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/LATTICE_PROVIDER_SECRET|AIza|vault:/);
  await audit(page);
});

test("members have no admin navigation and get the permission page", async ({ page }) => {
  await openAsk(page, { providers: "forbidden" });
  await expect(page.getByRole("link", { name: "AI providers" })).toHaveCount(0);
  await page.goto("/settings/providers");
  await expect(page.getByRole("heading", { name: /You need administrator access/ })).toBeVisible();
});

test("evidence only: nothing configured, nothing leaves, and a provider can be added", async ({ page }) => {
  const log = await openProviders(page, {
    providers: PROVIDERS_EMPTY,
    providerWrite: (method, path) => (method === "POST" && path === "/v1/admin/providers" ? PROVIDERS_GEMINI_NEW : undefined),
  });
  await expect(page.getByRole("heading", { name: "No AI providers are configured" })).toBeVisible();
  await expect(page.getByTestId("ai-status")).toHaveAttribute("data-reason", "evidence_only");
  await page.getByRole("button", { name: "Add provider" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a generation provider" });
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Answer drafting");
  await dialog.getByRole("button", { name: "Add provider" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("Provider added");
  await expect(page.getByTestId("provider-record")).toContainText("Sends nothing: disabled");
  expect(log.providerWrites).toEqual([
    { method: "POST", path: "/v1/admin/providers", body: { provider: "gemini", model: "gemini-3.5-flash-lite", training_opt_out: true, reason: "Answer drafting" } },
  ]);
});

test("an API key is typed into an empty masked field, sent once, and never stored or shown", async ({ page }) => {
  const log = await openProviders(page, { providers: PROVIDERS_GEMINI_NEW });
  await menuAction(page, "gemini · gemini-3.5-flash-lite", "Replace key…");
  const dialog = page.getByRole("dialog", { name: "Replace the API key of gemini" });
  const field = dialog.getByTestId("api-key-input");
  await expect(field).toHaveAttribute("type", "password");
  await expect(field).toHaveValue("");
  await field.fill(KEY);
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Rotation");
  await dialog.getByRole("button", { name: "Replace key" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("API key replaced");
  await expect(dialog).toBeHidden();
  const carrying = log.providerWrites.filter((w) => JSON.stringify(w.body).includes(KEY));
  expect(carrying.map((w) => `${w.method} ${w.path}`)).toEqual([`PUT /v1/admin/providers/${PROVIDER_ID.gemini}/credential`]);
  expect(await page.content()).not.toContain(KEY);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie + location.href);
  expect(stored).not.toContain(KEY);
});

test("a test says what it proves, and the record keeps the facts apart", async ({ page }) => {
  await openProviders(page, { providers: PROVIDERS_GEMINI_NEW, providerTest: "invalid_credential" });
  const record = page.getByTestId("provider-record");
  await expect(record).toContainText("Set in Lattice · ends ••••WXYZ");
  await expect(record).toContainText("Not approved: nothing is sent to it");
  await record.getByRole("button", { name: "Test" }).click();
  await expect(record.getByTestId("test-result")).toContainText("The provider rejected the API key. Replace it.");
});

test("lowering applies with a reason and no sign-in", async ({ page }) => {
  const lowered = providerSettings({ ...EGRESS_DEFAULT, external_egress_level: null, version: 8 }, [PROVIDER_GEMINI_NEW]);
  const log = await openProviders(page, {
    providers: PROVIDERS_GEMINI_NEW,
    providerWrite: (method, path) => (path === "/v1/admin/providers/changes" ? lowered : undefined),
  });
  await page.getByRole("button", { name: "Change policy" }).click();
  const dialog = page.getByRole("dialog", { name: "Change the AI data policy" });
  await dialog.getByRole("combobox", { name: "External APIs" }).selectOption("none");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByRole("heading", { name: "This doesn't raise anything" })).toBeVisible();
  await expect(dialog.getByTestId("now-after")).toContainText("Not allowed");
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Incident 7");
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("AI data policy changed");
  await expect(page.locator("dl").first()).toContainText("Not allowed");
  expect(log.providerWrites.map((w) => w.path)).toEqual(["/v1/admin/providers/changes/preview", "/v1/admin/providers/changes"]);
});

test("an escalation: Now → After, reason, the organization's name, a fresh sign-in, then applied once", async ({ page }) => {
  const approved = providerSettings({ ...EGRESS_DEFAULT, version: 9 }, [{ ...PROVIDER_GEMINI_NEW, enabled: true, explicitly_approved: true }]);
  const log = await openProviders(page, {
    providers: PROVIDERS_GEMINI_NEW,
    providerPreview: () => PREVIEW_APPROVE_GEMINI,
    providerWrite: (method, path) => (path.endsWith("/apply") ? approved : undefined),
  });
  await menuAction(page, "gemini · gemini-3.5-flash-lite", "Approve…");
  const dialog = page.getByRole("dialog", { name: "Approval of gemini · gemini-3.5-flash-lite" });
  await dialog.getByRole("radio", { name: "Internal" }).check();
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByRole("heading", { name: "This raises what may leave Lattice" })).toBeVisible();
  await expect(dialog.getByTestId("newly-allowed")).toContainText("Internal content may now be sent to gemini");
  await audit(page);
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Security review 42");
  await dialog.getByTestId("confirmation").fill("Halden Logistics");
  await dialog.getByRole("button", { name: "Continue to sign in again" }).click();
  // The mocked identity provider sends the browser straight back.
  await expect(page).toHaveURL(new RegExp(`step_up=${STEP_UP_GRANTED.id}`));
  const result = page.getByTestId("step-up-result");
  await expect(result).toContainText("Signed in again");
  await expect(result.getByTestId("newly-allowed")).toContainText("Internal content may now be sent to gemini");
  expect(log.providerWrites.some((w) => w.path.endsWith("/apply"))).toBe(false); // nothing applied by the return
  await audit(page);
  await result.getByRole("button", { name: "Apply this change" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("AI data policy raised");
  await expect(page).not.toHaveURL(/step_up=/);
  expect(log.providerWrites.filter((w) => w.path.endsWith("/apply"))).toHaveLength(1);
  const started = log.providerWrites.find((w) => w.path === "/v1/admin/providers/step-ups");
  expect(started?.body).toEqual({
    change: { kind: "provider", provider_id: PROVIDER_ID.gemini, approval: "internal" },
    policy_version: 7,
    reason: "Security review 42",
    confirmation: "Halden Logistics",
  });
});

test("a refused or expired step-up applies nothing and says why", async ({ page }) => {
  await openProviders(page, { stepUp: STEP_UP_NOT_FRESH }, `?step_up=${STEP_UP_GRANTED.id}`);
  const result = page.getByTestId("step-up-result");
  await expect(result).toContainText("Not a fresh sign-in");
  await expect(result.getByRole("button", { name: "Apply this change" })).toHaveCount(0);
  await result.getByRole("button", { name: "Dismiss" }).click();
  await expect(result).toBeHidden();

  await openProviders(
    page,
    { providerWrite: (_m, path) => (path.endsWith("/apply") ? { status: 409, body: { error: { code: "conflict", message: "x", request_id: "r", details: [{ field: "step_up", issue: "grant_expired" }] } } } : undefined) },
    `?step_up=${STEP_UP_GRANTED.id}`,
  );
  await page.getByRole("button", { name: "Apply this change" }).click();
  await expect(page.getByTestId("step-up-result")).toContainText("More than five minutes passed since you signed in again");
});

test("a policy changed by someone else is refused, not overwritten", async ({ page }) => {
  await openProviders(page, {
    providerWrite: (_m, path) =>
      path === "/v1/admin/providers/changes" ? { status: 409, body: { error: { code: "conflict", message: "x", request_id: "r", details: [{ field: "policy_version", issue: "changed" }] } } } : undefined,
  });
  await page.getByRole("button", { name: "Change policy" }).click();
  const dialog = page.getByRole("dialog", { name: "Change the AI data policy" });
  await dialog.getByRole("combobox", { name: "External APIs" }).selectOption("none");
  await dialog.getByRole("button", { name: "Review change" }).click();
  await dialog.getByRole("textbox", { name: "Reason" }).fill("Lower");
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(dialog.getByRole("alert")).toContainText("The AI data policy changed after you reviewed this");
});

test("the egress lattice is one Tab stop, walked with arrows, every cell named", async ({ page }) => {
  await openProviders(page);
  const lattice = page.getByRole("table", { name: LATTICE });
  await expect(lattice.getByRole("button")).toHaveCount(9);
  await expect(lattice.locator("button[tabindex='0']")).toHaveCount(1);
  await lattice.locator("button[tabindex='0']").focus();
  await expect(page.getByTestId("egress-why")).toHaveText(/^Internal data may be sent to ollama/);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(":focus")).toHaveAccessibleName("Restricted data isn't sent to ollama · qwen3.5:4b: it's approved only up to Confidential.");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("egress-why")).toHaveText(/^Restricted data isn't sent to ollama/);
});

test("keyboard: actions are reachable, dialogs trap focus and return it to their opener", async ({ page }) => {
  await openProviders(page);
  const opener = page.getByRole("button", { name: "Change policy" });
  await hydrated(opener);
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Change the AI data policy" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("boundary cells are hatched with a dashed edge; filled cells are solid -- not colour alone", async ({ page }) => {
  await openProviders(page, { providers: PROVIDERS });
  const lattice = page.getByRole("table", { name: LATTICE });
  const kept = lattice.getByRole("button", { name: /^Restricted data isn't sent to ollama/ });
  const sent = lattice.getByRole("button", { name: /^Internal data may be sent to ollama/ });
  await expect(kept).toHaveCSS("border-top-style", "dashed");
  expect(await kept.evaluate((el) => getComputedStyle(el).backgroundImage)).toContain("repeating-linear-gradient");
  await expect(sent).toHaveCSS("border-top-style", "solid");
  await page.emulateMedia({ forcedColors: "active" });
  await expect(kept).toHaveCSS("border-top-style", "dashed");
  expect(await kept.evaluate((el) => getComputedStyle(el).backgroundImage)).toBe("none");
});

test("forced colours and reduced motion: the step-up result keeps its edge, nothing animates", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  await openProviders(page, {}, `?step_up=${STEP_UP_GRANTED.id}`);
  const result = page.getByTestId("step-up-result");
  await expect(result).toBeVisible();
  await expect(result).toHaveCSS("border-left-style", "solid");
  await audit(page);
  expect(await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length)).toBe(0);
});

for (const width of [...WIDTHS, 320]) {
  test(`providers fit at ${width}px with eight providers; a wide lattice scrolls in a named region`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openProviders(page, { providers: PROVIDERS_MANY });
    const lattice = page.getByRole("table", { name: LATTICE });
    await expect(lattice.getByRole("button")).toHaveCount(24);
    await lattice.getByRole("button", { name: /^Restricted data isn't sent to voyage/ }).focus();
    await expectNoHorizontalOverflow(page);
    if (width < 1024) await expect(page.getByRole("region", { name: LATTICE })).toBeVisible();
  });
}

for (const [label, width, height] of [
  ["tablet", 768, 1024],
  ["320 px", 320, 640],
  ["200 % zoom (640 CSS px)", 640, 450],
] as const) {
  test(`${label}: the escalation review and the key dialog reflow without page scroll`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openProviders(page, { providers: PROVIDERS_GEMINI_NEW, providerPreview: () => PREVIEW_APPROVE_GEMINI });
    await expectNoHorizontalOverflow(page);
    await menuAction(page, "gemini · gemini-3.5-flash-lite", "Approve…");
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("radio", { name: "Internal" }).check();
    await dialog.getByRole("button", { name: "Review change" }).click();
    await expect(dialog.getByTestId("confirmation")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const box = await dialog.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 0.5).toBe(true);
    await page.keyboard.press("Escape");
    await menuAction(page, "gemini · gemini-3.5-flash-lite", "Replace key…");
    await expect(page.getByRole("dialog").getByTestId("api-key-input")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}
