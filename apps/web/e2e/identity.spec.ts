import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ME_LENA, MEMBER_PAT_MANAGED } from "../src/test/fixtures";

import { openAsk, settled } from "./helpers";
import { mockApi } from "./mock-api";

const RATE_LIMITED = {
  status: 429,
  body: { error: { code: "rate_limited", message: "Too many requests. Wait a moment and try again.", request_id: "req-429" } },
  headers: { "retry-after": "30" },
};

async function audit(page: Page) {
  await settled(page);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => `${n.target.join(" ")} ${n.failureSummary ?? ""}`).join(" | ")}`)).toEqual([]);
}

test.describe("sign-in with the organization's identity provider", () => {
  for (const [code, text] of [
    ["sso_failed", "Sign-in didn't complete."],
    ["sso_unavailable", "Your identity provider couldn't be reached."],
    ["rate_limited", "Too many sign-in attempts from this browser."],
  ] as const) {
    test(`shows the fixed message for ${code}`, async ({ page }) => {
      await mockApi(page);
      await page.goto(`/sign-in?error=${code}`);
      await expect(page.getByRole("alert").filter({ hasText: text })).toBeVisible();
      await audit(page);
    });
  }

  test("an unknown error code is ignored, not echoed", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/sign-in?error=${encodeURIComponent("<b>zq-injected</b>")}`);
    await expect(page.getByRole("heading", { name: "Sign in", level: 2 })).toBeVisible();
    // Next.js keeps an empty role=alert route announcer; no alert has any text.
    await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("zq-injected");
  });

  // The browser follows these redirects itself (route interception does not see the
  // followed request), so the hand-over is checked on the response.
  test("the /sso link hands over to the API's OIDC start, keeping a same-origin next", async ({ page }) => {
    const response = await page.request.get("/sso/halden-login-key-0123456789abc?next=/sources", { maxRedirects: 0 });
    expect(response.status()).toBe(303);
    expect(response.headers()["location"]).toBe("/api/v1/auth/oidc/halden-login-key-0123456789abc/start?next=%2Fsources");
  });

  for (const [link, location] of [
    ["/sso/short", "/sign-in?error=sso_failed"],
    ["/sso/halden-login-key-0123456789abc?next=//evil.example", "/api/v1/auth/oidc/halden-login-key-0123456789abc/start"],
  ] as const) {
    test(`${link} redirects to ${location}`, async ({ page }) => {
      const response = await page.request.get(link, { maxRedirects: 0 });
      expect(response.status()).toBe(303);
      expect(response.headers()["location"]).toBe(location);
    });
  }
});

test.describe("content security policy", () => {
  test("scripts run only with this request's nonce", async ({ page }) => {
    const violations: string[] = [];
    await page.addInitScript(() => {
      document.addEventListener("securitypolicyviolation", (e) => {
        (window as unknown as { __csp: string[] }).__csp ??= [];
        (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`);
      });
    });
    page.on("console", (m) => {
      if (/Content Security Policy/i.test(m.text())) violations.push(m.text());
    });
    await mockApi(page);
    const first = await page.goto("/ask");
    await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible(); // hydrated
    const policy = first!.headers()["content-security-policy"] ?? "";
    const scriptSrc = policy.split(";").find((d) => d.trim().startsWith("script-src")) ?? "";
    expect(scriptSrc).toMatch(/'nonce-[A-Za-z0-9+/=]+'/);
    expect(scriptSrc).toContain("'strict-dynamic'");
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(scriptSrc).not.toContain("'unsafe-eval'");
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("object-src 'none'");
    const unsigned = await page.evaluate(() => [...document.scripts].filter((s) => !s.nonce).length);
    expect(unsigned).toBe(0);
    const second = await page.goto("/ask");
    expect(second!.headers()["content-security-policy"]).not.toBe(policy); // a fresh nonce
    await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])).toEqual([]);
    expect(violations).toEqual([]);
  });

  test("pages carry the cross-origin and feature headers", async ({ page }) => {
    await mockApi(page);
    const response = await page.goto("/sign-in");
    const headers = response!.headers();
    expect(headers["cross-origin-opener-policy"]).toBe("same-origin");
    expect(headers["permissions-policy"]).toContain("camera=()");
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["x-content-type-options"]).toBe("nosniff");
  });
});

test("an Ask over the member's limit says when to try again and keeps the question", async ({ page }) => {
  await openAsk(page, { ask: () => RATE_LIMITED });
  const box = page.getByRole("textbox", { name: "Question" });
  await box.fill("What changed in the Acme renewal?");
  await box.press("Enter");
  // A limit is a wait (a status, ochre), not a fault.
  const alert = page.getByRole("status").filter({ hasText: "Too many questions in a row" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Try again in 30 seconds");
  await expect(box).toHaveValue("What changed in the Acme renewal?");
  await audit(page);
});

test("a provisioned member's lifecycle belongs to the identity provider", async ({ page }) => {
  const id = MEMBER_PAT_MANAGED.member.id;
  await mockApi(page, { me: ME_LENA, member: { [id]: MEMBER_PAT_MANAGED } });
  await page.goto(`/access/members/${id}`);
  await expect(page.getByRole("heading", { name: "Pat Rivera", level: 1 })).toBeVisible();
  await expect(page.getByTestId("managed-note")).toContainText("come from your identity provider");
  await expect(page.getByRole("button", { name: "Suspend" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Change access" })).toBeEnabled();
  await audit(page);
});

test("the audit log names the identity provider and its profile updates", async ({ page }) => {
  await mockApi(page, { me: ME_LENA });
  await page.goto("/access/audit");
  await expect(page.getByText("By Your identity provider (SCIM)").first()).toBeVisible();
  await expect(page.getByText("Profile updated")).toBeVisible();
  const entry = page.getByTestId("audit-entry").filter({ hasText: "Profile updated" });
  await expect(entry).toContainText("Pat Doe");
  await expect(entry).toContainText("Pat Rivera");
  await audit(page);
});
