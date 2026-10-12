import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ACCOUNT_ACME,
  ACCOUNT_FERNHILL,
  ACCOUNT_IDS,
  ACCOUNT_ORBITAL_ARCHIVED,
  ACCOUNTS_PAGE,
  ME_LENA,
  ME_MAYA,
  PREVIEW_MAP_FERNHILL,
  PREVIEW_REMAP_ACME_KESTREL,
  PREVIEW_UNMAP_CHANNEL,
  UNMAPPED,
} from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { keyFromName, parseAliases } from "./AccountDialogs";
import { AccountScreen } from "./AccountScreen";
import { AccountsScreen } from "./AccountsScreen";
import { mappingEffect } from "./accountsCopy";
import { ReferencesScreen } from "./ReferencesScreen";

afterEach(() => vi.unstubAllGlobals());

const LIST = "/v1/admin/access/accounts";
const UNMAPPED_PATH = "/v1/admin/access/account-references/unmapped";
const PREVIEW = "/v1/admin/access/account-references/preview";

describe("Accounts list", () => {
  it("is a ledger of accounts with their lifecycle, references and assignments", async () => {
    mockFetch({ [`GET ${LIST}`]: [200, ACCOUNTS_PAGE], [`GET ${UNMAPPED_PATH}`]: [200, UNMAPPED] });
    renderInWorkspace(<AccountsScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "Accounts" });
    const acme = await within(table).findByRole("link", { name: "Acme Corp" });
    expect(acme).toHaveAttribute("href", `/access/accounts/${ACCOUNT_IDS.acme}`);
    const orbital = within(table).getByRole("row", { name: /Orbital Freight/ });
    expect(orbital).toHaveTextContent("Archived");
    expect(orbital).toHaveTextContent("Prospect");
    // The references waiting are announced with a way to them -- counts only.
    const notice = await screen.findByTestId("unmapped-notice");
    expect(notice).toHaveTextContent("1 reference from your sources isn't mapped to an account");
    expect(within(notice).getByRole("link", { name: "Review unmapped references" })).toHaveAttribute("href", "/access/accounts/references");
    expect(screen.getByRole("navigation", { name: "Access" })).toHaveTextContent("MembersAccountsAudit log");
  });

  it("is for administrators only", async () => {
    mockFetch({});
    renderInWorkspace(<AccountsScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You need administrator access to view this page" })).toBeInTheDocument();
  });

  it("creates an account with a derived key, a reason and nothing else", async () => {
    const calls = mockFetch({
      [`GET ${LIST}`]: [200, ACCOUNTS_PAGE],
      [`GET ${UNMAPPED_PATH}`]: [200, { items: [] }],
      [`POST ${LIST}`]: [201, ACCOUNT_FERNHILL],
    });
    renderInWorkspace(<AccountsScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Create account" }));
    const dialog = await screen.findByRole("dialog", { name: "Create an account" });
    expect(dialog).toHaveTextContent("Creating it gives nobody access");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create account" }));
    expect(within(dialog).getByText("Enter the account's name.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Fernhill Studios");
    expect(within(dialog).getByRole("textbox", { name: "Key" })).toHaveValue("fernhill-studios");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Other names/ }), "Fernhill, FH");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Signed up");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create account" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(JSON.parse(post.body)).toEqual({
      key: "fernhill-studios",
      name: "Fernhill Studios",
      aliases: ["Fernhill", "FH"],
      status: "active",
      reason: "Signed up",
    });
  });

  it("shows a taken key on its field", async () => {
    mockFetch({
      [`GET ${LIST}`]: [200, ACCOUNTS_PAGE],
      [`GET ${UNMAPPED_PATH}`]: [200, { items: [] }],
      [`POST ${LIST}`]: [409, { error: { code: "conflict", message: "Taken.", request_id: "r", details: [{ field: "key", issue: "taken" }] } }],
    });
    renderInWorkspace(<AccountsScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Create account" }));
    const dialog = await screen.findByRole("dialog", { name: "Create an account" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Acme Corp");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Again");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create account" }));
    const key = within(dialog).getByRole("textbox", { name: "Key" });
    await vi.waitFor(() => expect(key).toHaveAccessibleDescription(/Another account already uses this key/));
    expect(key).toHaveFocus();
  });
});

describe("An account", () => {
  it("shows its record and references, never content", async () => {
    mockFetch({ [`GET ${LIST}/${ACCOUNT_IDS.acme}`]: [200, ACCOUNT_ACME] });
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.acme} />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { level: 1, name: "Acme Corp" })).toBeInTheDocument();
    expect(screen.getByTestId("account-key")).toHaveTextContent("acme-corp");
    expect(screen.getByText("Also Acme, ACME Corp")).toBeInTheDocument();
    const refs = screen.getByRole("table", { name: "References mapped to Acme Corp" });
    expect(within(refs).getAllByRole("row")).toHaveLength(4);
    expect(refs).toHaveTextContent("C0EXTACME01");
    expect(refs).toHaveTextContent("Ticketing organization ID");
    expect(screen.getByRole("button", { name: "Add a reference" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reactivate" })).toBeNull();
    expect(screen.getByText("Managing this account gives you no access to its content.")).toBeInTheDocument();
  });

  it("archived: kept, read-only, reactivatable", async () => {
    const calls = mockFetch({
      [`GET ${LIST}/${ACCOUNT_IDS.orbital}`]: [200, ACCOUNT_ORBITAL_ARCHIVED],
      [`POST ${LIST}/${ACCOUNT_IDS.orbital}/reactivate`]: [200, { ...ACCOUNT_ORBITAL_ARCHIVED, lifecycle: "active", archived_at: null }],
    });
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.orbital} />, { me: ME_LENA });
    expect(await screen.findByTestId("archived-notice")).toHaveTextContent("nothing was deleted or hidden");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a reference" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Reactivate Orbital Freight" });
    expect(within(dialog).getByTestId("now-after")).toHaveTextContent("ArchivedActive");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reactivate" }));
    expect(within(dialog).getByText("Give a reason for this change.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Renewed");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reactivate" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "POST")!.body)).toEqual({ version: ACCOUNT_ORBITAL_ARCHIVED.version, reason: "Renewed" });
  });

  it("archiving says what stays: documents, access and existing references", async () => {
    mockFetch({ [`GET ${LIST}/${ACCOUNT_IDS.acme}`]: [200, ACCOUNT_ACME] });
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.acme} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
    const dialog = await screen.findByRole("dialog", { name: "Archive Acme Corp" });
    const effects = within(dialog).getByTestId("lifecycle-effects");
    expect(effects).toHaveTextContent("Its documents keep their account");
    expect(effects).toHaveTextContent("Its 3 references keep working");
    expect(effects).toHaveTextContent("No new reference can be mapped to it");
    expect(dialog).toHaveTextContent("Nobody's access changes.");
  });

  it("moves a reference only after an API preview and a reason", async () => {
    const calls = mockFetch({
      [`GET ${LIST}/${ACCOUNT_IDS.acme}`]: [200, ACCOUNT_ACME],
      [`GET ${LIST}`]: [200, ACCOUNTS_PAGE],
      [`POST ${PREVIEW}`]: [200, PREVIEW_REMAP_ACME_KESTREL],
      ["POST /v1/admin/access/account-references/remap"]: [
        200,
        { operation: "remap", source: PREVIEW_REMAP_ACME_KESTREL.source, ref_kind: "ticketing_organization", external_ref: "360001001", account: PREVIEW_REMAP_ACME_KESTREL.after, takes_effect: "next_sync" },
      ],
    });
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.acme} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Move 360001001 to another account" }));
    const dialog = await screen.findByRole("dialog", { name: "Move a reference to another account" });
    expect(within(dialog).getByTestId("reference-facts")).toHaveTextContent("360001001");
    // The current account is not offered as a target.
    const kestrel = await within(dialog).findByRole("radio", { name: /Kestrel Bank/ });
    expect(within(dialog).queryByRole("radio", { name: /Acme Corp/ })).toBeNull();
    await userEvent.click(kestrel);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByTestId("now-after")).toHaveTextContent("Acme CorpKestrel Bank");
    expect(within(dialog).getByTestId("mapping-effect")).toHaveTextContent("move from Acme Corp to Kestrel Bank. Until then they stay with Acme Corp.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Move reference" }));
    expect(within(dialog).getByText("Give a reason for this change.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Acquired");
    await userEvent.click(within(dialog).getByRole("button", { name: "Move reference" }));
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith("/remap"))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/remap"))!.body)).toEqual({
      source_id: PREVIEW_REMAP_ACME_KESTREL.source.id,
      ref_kind: "ticketing_organization",
      external_ref: "360001001",
      current_account_id: ACCOUNT_IDS.acme,
      account_id: ACCOUNT_IDS.kestrel,
      reason: "Acquired",
    });
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("Sync Halden Support (Zendesk) now");
  });

  it("warns when removing a mapping widens who can read", async () => {
    mockFetch({ [`GET ${LIST}/${ACCOUNT_IDS.acme}`]: [200, ACCOUNT_ACME], [`POST ${PREVIEW}`]: [200, PREVIEW_UNMAP_CHANNEL] });
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.acme} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Remove the mapping of C0EXTACME01" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove a reference's mapping" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText("This widens who can read these records")).toBeInTheDocument();
    expect(within(dialog).getByTestId("mapping-effect")).toHaveTextContent("are no longer Acme Corp's");
  });

  it("an unknown or foreign account is simply not available", async () => {
    mockFetch({});
    renderInWorkspace(<AccountScreen id={ACCOUNT_IDS.fernhill} />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "This account isn't available" })).toBeInTheDocument();
  });
});

describe("Unmapped references", () => {
  it("lists references with counts only and maps one to a chosen account", async () => {
    const calls = mockFetch({
      [`GET ${UNMAPPED_PATH}`]: [200, UNMAPPED],
      [`GET ${LIST}`]: [200, { items: [{ ...ACCOUNTS_PAGE.items[0]! }, { ...ACCOUNT_FERNHILL, mappings: undefined }], next_after: null }],
      [`POST ${PREVIEW}`]: [200, PREVIEW_MAP_FERNHILL],
      ["POST /v1/admin/access/account-references"]: [
        201,
        { operation: "map", source: PREVIEW_MAP_FERNHILL.source, ref_kind: "ticketing_organization", external_ref: "360009913", account: PREVIEW_MAP_FERNHILL.after, takes_effect: "next_sync" },
      ],
    });
    renderInWorkspace(<ReferencesScreen />, { me: ME_LENA });
    const table = await screen.findByRole("table", { name: "References the latest syncs could not map to an account" });
    const row = within(table).getByRole("row", { name: /360009913/ });
    expect(row).toHaveTextContent("Halden Support (Zendesk)");
    expect(row).toHaveTextContent("Not mapped");
    await userEvent.click(within(row).getByRole("button", { name: "Map 360009913" }));
    const dialog = await screen.findByRole("dialog", { name: "Map a reference to an account" });
    // No suggestion: nothing is pre-selected.
    expect(within(dialog).queryByRole("radio", { checked: true })).toBeNull();
    await userEvent.click(await within(dialog).findByRole("radio", { name: /Fernhill Studios/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByTestId("mapping-effect")).toHaveTextContent(
      "At the next sync of Halden Support (Zendesk): The record waiting for Ticketing organization 360009913 is indexed under Fernhill Studios.",
    );
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "New customer");
    await userEvent.click(within(dialog).getByRole("button", { name: "Map reference" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/v1/admin/access/account-references")).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url === "/v1/admin/access/account-references")!;
    expect(JSON.parse(post.body)).toEqual({
      source_id: PREVIEW_MAP_FERNHILL.source.id,
      ref_kind: "ticketing_organization",
      external_ref: "360009913",
      account_id: ACCOUNT_IDS.fernhill,
      reason: "New customer",
    });
  });

  it("says when every reference is mapped", async () => {
    mockFetch({ [`GET ${UNMAPPED_PATH}`]: [200, { items: [] }] });
    renderInWorkspace(<ReferencesScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "Every reference is mapped" })).toBeInTheDocument();
  });
});

describe("Account copy", () => {
  it("derives a key from a name", () => {
    expect(keyFromName("Fernhill Studios")).toBe("fernhill-studios");
    expect(keyFromName("  Café & Co. (EMEA) ")).toBe("cafe-co-emea");
    expect(keyFromName("---")).toBe("");
  });

  it("reads other names one per line or comma-separated", () => {
    expect(parseAliases("Acme, ACME Corp\n\n Acme Inc ")).toEqual(["Acme", "ACME Corp", "Acme Inc"]);
  });

  it("states each effect at the next sync", () => {
    expect(mappingEffect(PREVIEW_REMAP_ACME_KESTREL)).toBe(
      "Records from Ticketing organization 360001001 move from Acme Corp to Kestrel Bank. Until then they stay with Acme Corp.",
    );
    expect(mappingEffect({ ...PREVIEW_UNMAP_CHANNEL, account_scoped: true, effect: "quarantined" })).toBe(
      "Records from Channel C0EXTACME01 stop being readable until the reference is mapped again. Nothing is deleted.",
    );
  });
});
