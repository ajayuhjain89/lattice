import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CONNECTION_ID,
  IDENTITY_BLOCKED,
  IDENTITY_DISABLED,
  IDENTITY_NONE,
  IDENTITY_VERIFIED,
  ME_LENA,
  ME_MAYA,
  PROVISIONING_ACTIVITY,
  REVEALED_TOKEN,
  TOKEN_IDS,
} from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { checkMessage, testMessage } from "./identity-copy";
import { IdentityScreen, TestResult } from "./IdentityScreen";

afterEach(() => vi.unstubAllGlobals());

const API = "/v1/admin/identity";
const OIDC = `${API}/oidc/${CONNECTION_ID}`;
const BASE = {
  [`GET ${API}`]: [200, IDENTITY_VERIFIED] as [number, unknown],
  "GET /v1/admin/audit-events": [200, PROVISIONING_ACTIVITY] as [number, unknown],
};

async function open() {
  renderInWorkspace(<IdentityScreen />, { me: ME_LENA });
  return screen.findByTestId("connection-facts");
}

describe("IdentityScreen", () => {
  it("shows the connection and the tokens as the API states them, never a secret", async () => {
    mockFetch(BASE);
    const facts = await open();
    expect(facts).toHaveTextContent("https://login.halden.example");
    expect(facts).toHaveTextContent("Public address");
    expect(facts).toHaveTextContent("lattice-halden");
    expect(facts).toHaveTextContent("Set in Lattice, encrypted");
    expect(facts).toHaveTextContent("14 members");
    expect(screen.getByRole("button", { name: "Copy redirect URI" })).toBeInTheDocument();
    const signIn = screen.getByRole("region", { name: "Sign-in" });
    expect(within(signIn).getByText("Sign-in through the identity provider works.")).toBeInTheDocument();
    const tokens = screen.getByRole("table", { name: "Provisioning tokens" });
    expect(within(tokens).getByText("Token ending Qx7f")).toBeInTheDocument();
    expect(within(tokens).getByText(/Revoked 9 Oct 2026/)).toBeInTheDocument();
    expect(within(tokens).getAllByRole("button", { name: /Revoke token ending Qx7f/ })).toHaveLength(1);
    expect(await screen.findByText(/Request refused: the person exists as a member added by hand \(Maya Okafor\)/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/vault:|lsc_|env:LATTICE/);
  });

  it("is for administrators only", async () => {
    mockFetch(BASE);
    renderInWorkspace(<IdentityScreen />, { me: ME_MAYA });
    expect(await screen.findByText("You need administrator access to view this page")).toBeInTheDocument();
  });

  it("offers to connect when there is no connection, and asks for a secret only for a confidential client", async () => {
    const calls = mockFetch({ ...BASE, [`GET ${API}`]: [200, IDENTITY_NONE], [`POST ${API}/oidc`]: [201, IDENTITY_VERIFIED] });
    renderInWorkspace(<IdentityScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Connect identity provider" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect an identity provider" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Issuer/ }), "https://login.halden.example");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Client ID/ }), "lattice-halden");
    const secret = within(dialog).getByTestId("client-secret-input");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveValue("");
    await userEvent.type(secret, "s3cr3t-value");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Go live");
    await userEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === `${API}/oidc`)).toBe(true));
    const sent = JSON.parse(calls.find((c) => c.url === `${API}/oidc`)!.body);
    expect(sent).toEqual({ issuer: "https://login.halden.example", client_id: "lattice-halden", subject_claim: "sub", client_secret: "s3cr3t-value", reason: "Go live" });
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("Identity provider connected");
    expect(document.body.textContent).not.toContain("s3cr3t-value");
  });

  it("replaces the client secret through a masked field that starts empty, with the version and a reason", async () => {
    const calls = mockFetch({ ...BASE, [`PUT ${OIDC}/client-secret`]: [200, IDENTITY_VERIFIED] });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Replace client secret" }));
    const dialog = await screen.findByRole("dialog", { name: "Replace the client secret" });
    expect(dialog).toHaveTextContent("replaces the current one at once");
    const field = within(dialog).getByLabelText(/New client secret/);
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "new-password");
    expect(field).toHaveValue("");
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace secret" }));
    expect(within(dialog).getByText("Paste the new client secret.")).toBeInTheDocument();
    await userEvent.type(field, "next-secret");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Rotation");
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace secret" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "PUT")!.body)).toEqual({ client_secret: "next-secret", version: IDENTITY_VERIFIED.oidc!.version, reason: "Rotation" });
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("Client secret replaced");
  });

  it("previews disabling: sessions end, including the administrator's own", async () => {
    const calls = mockFetch({
      ...BASE,
      [`POST ${OIDC}/status/preview`]: [200, { enabled: false, sessions_ending: 6, members_bound: 14, ends_own_session: true }],
      [`POST ${OIDC}/status`]: [200, IDENTITY_DISABLED],
    });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Disable sign-in" }));
    const dialog = await screen.findByRole("dialog", { name: "Disable sign-in" });
    const effects = await within(dialog).findByTestId("status-effects");
    expect(effects).toHaveTextContent("6 sessions end at once");
    expect(effects).toHaveTextContent("Yours too");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Incident");
    await userEvent.click(within(dialog).getByRole("button", { name: "Disable sign-in" }));
    await waitFor(() => expect(calls.some((c) => c.url === `${OIDC}/status`)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === `${OIDC}/status`)!.body)).toMatchObject({ enabled: false, reason: "Incident" });
  });

  it("needs the administrator's matched test sign-in before an issuer change can be applied", async () => {
    const preview = {
      issuer: "https://new.halden.example",
      subject_claim: "sub",
      issuer_changed: true,
      claim_changed: false,
      issuer_access: "public",
      sessions_ending: 6,
      members_bound: 14,
      ends_own_session: false,
      proof: null,
    };
    mockFetch({ ...BASE, [`POST ${OIDC}/identity/preview`]: [200, preview] });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Change issuer or claim" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the issuer or identifying claim" });
    expect(dialog).toHaveTextContent("Moving to a different identity provider is not supported");
    const issuer = within(dialog).getByRole("textbox", { name: /Issuer/ });
    await userEvent.clear(issuer);
    await userEvent.type(issuer, "https://new.halden.example");
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText("Needs your test sign-in with these values")).toBeInTheDocument();
    expect(within(dialog).getByTestId("identity-effects")).toHaveTextContent("Every session of this connection ends at once: 6 sessions.");
    expect(within(dialog).getByRole("button", { name: "Apply change" })).toBeDisabled();
    expect(within(dialog).queryByRole("textbox", { name: /to confirm/ })).not.toBeInTheDocument();
  });

  it("creates a token, shows it once, and drops it when the dialog closes", async () => {
    const calls = mockFetch({ ...BASE, [`POST ${API}/scim/tokens`]: [201, REVEALED_TOKEN] });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));
    const dialog = await screen.findByRole("dialog", { name: "Create a provisioning token" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Okta");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create token" }));
    const revealed = await within(dialog).findByTestId("revealed-token");
    expect(within(revealed).getByLabelText("Provisioning token")).toHaveValue(REVEALED_TOKEN.revealed_token);
    expect(revealed).toHaveTextContent("Shown only now");
    expect(calls.filter((c) => c.url === `${API}/scim/tokens`)).toHaveLength(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "I've stored it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain(REVEALED_TOKEN.revealed_token);
  });

  it("previews revoking the last live token", async () => {
    const calls = mockFetch({
      ...BASE,
      [`POST ${API}/scim/tokens/${TOKEN_IDS.live}/revoke/preview`]: [200, { last_live_token: true, provisioned_members: 12 }],
      [`POST ${API}/scim/tokens/${TOKEN_IDS.live}/revoke`]: [200, { ...IDENTITY_VERIFIED.scim.tokens[0], revoked_at: "2026-10-13T10:00:00Z" }],
    });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Revoke token ending Qx7f" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke a provisioning token" });
    expect(await within(dialog).findByTestId("revoke-effects")).toHaveTextContent("It's the last live token");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "IdP replaced");
    await userEvent.click(within(dialog).getByRole("button", { name: "Revoke token" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/revoke"))).toBe(true));
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("Token ending Qx7f revoked");
  });

  it("explains a blocked private issuer, and checks the connection on request", async () => {
    mockFetch({
      ...BASE,
      [`GET ${API}`]: [200, IDENTITY_BLOCKED],
      [`POST ${OIDC}/check`]: [200, { result: "outbound_blocked", step: "issuer", checked_at: "2026-10-13T10:00:00Z" }],
    });
    await open();
    expect(screen.getByText("Waiting for your Lattice operator")).toBeInTheDocument();
    expect(screen.getByText("Refused by the outbound policy")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByTestId("check-result")).toHaveTextContent("outbound policy refuses this address");
  });

  it("offers to enable a disabled connection, and nothing else that would need it active", async () => {
    mockFetch({ ...BASE, [`GET ${API}`]: [200, IDENTITY_DISABLED] });
    await open();
    expect(screen.getByRole("button", { name: "Enable sign-in" })).toBeInTheDocument();
    for (const name of ["Test sign-in", "Check connection", "Replace client secret", "Create token"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});

describe("TestResult", () => {
  it("shows the administrator's own identifier and the way on, for a matched test of proposed values", async () => {
    const result = {
      test_id: "c0ffee00-0000-4000-8000-000000000001",
      status: "completed",
      outcome: "matched",
      identifier: "sub-lena",
      issuer: "https://new.halden.example",
      subject_claim: "sub",
      proposed: true,
      completed_at: "2026-10-13T10:00:00Z",
      expires_at: "2026-10-13T10:10:00Z",
      proof_available: true,
    };
    mockFetch({ [`GET ${API}/oidc/test-sign-in/${result.test_id}`]: [200, result] });
    const onContinue = vi.fn();
    renderInWorkspace(<TestResult id={result.test_id} onDismiss={vi.fn()} onContinue={onContinue} />, { me: ME_LENA });
    const panel = await screen.findByTestId("test-result");
    expect(panel).toHaveTextContent("Test sign-in matched your identity");
    expect(panel).toHaveTextContent("sub: sub-lena");
    await userEvent.click(within(panel).getByRole("button", { name: "Continue the change" }));
    expect(onContinue).toHaveBeenCalledWith("https://new.halden.example", "sub");
  });

  it("calls a mismatch under proposed values what it is: a changed-subject migration Lattice doesn't do", () => {
    expect(
      testMessage({
        test_id: "x",
        status: "completed",
        outcome: "mismatched",
        identifier: "lena@halden.example",
        issuer: "https://login.halden.example",
        subject_claim: "email",
        proposed: true,
        completed_at: null,
        expires_at: "2026-10-13T10:10:00Z",
        proof_available: false,
      }),
    ).toMatch(/Existing members would not be recognised.*doesn't support/);
    expect(checkMessage({ result: "valid", step: "client", checked_at: "2026-10-13T10:00:00Z" })).toMatch(/The connection works/);
  });
});
