/**
 * The onboarding journey in the real product (P12.7; phase-12-plan §20), against the
 * reference stack — not the mocked API. Run by `make prod-onboard` (project
 * `onboarding-stack`, only when LATTICE_ONBOARD_STACK is set) after `make prod-up IDP=1`:
 * the stack in production mode, the web and API instances behind Caddy on
 * https://localhost:8443, the verification identity provider on https://localhost:9443.
 *
 * The operator creates a new run-unique organization (`lattice org create`, in the stack's
 * ops container); its first administrator signs in through the identity provider (the
 * provider has no login page: the browser names the person, as the API drill does) and,
 * in the product only, reads the setup record, follows its links, adds a member, creates a
 * source, uploads its export, watches the check, maps the account reference it reports,
 * runs the first sync and gives the member the account. The member signs in, asks, opens
 * the evidence and the trace. The setup record then says the first answer is done.
 *
 * Serial by design: each step is the next thing a customer does.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request, test, type BrowserContext, type Page } from "@playwright/test";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const IDP = "https://localhost:9443";
const RUN = new Date().toISOString().replace(/\D/g, "").slice(4, 14);
const SLUG = `ui-${RUN}`;
const CLIENT = `onboard-ui-${RUN}`;
const SECRET = `ui-secret-${RUN}-${Math.random().toString(36).slice(2)}`;
const REASON = "P12.7 browser onboarding (synthetic)";
const QUESTION = "What restored the Quillon telemetry feed for Ashgrove?";

test.describe.configure({ mode: "serial" });
test.setTimeout(180_000);

function env(): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of readFileSync(path.join(ROOT, "deploy/.env.reference"), "utf8").split("\n")) {
    const at = line.indexOf("=");
    if (at > 0 && !line.startsWith("#")) values[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return values;
}

/** `lattice …` in the stack's ops container, as the platform operator. */
function operator(args: string[], input?: string): string {
  const words = (process.env.LATTICE_COMPOSE ?? "").split(/\s+/).filter(Boolean);
  const vars = Object.fromEntries(words.filter((w) => /^[A-Z_]+=/.test(w)).map((w) => w.split("=", 2)));
  const [command, ...rest] = words.filter((w) => !/^[A-Z_]+=/.test(w));
  if (!command) throw new Error("LATTICE_COMPOSE is not set: run this project through `make prod-onboard`");
  return execFileSync(command, [...rest, "run", "--rm", "--no-deps", "-T", "ops", ...args], {
    cwd: ROOT,
    env: { ...process.env, ...vars },
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  });
}

async function registerClient() {
  const values = env();
  const admin = await request.newContext({ ignoreHTTPSErrors: true });
  const response = await admin.put(`${IDP}/admin/state`, {
    headers: { Authorization: `Bearer ${values.VERIFY_IDP_ADMIN_TOKEN}` },
    data: { clients: { "lattice-verify": [values.VERIFY_OIDC_CLIENT_SECRET], [CLIENT]: [SECRET] }, offsets: {} },
  });
  expect(response.ok()).toBe(true);
  await admin.dispose();
}

/**
 * Sign in at the organization's sign-in link. The verification provider has no login page:
 * the browser reaches its authorization endpoint, then "signs in" there by naming the
 * person (`persona`), and the provider redirects back with a one-time code.
 */
async function signIn(page: Page, loginKey: string, subject: string) {
  await page.goto(`/sso/${loginKey}`);
  await expect(page).toHaveURL(new RegExp(`^${IDP}/authorize\\?`));
  await page.goto(`${page.url()}&persona=${subject}`);
  await expect(page).toHaveURL(/\/ask$/);
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
}

const record = (page: Page) => page.getByTestId("setup-record");
const row = (page: Page, name: string) => record(page).getByRole("listitem", { name, exact: true });

let loginKey = "";
let admin: BrowserContext;
let page: Page;
let sourceUrl = "";

test.beforeAll(async ({ browser }) => {
  await registerClient();
  const out = operator(
    [
      "org", "create", "--slug", SLUG, "--name", `Wrenfield UI ${RUN} (synthetic)`, "--issuer", "https://idp:9443",
      "--client-id", CLIENT, "--client-secret-stdin", "--register-private-issuer",
      "--first-admin-email", "ada.quist@wrenfield.example", "--first-admin-identifier", "wrn-ada", "--reason", REASON,
    ],
    SECRET,
  );
  loginKey = /\/sso\/([A-Za-z0-9_-]+)/.exec(out)?.[1] ?? "";
  expect(loginKey).not.toBe("");
  operator(["outbound", "allow", "--org", SLUG, "--url", IDP, "--purpose", "oidc", "--reason", "Verification IdP (browser-facing)"]);
  admin = await browser.newContext({ ignoreHTTPSErrors: true });
  page = await admin.newPage();
});

test.afterAll(async () => {
  await admin?.close();
});

test("the first administrator signs in and reads a truthful setup record", async () => {
  await signIn(page, loginKey, "wrn-ada");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Organization", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Organization", level: 1 })).toBeVisible();
  await expect(record(page).getByRole("listitem")).toHaveCount(8);
  await expect(row(page, "Identity provider")).toContainText("Complete");
  await expect(row(page, "Members")).toContainText("Required");
  await expect(row(page, "AI policy")).toContainText("Optional");
  await expect(row(page, "Sources")).toContainText("Required");
  await expect(row(page, "First answer")).toContainText("Blocked");
  await expect(page.locator("main")).not.toContainText("%");
});

test("Members → Access: a member is added by their exact identifier", async () => {
  await row(page, "Members").getByRole("link", { name: "Add a member" }).click();
  await expect(page.getByRole("heading", { name: "Access", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Add member" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a member" });
  await dialog.getByRole("textbox", { name: "Name" }).fill("Riley Moss");
  await dialog.getByRole("textbox", { name: "E-mail" }).fill("riley.moss@wrenfield.example");
  await dialog.getByRole("textbox", { name: "Identifier from your identity provider" }).fill("wrn-riley");
  await dialog.getByRole("textbox", { name: "Reason" }).fill(REASON);
  await dialog.getByRole("button", { name: "Add member" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Riley Moss" })).toBeVisible();
  await expect(page.getByTestId("sign-in-identifier")).toHaveText("Signs in as wrn-riley");
});

test("Sources: a Tickets source is created and its export uploaded and checked", async () => {
  await page.goto("/settings");
  await expect(row(page, "Members")).toContainText("Complete");
  await row(page, "Sources").getByRole("link", { name: "Open Sources" }).click();
  await page.getByRole("button", { name: "Add source" }).click();
  const dialog = page.getByRole("dialog", { name: "Add a source" });
  await dialog.getByRole("radio", { name: "Tickets — JSON Lines export" }).check();
  await expect(dialog.getByRole("checkbox", { name: "Every record must belong to an account" })).toBeChecked();
  await dialog.getByRole("textbox", { name: "Name" }).fill("Support tickets");
  await dialog.getByRole("textbox", { name: "Reason" }).fill(REASON);
  await dialog.getByRole("button", { name: "Add source" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Support tickets" })).toBeVisible();
  sourceUrl = page.url();
  await expect(page.getByTestId("next-step")).toContainText("Upload an export");

  await page.getByLabel("Export file").setInputFiles(path.join(ROOT, "data/synthetic/onboarding/tickets/tickets.jsonl"));
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByTestId("upload-status")).toContainText("Export accepted", { timeout: 60_000 });
  // The worker checks it: the references without an account are reported, by ID only.
  await expect(page.getByTestId("next-step")).toHaveAttribute("data-action", "map_references", { timeout: 90_000 });
  const references = page.getByRole("table", { name: "Account references in the current export and whether each has an account" });
  await expect(references.getByRole("row").filter({ hasText: "7100001" })).toContainText("No account: held back");
  await expect(page.getByText("Managing this source gives you no access to its content.")).toBeVisible();
  await expect(page.locator("main")).not.toContainText("Quillon");

  // A refused replacement (a zip with nothing this type reads) says why and keeps the
  // current export.
  const empty = Buffer.from([0x50, 0x4b, 0x05, 0x06, ...new Array(18).fill(0)]);
  await page.getByLabel("Export file").setInputFiles({ name: "wrong-export.zip", mimeType: "application/zip", buffer: empty });
  await page.getByRole("button", { name: "Upload a replacement" }).click();
  await expect(page.getByTestId("upload-status")).toContainText("Not accepted:");
  await expect(page.getByTestId("current-import")).toBeVisible();
  await expect(page.getByTestId("next-step")).toHaveAttribute("data-action", "map_references");
});

test("mapping recovery: the reference is mapped to a new account, then the first sync", async () => {
  await page.getByTestId("next-step").getByRole("link", { name: "Map references in Access" }).click();
  const table = page.getByRole("table", { name: "References the latest syncs could not map to an account" });
  const ref = table.getByRole("row").filter({ hasText: "7100001" });
  await ref.getByRole("button", { name: "Map 7100001" }).click();
  let dialog = page.getByRole("dialog", { name: "Map a reference to an account" });
  await dialog.getByRole("button", { name: "Create a new account instead" }).click();
  const create = page.getByRole("dialog", { name: "Create an account" });
  await create.getByRole("textbox", { name: "Name", exact: true }).fill("Ashgrove Transit");
  await expect(create.getByRole("textbox", { name: "Key" })).toHaveValue("ashgrove-transit");
  await create.getByRole("textbox", { name: "Reason" }).fill(REASON);
  await create.getByRole("button", { name: "Create account" }).click();
  dialog = page.getByRole("dialog", { name: "Map a reference to an account" });
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByTestId("now-after")).toContainText("Not mapped");
  await expect(dialog.getByTestId("now-after")).toContainText("Ashgrove Transit");
  await expect(dialog.getByTestId("mapping-effect")).toContainText("Nothing indexed changes before that sync");
  await dialog.getByRole("textbox", { name: "Reason" }).fill(REASON);
  await dialog.getByRole("button", { name: "Map reference" }).click();
  await expect(page.getByTestId("applied-change")).toContainText("7100001 → Ashgrove Transit");

  // The first sync, from the source's record; the worker runs it.
  await page.goto(sourceUrl);
  await page.getByRole("button", { name: "Sync now" }).click();
  // Polled while the worker has it; then the run's counts: Ashgrove's two tickets are
  // indexed, Brindle's (still unmapped) is held back -- never indexed without its account.
  await expect(page.getByTestId("run-counts")).toContainText("3 read · 2 new", { timeout: 120_000 });
  await expect(page.getByTestId("job-state")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("Quillon");
});

test("Access: the member is given the account (previewed, with a reason)", async () => {
  await page.goto("/access");
  await page.getByRole("link", { name: "Riley Moss" }).click();
  await page.getByRole("button", { name: "Change access" }).click();
  const dialog = page.getByRole("dialog", { name: "Change Riley Moss's access" });
  await dialog.getByRole("checkbox", { name: "Ashgrove Transit" }).check();
  await dialog.getByRole("button", { name: "Review change" }).click();
  await expect(dialog.getByText("Riley Moss will gain access to Ashgrove Transit content.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Reason" }).fill(REASON);
  await dialog.getByRole("button", { name: "Apply change" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("access-summary")).toContainText("Ashgrove Transit");
});

test("the setup record points at what is left: references to map, then a first question", async () => {
  await page.goto("/settings");
  await expect(row(page, "Sources")).toContainText("Complete");
  // Brindle's reference is still unmapped: the record says so and links to the mapping.
  await expect(row(page, "Account mapping")).toContainText("Needs attention");
  await expect(row(page, "Account mapping").getByRole("link", { name: "Map references" })).toHaveAttribute("href", "/access/accounts/references");
  await expect(row(page, "First answer")).toContainText("Required");
  await row(page, "First answer").getByRole("link", { name: "Ask a question" }).click();
  await expect(page).toHaveURL(/\/ask$/);
});

test("the member asks, opens the evidence and the trace; the record shows the first answer", async ({ browser }) => {
  const member = await browser.newContext({ ignoreHTTPSErrors: true });
  const riley = await member.newPage();
  await signIn(riley, loginKey, "wrn-riley");
  await riley.getByRole("textbox", { name: "Question" }).fill(QUESTION);
  await riley.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect(riley.getByTestId("answer")).toBeVisible({ timeout: 60_000 });
  // Evidence only: no generation provider is enabled -- said so, and the evidence shown.
  await expect(riley.getByRole("heading", { name: "Sources found — no AI model available" })).toBeVisible();
  const exhibits = riley.getByTestId("evidence");
  await expect(exhibits).toContainText("WRN-1001");
  await exhibits.getByRole("button", { name: /Open in inspector/ }).first().click();
  await expect(riley.getByTestId("evidence-inspector")).toContainText("4.2.7");
  await riley.getByTestId("evidence-inspector").getByRole("button", { name: "Close evidence" }).click();
  await riley.getByRole("link", { name: "Trace", exact: true }).click();
  await expect(riley).toHaveURL(/\/traces\//);
  await expect(riley.getByRole("heading", { level: 1 })).toBeVisible();
  await member.close();

  // The administrator: the record says so; their own Ask finds nothing (admin ≠ content).
  await page.goto("/settings");
  await expect(row(page, "First answer")).toContainText("Complete");
  await page.goto("/ask");
  await page.getByRole("textbox", { name: "Question" }).fill(QUESTION);
  await page.getByRole("textbox", { name: "Question" }).press("Enter");
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator("main")).not.toContainText("4.2.7");
  await expect(page.locator("main")).not.toContainText("WRN-1001");
});
