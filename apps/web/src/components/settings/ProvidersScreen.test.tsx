import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  EGRESS_DEFAULT,
  ME_LENA,
  ME_MAYA,
  PREVIEW_APPROVE_GEMINI,
  PREVIEW_LOWER_EXTERNAL,
  PROVIDER_GEMINI_NEW,
  PROVIDER_ID,
  PROVIDERS,
  PROVIDERS_EMPTY,
  PROVIDERS_GEMINI_NEW,
  providerSettings,
  STEP_UP_GRANTED,
  STEP_UP_NOT_FRESH,
} from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { StepUpResult } from "./ProviderDialogs";
import { ProvidersScreen } from "./ProvidersScreen";

afterEach(() => vi.unstubAllGlobals());

const API = "/v1/admin/providers";
const lattice = () => screen.findByRole("table", { name: "Which data levels each AI provider may receive" });
const KEY = "AIzaSy-unit-test-key-0123456789-WXYZ";

describe("ProvidersScreen", () => {
  it("draws the server's lattice: every provider × level a permitted or a boundary cell, named in words", async () => {
    mockFetch({ [`GET ${API}`]: [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(expect.arrayContaining(["Generation", "Embedding"]));
    expect(
      within(table).getByRole("button", { name: "Confidential data may be sent to ollama · qwen3.5:4b: it runs inside the Lattice deployment, and it's approved up to Confidential." }),
    ).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Restricted data isn't sent to ollama · qwen3.5:4b: it's approved only up to Confidential." })).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Internal data isn't sent to gemini · gemini-3.5-flash-lite: the provider is disabled, so it isn't called." })).toBeInTheDocument();
    expect(table.querySelectorAll("[class*='filled']").length + table.querySelectorAll("[class*='hatched']").length).toBe(9);
    expect(screen.getByTestId("never-sent")).toHaveTextContent("Every level may reach at least one provider");
  });

  it("renders cells from the server's reading, never its own: a cell the server permits is filled", async () => {
    // The fixture says permitted although the record is disabled: the page must not recompute.
    const odd = { ...PROVIDERS, lattice: PROVIDERS.lattice.map((c) => (c.provider_id === PROVIDER_ID.gemini && c.level === "internal" ? { ...c, permitted: true, reason: "permitted" } : c)) };
    mockFetch({ [`GET ${API}`]: [200, odd] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    expect(within(table).getByRole("button", { name: /^Internal data may be sent to gemini/ })).toBeInTheDocument();
  });

  it("moves through the cells by keyboard and explains the one in focus", async () => {
    mockFetch({ [`GET ${API}`]: [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const table = await lattice();
    const first = within(table).getAllByRole("button")[0]!;
    expect(first).toHaveAttribute("tabindex", "0");
    act(() => first.focus());
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Internal data may be sent to ollama · qwen3.5:4b");
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByTestId("egress-why")).toHaveTextContent("Internal data isn't sent to gemini");
    await userEvent.keyboard("{End}");
    expect(screen.getByTestId("egress-why")).toHaveTextContent(/^Internal data may be sent to local · mxbai-embed-large-v1/);
  });

  it("keeps the seven facts apart on each provider record, and never shows a key", async () => {
    mockFetch({ [`GET ${API}`]: [200, PROVIDERS] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const gemini = (await screen.findAllByTestId("provider-record")).find((r) => r.dataset.provider === "gemini")!;
    expect(gemini).toHaveTextContent("Sends nothing: disabled");
    expect(gemini).toHaveTextContent("Approved by an administrator");
    expect(gemini).toHaveTextContent("External API");
    expect(gemini).toHaveTextContent("The provider's public API, https://generativelanguage.googleapis.com");
    expect(gemini).toHaveTextContent("Set by your Lattice operator in the deployment");
    expect(gemini).toHaveTextContent("can't be opened");
    expect(gemini).toHaveTextContent("Not tested");
    expect(document.body.textContent).not.toMatch(/LATTICE_PROVIDER_SECRET|AIza|vault:|env:/);
  });

  it("says a working key sends nothing more when policy excludes the provider", async () => {
    mockFetch({ [`GET ${API}`]: [200, PROVIDERS_GEMINI_NEW] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    const record = await screen.findByTestId("provider-record");
    expect(record).toHaveTextContent("Works ·");
    expect(record).toHaveTextContent("Sends nothing: disabled");
    expect(record).toHaveTextContent("Not approved: nothing is sent to it");
    expect(record).toHaveTextContent("Set in Lattice · ends ••••WXYZ");
    expect(screen.getByTestId("ai-status")).toHaveTextContent("configured but not enabled");
  });

  it("a fresh organization is evidence only and can add a provider", async () => {
    mockFetch({ [`GET ${API}`]: [200, PROVIDERS_EMPTY] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "No AI providers are configured" })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Which data levels each AI provider may receive" })).toBeNull();
    expect(screen.getByTestId("ai-status")).toHaveAttribute("data-reason", "evidence_only");
    expect(screen.getByRole("button", { name: "Add provider" })).toBeInTheDocument();
  });

  it("adds a provider disabled and unapproved, with a reason", async () => {
    const calls = mockFetch({ [`GET ${API}`]: [200, PROVIDERS_EMPTY], [`POST ${API}`]: [201, PROVIDERS_GEMINI_NEW] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a generation provider" });
    expect(dialog).toHaveTextContent("starts disabled and unapproved");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Answer drafting");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add provider" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === API)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === "POST" && c.url === API)!.body)).toEqual({
      provider: "gemini",
      model: "gemini-3.5-flash-lite",
      training_opt_out: true,
      reason: "Answer drafting",
    });
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("Provider added");
  });

  it("takes an API key in an empty masked field, sends it once and forgets it", async () => {
    const calls = mockFetch({ [`GET ${API}`]: [200, PROVIDERS_GEMINI_NEW], [`PUT ${API}/${PROVIDER_ID.gemini}/credential`]: [200, PROVIDERS_GEMINI_NEW] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for gemini · gemini-3.5-flash-lite" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Replace key…" }));
    const dialog = await screen.findByRole("dialog", { name: "Replace the API key of gemini" });
    const field = within(dialog).getByTestId("api-key-input");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("autocomplete", "new-password");
    expect(dialog).toHaveTextContent("never shown again");
    await userEvent.type(field, KEY);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Rotation");
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace key" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const sent = JSON.parse(calls.find((c) => c.method === "PUT")!.body);
    expect(sent).toEqual({ api_key: KEY, data_use: "paid", expected_credential_id: PROVIDER_GEMINI_NEW.credential.id, reason: "Rotation" });
    expect(await screen.findByTestId("applied-change")).toHaveTextContent("API key replaced");
    expect(document.body.innerHTML).not.toContain(KEY);
    // Only the credential request ever carried it.
    expect(calls.filter((c) => c.body.includes(KEY))).toHaveLength(1);
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0);
  });

  it("a lowering change applies with a reason and no step-up", async () => {
    const calls = mockFetch({
      [`GET ${API}`]: [200, PROVIDERS],
      [`POST ${API}/changes/preview`]: [200, PREVIEW_LOWER_EXTERNAL],
      [`POST ${API}/changes`]: [200, providerSettings({ ...EGRESS_DEFAULT, external_egress_level: null }, [])],
    });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Change policy" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the AI data policy" });
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "External APIs" }), "none");
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByRole("heading", { name: "This doesn't raise anything" })).toBeInTheDocument();
    expect(within(dialog).getByTestId("remaining")).toHaveTextContent("External APIs receive nothing.");
    expect(within(dialog).queryByTestId("confirmation")).toBeNull();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Incident");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === `${API}/changes`)).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === `${API}/changes`)!.body)).toEqual({
      change: { kind: "settings", external_egress_level: null },
      policy_version: 7,
      reason: "Incident",
    });
    expect(calls.some((c) => c.url === `${API}/step-ups`)).toBe(false);
  });

  it("an escalation shows Now → After, asks for the organization's name and a reason, then leaves for a fresh sign-in", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const calls = mockFetch({
      [`GET ${API}`]: [200, PROVIDERS_GEMINI_NEW],
      [`POST ${API}/changes/preview`]: [200, PREVIEW_APPROVE_GEMINI],
      [`POST ${API}/step-ups`]: [201, { step_up_id: STEP_UP_GRANTED.id, authorization_url: "https://idp.halden.example/authorize?x=1" }],
    });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for gemini · gemini-3.5-flash-lite" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Approve…" }));
    const dialog = await screen.findByRole("dialog", { name: "Approval of gemini · gemini-3.5-flash-lite" });
    await userEvent.click(within(dialog).getByRole("radio", { name: "Internal" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByRole("heading", { name: "This raises what may leave Lattice" })).toBeInTheDocument();
    expect(within(dialog).getByTestId("newly-allowed")).toHaveTextContent(
      "Internal content may now be sent to gemini · gemini-3.5-flash-lite (External API, https://generativelanguage.googleapis.com).",
    );
    const submit = within(dialog).getByRole("button", { name: "Continue to sign in again" });
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Security review 42");
    await userEvent.type(within(dialog).getByTestId("confirmation"), "Halden");
    await userEvent.click(submit);
    expect(await within(dialog).findByText("Type Halden Logistics exactly.")).toBeInTheDocument();
    expect(calls.some((c) => c.url === `${API}/step-ups`)).toBe(false);
    await userEvent.clear(within(dialog).getByTestId("confirmation"));
    await userEvent.type(within(dialog).getByTestId("confirmation"), "Halden Logistics");
    await userEvent.click(submit);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://idp.halden.example/authorize?x=1"));
    expect(JSON.parse(calls.find((c) => c.url === `${API}/step-ups`)!.body)).toEqual({
      change: { kind: "provider", provider_id: PROVIDER_ID.gemini, approval: "internal" },
      policy_version: 7,
      reason: "Security review 42",
      confirmation: "Halden Logistics",
    });
    expect(calls.some((c) => c.url === `${API}/changes`)).toBe(false); // nothing applied yet
  });

  it("refuses to offer a raise it can't confirm", async () => {
    mockFetch({
      [`GET ${API}`]: [200, { ...PROVIDERS_GEMINI_NEW, step_up: { available: false, reason: "no_connection", assurance_required: null, max_age_seconds: 300 } }],
      [`POST ${API}/changes/preview`]: [200, PREVIEW_APPROVE_GEMINI],
    });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByTestId("step-up-rule")).toHaveTextContent("ask your Lattice operator");
    await userEvent.click(screen.getByRole("button", { name: "Actions for gemini · gemini-3.5-flash-lite" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Approve…" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("radio", { name: "Internal" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText(/doesn't sign in through one/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Continue to sign in again" })).toBeDisabled();
  });

  it("a test result says what it proves -- and nothing more", async () => {
    mockFetch({
      [`GET ${API}`]: [200, PROVIDERS_GEMINI_NEW],
      [`POST ${API}/${PROVIDER_ID.gemini}/test`]: [200, { provider_id: PROVIDER_ID.gemini, result: "valid", at: "2026-10-17T09:30:00Z", note: "x" }],
    });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Test" }));
    expect(await screen.findByText(/it approves nothing and lets nothing more leave Lattice/)).toBeInTheDocument();
  });

  it("members get the permission page and the API is never asked", async () => {
    const calls = mockFetch({});
    renderInWorkspace(<ProvidersScreen />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: /You need administrator access/ })).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("a revoked capability gets the same page", async () => {
    mockFetch({ [`GET ${API}`]: [403, { error: { code: "permission_denied", message: "x", request_id: "r" } }] });
    renderInWorkspace(<ProvidersScreen />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: /You need administrator access/ })).toBeInTheDocument();
  });
});

describe("StepUpResult", () => {
  it("shows the recomputed Now → After and applies exactly once on request", async () => {
    const calls = mockFetch({
      [`GET ${API}/step-ups/${STEP_UP_GRANTED.id}`]: [200, STEP_UP_GRANTED],
      [`POST ${API}/step-ups/${STEP_UP_GRANTED.id}/apply`]: [200, PROVIDERS],
    });
    const onApplied = vi.fn();
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={onApplied} />, { me: ME_LENA });
    const result = await screen.findByTestId("step-up-result");
    expect(result).toHaveTextContent("Signed in again");
    expect(result).toHaveTextContent("whether it used a second factor is up to your identity provider");
    expect(result).not.toHaveTextContent(/multi-factor|MFA/);
    expect(within(result).getByTestId("newly-allowed")).toHaveTextContent("Internal content may now be sent to gemini");
    expect(calls.some((c) => c.method === "POST")).toBe(false); // coming back applies nothing
    await userEvent.click(within(result).getByRole("button", { name: "Apply this change" }));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("reports the asserted assurance only when the operator required one", async () => {
    mockFetch({ [`GET ${API}/step-ups/${STEP_UP_GRANTED.id}`]: [200, { ...STEP_UP_GRANTED, grant: { ...STEP_UP_GRANTED.grant!, assurance: "urn:halden:mfa" } }] });
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={vi.fn()} />, { me: ME_LENA });
    expect(await screen.findByTestId("step-up-facts")).toHaveTextContent("the sign-in method your Lattice operator requires (urn:halden:mfa)");
  });

  it("a refused sign-in applies nothing and says why", async () => {
    mockFetch({ [`GET ${API}/step-ups/${STEP_UP_GRANTED.id}`]: [200, STEP_UP_NOT_FRESH] });
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={vi.fn()} />, { me: ME_LENA });
    const result = await screen.findByTestId("step-up-result");
    expect(result).toHaveTextContent("Not a fresh sign-in");
    expect(result).toHaveTextContent("Nothing changed");
    expect(within(result).queryByRole("button", { name: "Apply this change" })).toBeNull();
  });

  it("a policy changed meanwhile, or a used grant, can't be applied", async () => {
    mockFetch({ [`GET ${API}/step-ups/${STEP_UP_GRANTED.id}`]: [200, { ...STEP_UP_GRANTED, current_version: 8 }] });
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={vi.fn()} />, { me: ME_LENA });
    const result = await screen.findByTestId("step-up-result");
    expect(result).toHaveTextContent("The AI data policy changed after you reviewed this");
    expect(within(result).queryByRole("button", { name: "Apply this change" })).toBeNull();
  });

  it("a stale grant answered 409 on apply is explained", async () => {
    mockFetch({
      [`GET ${API}/step-ups/${STEP_UP_GRANTED.id}`]: [200, STEP_UP_GRANTED],
      [`POST ${API}/step-ups/${STEP_UP_GRANTED.id}/apply`]: [
        409,
        { error: { code: "conflict", message: "x", request_id: "r", details: [{ field: "step_up", issue: "grant_expired" }] } },
      ],
    });
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={vi.fn()} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Apply this change" }));
    expect(await screen.findByText(/More than five minutes passed since you signed in again/)).toBeInTheDocument();
  });

  it("someone else's step-up is simply not available", async () => {
    mockFetch({});
    renderInWorkspace(<StepUpResult id={STEP_UP_GRANTED.id} onDismiss={vi.fn()} onApplied={vi.fn()} />, { me: ME_LENA });
    expect(await screen.findByText(/isn't yours, or it has gone. Nothing changed./)).toBeInTheDocument();
  });
});

