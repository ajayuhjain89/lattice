/**
 * Visual review harness: renders every screen and state at the five review widths and
 * writes screenshots to e2e/screens/ (git-ignored). Run explicitly:
 *   pnpm exec playwright test --project=screens
 * It asserts only the layout invariants; the images are for human review.
 *
 * Captures are real windows, not stitched full-page images: the window is made as tall as
 * the page and photographed at the top, so the sticky masthead, the docked inspector and
 * the phone tab bar sit where they would in a tall window. Overlays fixed to the viewport
 * (drawers and sheets below 1280 px, dialogs at every width) are captured at the device's
 * own height. Motion is
 * reduced, focus cleared and the caret hidden, so the same build gives the same images.
 *
 * The canonical set (docs/screens/, committed) is the CANONICAL subset, written as JPEG:
 *   pnpm screens:canonical
 */
import { test, type Page } from "@playwright/test";

import {
  AMBIGUOUS,
  ANSWERED,
  CONFLICTS,
  MANY_EXHIBITS,
  MANY_TO_MANY,
  RATE_LIMITED,
  auditPages,
  EV,
  EVIDENCE_ONLY,
  FIXTURE_NOW,
  ORG_SOURCE,
  SOURCES_ADMIN,
  SOURCES_ADMIN_ORG,
  MANY_MEMBERS,
  MEMBER_MAYA,
  MEMBER_WIDE,
  PREVIEW_MIXED,
  PROVIDERS_MANY,
  PROVIDERS_EMPTY,
  PROVIDERS_GEMINI_NEW,
  PREVIEW_APPROVE_GEMINI,
  STEP_UP_GRANTED,
  ACCOUNT_IDS,
  IDENTITY_BLOCKED,
  IDENTITY_NONE,
  MEMBER_RAE_REMOVED,
  SOURCE_RECORD_DRIVE,
  SOURCE_RECORD_ESCALATIONS,
  SOURCE_RECORD_NEW,
  UPLOAD_REFUSED_LAYOUT,
  SETUP_FRESH,
  SETUP_IN_USE,
  SOURCES_ADMIN_LONG,
  FAILED,
  ID,
  INSUFFICIENT,
  ME_LENA,
  PARTIAL,
  SUPERSEDED,
  TRACE,
  UNAVAILABLE,
  UNSUPPORTED,
  UNVERIFIED,
} from "../src/test/fixtures";

import { askFor, expectNoHorizontalOverflow, hydrated, openAsk, WIDTHS } from "./helpers";
import { mockApi, type MockOptions } from "./mock-api";

const only = process.env.SCREENS?.split(",");
const widths = process.env.SCREEN_WIDTHS ? process.env.SCREEN_WIDTHS.split(",").map(Number) : [...WIDTHS];

// Overlays fixed to the viewport are photographed at the device's own height: the
// inspector below 1280 px (a drawer or a sheet; docked above), and dialogs at every width.
const OVERLAYS: Record<string, number> = { "ask-inspector": 1280, "ask-inspector-long": 1280, "access-change": Infinity };

/** The operational screens read ages and day headings: they render at the fixtures' "now". */
async function at(page: Page, options: MockOptions = {}) {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
  return mockApi(page, options);
}

// The development-sign-in-off server (playwright.config.ts): production sign-in.
const PRODUCTION = "http://127.0.0.1:3101";

// SCREENS_DIR writes elsewhere (e.g. a review folder) without touching the harness output.
const DIR = process.env.SCREENS_DIR ?? "e2e/screens";

// The committed reference set: one representative state per surface, at the widths that
// matter for it. Everything else stays in the git-ignored harness output.
const CANONICAL: Record<string, readonly number[]> = {
  landing: [1440, 390],
  "sign-in-production": [1440, 390],
  "sign-in-production-error": [1440],
  "sign-in-dev": [1440],
  "error-workspace": [1440],
  "not-found": [1440],
  "ask-intro": [1440, 390],
  "ask-answered": [1440, 390],
  "ask-many-to-many": [1440],
  "ask-conflicts": [1440],
  "ask-partial": [1440],
  "ask-evidence-only": [1440],
  "ask-inspector": [1440, 1024],
  "trace-asker": [1440],
  evidence: [1440],
  "sources-admin": [1440, 390],
  "activity-member": [1440],
  "access-members": [1440, 390],
  "access-member": [1440],
  "access-change": [1440],
  "access-audit": [1440],
  providers: [1440, 390],
};
const CANONICAL_DIR = process.env.CANONICAL_DIR;
// A window taller than this is photographed at this height (no page comes close).
const MAX_HEIGHT = 8000;

const shot = async (page: Page, name: string, width: number) => {
  await page.evaluate(() => document.fonts.ready);
  // Nothing focused (no skip link, no focus ring left by the setup), and the top of the page.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur?.();
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(200);
  await expectNoHorizontalOverflow(page);
  if (!(width < (OVERLAYS[name] ?? 0))) {
    // Grow the window to the page twice: a taller window can change what 100svh lays out.
    for (let i = 0; i < 2; i++) {
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      const current = page.viewportSize()!.height;
      if (height <= current) break;
      await page.setViewportSize({ width, height: Math.min(height, MAX_HEIGHT) });
      await page.waitForTimeout(150);
    }
  }
  const options = { animations: "disabled", caret: "hide" } as const;
  if (CANONICAL_DIR) await page.screenshot({ ...options, path: `${CANONICAL_DIR}/${name}@${width}.jpg`, type: "jpeg", quality: 80 });
  else await page.screenshot({ ...options, path: `${DIR}/${name}@${width}.png` });
};

const SCREENS: Record<string, (page: Page) => Promise<void>> = {
  specimen: async (page) => {
    await page.goto("/specimen");
    await page.evaluate(() => document.fonts.ready);
  },
  "specimen-dark": async (page) => {
    await page.goto("/specimen");
    await page.getByRole("button", { name: "Dark map" }).click();
    await page.evaluate(() => document.fonts.ready);
  },
  landing: async (page) => {
    await page.goto("/");
    await page.evaluate(() => document.fonts.ready);
  },
  "sign-in-dev": async (page) => {
    await mockApi(page);
    await page.goto("/sign-in");
  },
  "sign-in-dev-next": async (page) => {
    await mockApi(page);
    await page.goto(`/sign-in?next=${encodeURIComponent("/access/audit")}`);
  },
  "sign-in-dev-failed": async (page) => {
    await mockApi(page, { devLogin: { status: 401, body: { error: { code: "authentication_failed", message: "Sign-in failed.", request_id: "r" } } } });
    await page.goto("/sign-in");
    await page.getByRole("button", { name: /Maya Okafor/ }).click();
    await page.getByRole("alert").filter({ hasText: "Sign-in failed." }).waitFor();
  },
  "sign-in-production": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in`);
  },
  "sign-in-production-error": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?error=sso_failed`);
  },
  "sign-in-production-rate-limited": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?error=rate_limited`);
  },
  "sign-in-production-next": async (page) => {
    await page.goto(`${PRODUCTION}/sign-in?next=${encodeURIComponent("/access/members/40000000-0000-4000-8000-000000000001")}`);
  },
  "ask-intro": async (page) => {
    await openAsk(page);
  },
  "ask-pending": async (page) => {
    await openAsk(page, { ask: () => ANSWERED, askDelay: 60_000 });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme, and what did we tell them?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByTestId("ask-progress").waitFor();
  },
  "ask-answered": async (page) => {
    await askFor(page, ANSWERED);
  },
  "ask-inspector": async (page) => {
    await askFor(page, ANSWERED);
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 }).waitFor();
  },
  "ask-many-to-many": async (page) => {
    await askFor(page, MANY_TO_MANY);
  },
  "ask-conflicts": async (page) => {
    await askFor(page, CONFLICTS, "How big is Acme's pilot, and how fast must Support respond?");
  },
  "ask-many-exhibits": async (page) => {
    await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say about scope, dates and support?");
  },
  "ask-inspector-long": async (page) => {
    await askFor(page, MANY_EXHIBITS, "What does the SCIM rollout plan say about scope, dates and support?");
    await page.getByRole("button", { name: /^Citation 1:/ }).first().click();
    await page.getByTestId("evidence-inspector").getByRole("heading", { level: 2 }).first().waitFor();
  },
  "ask-rate-limited": async (page) => {
    await openAsk(page, { ask: () => RATE_LIMITED });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByRole("heading", { name: "Too many questions in a row" }).waitFor();
  },
  "ask-partial": async (page) => {
    await askFor(page, PARTIAL);
  },
  "ask-insufficient": async (page) => {
    await askFor(page, INSUFFICIENT, "What did Kestrel agree to in the renewal?");
  },
  "ask-unverified": async (page) => {
    await askFor(page, UNVERIFIED);
  },
  "ask-evidence-only": async (page) => {
    await askFor(page, EVIDENCE_ONLY, "What did Acme's CIO say at the QBR?");
  },
  "ask-unavailable": async (page) => {
    await askFor(page, UNAVAILABLE);
  },
  "ask-ambiguous": async (page) => {
    await askFor(page, AMBIGUOUS, "What did we promise Acme?");
  },
  "ask-unsupported": async (page) => {
    await askFor(page, UNSUPPORTED, "Draft an email to Acme about the SCIM delay");
  },
  "ask-failed": async (page) => {
    await askFor(page, FAILED);
  },
  "ask-superseded": async (page) => {
    await askFor(page, SUPERSEDED, "What is the refund window?");
  },
  "ask-network-error": async (page) => {
    await openAsk(page, { ask: () => "network" });
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByRole("heading", { name: "Couldn't reach Lattice" }).waitFor();
  },
  "trace-asker": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.answered}`);
    await page.getByRole("heading", { name: "Pipeline", exact: true }).waitFor();
  },
  "trace-hidden": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.hidden}`);
    await page.getByText("Answer hidden").waitFor();
  },
  "trace-admin": async (page) => {
    await mockApi(page, { me: ME_LENA });
    await page.goto(`/traces/${TRACE.other}`);
    await page.getByRole("heading", { name: "Operational trace" }).waitFor();
  },
  "trace-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/traces/${TRACE.dangling}`);
    await page.getByRole("heading", { name: "This trace isn't available" }).waitFor();
  },
  evidence: async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.decision}`);
    await page.getByRole("heading", { name: /Identity roadmap decision/, level: 1 }).waitFor();
  },
  "evidence-missing": async (page) => {
    await mockApi(page);
    await page.goto(`/evidence/${EV.missing}`);
    await page.getByRole("heading", { name: "This evidence isn't available" }).waitFor();
  },
  providers: async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/settings/providers");
    await page.getByTestId("egress-lattice").waitFor();
  },
  organization: async (page) => {
    await at(page, { me: ME_LENA, setup: SETUP_FRESH });
    await page.goto("/settings");
    await page.getByTestId("setup-record").waitFor();
  },
  "organization-in-use": async (page) => {
    await at(page, { me: ME_LENA, setup: SETUP_IN_USE });
    await page.goto("/settings");
    await page.getByTestId("setup-record").waitFor();
  },
  "providers-many": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_MANY });
    await page.goto("/settings/providers");
    await page.getByTestId("egress-lattice").waitFor();
    await page.getByRole("button", { name: /^Confidential data isn't sent to anthropic/ }).focus();
  },
  "sources-member": async (page) => {
    await at(page);
    await page.goto("/sources");
    await page.getByRole("table", { name: "Sources you can read from" }).and(page.locator(":not([aria-busy])")).waitFor();
  },
  "sources-admin": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/sources");
    await page.getByTestId("register-summary").waitFor();
  },
  "sources-failed": async (page) => {
    await at(page, { me: ME_LENA, adminSources: SOURCES_ADMIN_LONG });
    await page.goto("/sources");
    await page.getByRole("button", { name: "Show failed records: Halden Support (Zendesk)" }).click();
    await page.getByText("rec_3f9a0c1d2e4b5a67").waitFor();
  },
  "sources-empty": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [] } });
    await page.goto("/sources");
    await page.getByRole("heading", { name: "No sources are connected" }).waitFor();
  },
  // P12.6: an organization-managed source's record (checked export, references waiting),
  // a new one waiting for its first export, and the disconnect review.
  "source-record": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] } });
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    await page.getByTestId("check-result").waitFor();
  },
  "source-record-new": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] } });
    await page.goto(`/sources/${ORG_SOURCE.handbook}`);
    await page.getByTestId("no-import").waitFor();
  },
  "source-disconnect": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] } });
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    await page.getByRole("button", { name: "Disconnect" }).click();
    await page.getByTestId("disconnect-preview").waitFor();
  },
  "activity-member": async (page) => {
    await at(page);
    await page.goto("/activity");
    await page.getByRole("heading", { name: /^Today/ }).waitFor();
  },
  "activity-empty": async (page) => {
    await at(page, { activity: { items: [] } });
    await page.goto("/activity");
    await page.getByRole("heading", { name: "You haven't asked anything yet" }).waitFor();
  },
  "activity-admin": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/activity/organization");
    await page.getByRole("table", { name: "Organization runs (operational view)" }).and(page.locator(":not([aria-busy])")).waitFor();
  },
  "access-members": async (page) => {
    await at(page, { me: ME_LENA, members: MANY_MEMBERS });
    await page.goto("/access");
    await page.getByRole("link", { name: "Member 02" }).waitFor();
  },
  "access-member": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-member-wide": async (page) => {
    await at(page, { me: ME_LENA, member: { [MEMBER_WIDE.member.id]: MEMBER_WIDE } });
    await page.goto(`/access/members/${MEMBER_WIDE.member.id}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-suspended": async (page) => {
    await at(page, { me: ME_LENA, member: { [ID.maya]: { ...MEMBER_MAYA, member: { ...MEMBER_MAYA.member, status: "suspended" } } } });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByTestId("reach").waitFor();
  },
  "access-change": async (page) => {
    await at(page, { me: ME_LENA, preview: PREVIEW_MIXED });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByRole("button", { name: "Change access" }).click();
    await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
    await page.getByRole("checkbox", { name: "Sales" }).uncheck();
    await page.getByRole("radio", { name: "Confidential" }).check();
    await page.getByRole("button", { name: "Review change" }).click();
    await page.getByRole("table", { name: "Now and after this change, rule by rule" }).waitFor();
  },
  "access-applied": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto(`/access/members/${ID.maya}`);
    await page.getByRole("button", { name: "Change access" }).click();
    await page.getByRole("checkbox", { name: "Kestrel Bank" }).check();
    await page.getByRole("button", { name: "Review change" }).click();
    await page.getByRole("textbox", { name: "Reason" }).fill("Covering Kestrel during the renewal");
    await page.getByRole("button", { name: "Apply change" }).click();
    await page.getByTestId("applied-change").waitFor();
  },
  "access-audit": async (page) => {
    await at(page, { me: ME_LENA, audit: (before) => auditPages(before) });
    await page.goto("/access/audit");
    await page.getByTestId("audit-entry").first().waitFor();
  },
  "error-workspace": async (page) => {
    page.on("pageerror", () => {}); // a render failure is the state under review
    await mockApi(page, { ask: () => ({ ...ANSWERED, claims: null }) as unknown as typeof ANSWERED });
    await page.goto("/ask");
    await page.getByRole("textbox", { name: "Question" }).fill("When will SCIM ship for Acme?");
    await page.getByRole("textbox", { name: "Question" }).press("Enter");
    await page.getByTestId("page-fault").waitFor();
  },
  "not-found": async (page) => {
    await page.goto("/no-such-page");
    await page.getByRole("heading", { name: "This page doesn't exist" }).waitFor();
  },
  // P12.5: a provider configured in the product, the escalation review, the return from the IdP.
  "providers-new": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_GEMINI_NEW });
    await page.goto("/settings/providers");
    await page.getByTestId("provider-record").waitFor();
  },
  "providers-escalation": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_GEMINI_NEW, providerPreview: () => PREVIEW_APPROVE_GEMINI });
    await page.goto("/settings/providers");
    const trigger = page.getByRole("button", { name: "Actions for gemini · gemini-3.5-flash-lite" });
    await hydrated(trigger);
    await trigger.click();
    await page.getByRole("menuitem", { name: "Approve…" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("radio", { name: "Internal" }).check();
    await dialog.getByRole("button", { name: "Review change" }).click();
    await dialog.getByTestId("confirmation").waitFor();
  },
  "providers-step-up": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_GEMINI_NEW });
    await page.goto(`/settings/providers?step_up=${STEP_UP_GRANTED.id}`);
    await page.getByTestId("step-up-result").getByRole("button", { name: "Apply this change" }).waitFor();
  },
  "providers-empty": async (page) => {
    await at(page, { me: ME_LENA, providers: PROVIDERS_EMPTY });
    await page.goto("/settings/providers");
    await page.getByRole("heading", { name: "No AI providers are configured" }).waitFor();
  },
  // P12.8: the Phase 12 states the closure review looks at -- identity, accounts and their
  // references, a removed member's reinstatement, and a source's failures.
  identity: async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/settings/identity");
    await page.getByTestId("connection-facts").waitFor();
  },
  "identity-none": async (page) => {
    await at(page, { me: ME_LENA, identitySettings: IDENTITY_NONE });
    await page.goto("/settings/identity");
    await page.getByRole("heading", { level: 1 }).waitFor();
  },
  "identity-blocked": async (page) => {
    await at(page, { me: ME_LENA, identitySettings: IDENTITY_BLOCKED });
    await page.goto("/settings/identity");
    await page.getByTestId("connection-facts").waitFor();
  },
  accounts: async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/access/accounts");
    await page.getByRole("table").and(page.locator(":not([aria-busy])")).waitFor();
  },
  "account-archived": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto(`/access/accounts/${ACCOUNT_IDS.orbital}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
  },
  "references-unmapped": async (page) => {
    await at(page, { me: ME_LENA });
    await page.goto("/access/accounts/references");
    await page.getByRole("table").and(page.locator(":not([aria-busy])")).waitFor();
  },
  "member-reinstate": async (page) => {
    await at(page, { me: ME_LENA, member: { [MEMBER_RAE_REMOVED.member.id]: MEMBER_RAE_REMOVED } });
    await page.goto(`/access/members/${MEMBER_RAE_REMOVED.member.id}`);
    const reinstate = page.getByRole("button", { name: "Reinstate" });
    await hydrated(reinstate);
    await reinstate.click();
    await page.getByRole("dialog").waitFor();
  },
  "source-upload-refused": async (page) => {
    await at(page, { me: ME_LENA, adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] }, upload: () => ({ status: 422, body: UPLOAD_REFUSED_LAYOUT }) });
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    const file = page.getByLabel("Export file");
    await hydrated(file);
    await file.setInputFiles({ name: "not-an-export.zip", mimeType: "application/zip", buffer: Buffer.from("PK\u0003\u0004 not an export") });
    await page.getByRole("button", { name: "Upload a replacement" }).click();
    await page.getByTestId("upload-status").getByText(/Not accepted/).waitFor();
  },
  "source-check-failed": async (page) => {
    const current = SOURCE_RECORD_ESCALATIONS.current_import!;
    const invalid = {
      ...SOURCE_RECORD_ESCALATIONS,
      current_import: { ...current, check: { ...current.check!, error_code: "source_unavailable:unreadable" } },
      next_action: "replace_import",
      can_sync: false,
      sync_blocked_by: "import_invalid",
    } as const;
    await at(page, {
      me: ME_LENA,
      adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] },
      sourceRecords: { [invalid.id]: invalid, [SOURCE_RECORD_NEW.id]: SOURCE_RECORD_NEW, [SOURCE_RECORD_DRIVE.id]: SOURCE_RECORD_DRIVE },
    });
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    await page.getByText("This export can't be synced").waitFor();
  },
  "source-sync-failed": async (page) => {
    const failed = {
      ...SOURCE_RECORD_ESCALATIONS,
      last_synced_at: "2026-09-29T06:00:09Z",
      job: {
        id: "7f2a1b0c-4444-4d3e-9f4a-1b2c3d4e5f09",
        source_id: SOURCE_RECORD_ESCALATIONS.id,
        kind: "source_sync",
        import_id: SOURCE_RECORD_ESCALATIONS.current_import!.id,
        mode: "full",
        status: "failed",
        attempts: 3,
        error_code: "source_unavailable:timeout",
        created_at: "2026-09-29T05:59:58Z",
        started_at: "2026-09-29T06:00:01Z",
        finished_at: "2026-09-29T06:00:09Z",
        sync_run_id: null,
      },
      next_action: "retry_sync",
    } as const;
    await at(page, {
      me: ME_LENA,
      adminSources: { sources: [...SOURCES_ADMIN.sources, ...SOURCES_ADMIN_ORG] },
      sourceRecords: { [failed.id]: failed, [SOURCE_RECORD_NEW.id]: SOURCE_RECORD_NEW, [SOURCE_RECORD_DRIVE.id]: SOURCE_RECORD_DRIVE },
    });
    await page.goto(`/sources/${ORG_SOURCE.escalations}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
  },
  "providers-forbidden": async (page) => {
    await at(page, { providers: "forbidden" });
    await page.goto("/settings/providers");
    await page.getByRole("heading", { name: /administrator access/ }).waitFor();
  },
};

for (const [name, render] of Object.entries(SCREENS)) {
  if (only && !only.includes(name)) continue;
  for (const width of widths) {
    if (CANONICAL_DIR && !CANONICAL[name]?.includes(width)) continue;
    test(`${name} @ ${width}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
      await render(page);
      await shot(page, name, width);
    });
  }
}
