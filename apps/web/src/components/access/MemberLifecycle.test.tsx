import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  IDENTITY_NOT_CONNECTED,
  IDENTITY_REQUIREMENT,
  IDENTITY_REQUIREMENT_EMAIL,
  ME_LENA,
  MEMBER_GRACE_ADMIN,
  MEMBER_MAYA,
  MEMBER_NEW,
  MEMBER_RAE_REINSTATED,
  MEMBER_RAE_REMOVED,
  MEMBERS_PAGE,
  PREVIEW_REINSTATE,
} from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { AccessScreen } from "./AccessScreen";
import { MemberScreen } from "./MemberScreen";

afterEach(() => vi.unstubAllGlobals());

const MEMBERS = "/v1/admin/access/members";
const REQUIREMENT = "/v1/admin/access/identity-requirement";

async function openAdd(routes: Record<string, [number, unknown]>) {
  const calls = mockFetch({ [`GET ${MEMBERS}`]: [200, MEMBERS_PAGE], ...routes });
  renderInWorkspace(<AccessScreen />, { me: ME_LENA });
  await userEvent.click(await screen.findByRole("button", { name: "Add member" }));
  const dialog = await screen.findByRole("dialog", { name: "Add a member" });
  return { calls, dialog };
}

describe("Add member", () => {
  it("explains the trust model: no invitation, the exact identifier, least access", async () => {
    const { dialog } = await openAdd({ [`GET ${REQUIREMENT}`]: [200, IDENTITY_REQUIREMENT] });
    expect(dialog).toHaveTextContent("this is not an invitation");
    expect(await within(dialog).findByText(/which must send exactly the identifier below/)).toHaveTextContent("halden.okta.example");
    expect(dialog).toHaveTextContent("Lattice never matches people by e-mail.");
    expect(dialog).toHaveTextContent(
      "They start with least access: Internal clearance, no accounts, departments, groups or documents, and not an administrator.",
    );
    const identifier = within(dialog).getByRole("textbox", { name: "Identifier from your identity provider" });
    expect(identifier).toHaveAccessibleDescription(/The exact value halden.okta.example sends in the sub claim for this person, including capital letters/);
    expect(identifier).toHaveAttribute("spellcheck", "false");
  });

  it("says an email claim is still compared exactly", async () => {
    const { dialog } = await openAdd({ [`GET ${REQUIREMENT}`]: [200, IDENTITY_REQUIREMENT_EMAIL] });
    const identifier = await within(dialog).findByRole("textbox", { name: "Identifier from your identity provider" });
    expect(identifier).toHaveAccessibleDescription(/it's compared exactly, and it can differ from the e-mail above/);
  });

  it("is blocked until an identity provider is connected", async () => {
    const { dialog } = await openAdd({ [`GET ${REQUIREMENT}`]: [200, IDENTITY_NOT_CONNECTED] });
    expect(await within(dialog).findByRole("heading", { name: "Connect an identity provider first" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).toBeNull();
  });

  it("validates every field, sends no access, and opens the new member", async () => {
    const { dialog, calls } = await openAdd({
      [`GET ${REQUIREMENT}`]: [200, IDENTITY_REQUIREMENT],
      [`POST ${MEMBERS}`]: [201, MEMBER_NEW],
    });
    await within(dialog).findByRole("textbox", { name: "Name" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(within(dialog).getByText("Enter their name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Enter their e-mail address.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Enter the value your identity provider sends/)).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Name" }), "Sam Achebe");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "E-mail" }), "not an address");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Identifier from your identity provider" }), "00u9sam-achebe");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Joined support");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(within(dialog).getByText(/Enter one e-mail address/)).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByRole("textbox", { name: "E-mail" }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "E-mail" }), "sam.achebe@halden.example");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const post = calls.find((c) => c.method === "POST");
    expect(JSON.parse(post!.body)).toEqual({
      name: "Sam Achebe",
      email: "sam.achebe@halden.example",
      identifier: "00u9sam-achebe",
      reason: "Joined support",
    });
    expect(post!.headers.get("X-Lattice-Request")).toBe("1");
  });

  it("marks the field a member of this organization already has", async () => {
    const { dialog } = await openAdd({
      [`GET ${REQUIREMENT}`]: [200, IDENTITY_REQUIREMENT],
      [`POST ${MEMBERS}`]: [409, { error: { code: "conflict", message: "Taken.", request_id: "r", details: [{ field: "identifier", issue: "taken" }] } }],
    });
    await userEvent.type(await within(dialog).findByRole("textbox", { name: "Name" }), "Sam");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "E-mail" }), "sam@halden.example");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Identifier from your identity provider" }), "00u1lena-brandt");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "r");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    const identifier = within(dialog).getByRole("textbox", { name: "Identifier from your identity provider" });
    await vi.waitFor(() => expect(identifier).toHaveFocus());
    expect(identifier).toHaveAccessibleDescription(/A member of this organization already has this identifier/);
    expect(identifier).toHaveAttribute("aria-invalid", "true");
  });
});

describe("Administrator capability", () => {
  it("previews Now → After and says content access doesn't change", async () => {
    const id = MEMBER_MAYA.member.id;
    const calls = mockFetch({
      [`GET ${MEMBERS}/${id}`]: [200, MEMBER_MAYA],
      [`POST ${MEMBERS}/${id}/administrator/preview`]: [200, { now: false, after: true, content_access_changes: false }],
      [`POST ${MEMBERS}/${id}/administrator`]: [200, { ...MEMBER_MAYA, member: { ...MEMBER_MAYA.member, is_admin: true } }],
    });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Make administrator" }));
    const dialog = await screen.findByRole("dialog", { name: "Make Maya Okafor an administrator" });
    const row = await within(dialog).findByRole("row", { name: /Administrator/ });
    expect(row).toHaveTextContent("Not an administrator");
    expect(row).toHaveTextContent("Gains");
    expect(within(dialog).getByTestId("content-unchanged")).toHaveTextContent("Content access doesn't change.");
    // No typed confirmation: a reason is enough.
    await userEvent.click(within(dialog).getByRole("button", { name: "Make administrator" }));
    expect(within(dialog).getByText("Give a reason for this change.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Second administrator");
    await userEvent.click(within(dialog).getByRole("button", { name: "Make administrator" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const post = calls.find((c) => c.url.endsWith("/administrator"));
    expect(JSON.parse(post!.body)).toEqual({ administrator: true, version: MEMBER_MAYA.version, reason: "Second administrator" });
    expect(screen.getByTestId("applied-change")).toHaveTextContent("Maya Okafor is an administrator");
    expect(screen.getByTestId("applied-change")).toHaveTextContent("Content access unchanged.");
  });

  it("explains the last-administrator refusal", async () => {
    const id = MEMBER_GRACE_ADMIN.member.id;
    mockFetch({
      [`GET ${MEMBERS}/${id}`]: [200, MEMBER_GRACE_ADMIN],
      [`POST ${MEMBERS}/${id}/administrator/preview`]: [200, { now: true, after: false, content_access_changes: false }],
      [`POST ${MEMBERS}/${id}/administrator`]: [
        422,
        { error: { code: "validation_failed", message: "Invalid.", request_id: "r", details: [{ field: "membership", issue: "last_administrator" }] } },
      ],
    });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Remove administrator capability" }));
    const dialog = await screen.findByRole("dialog", { name: /Remove Grace Whitfield's administrator capability/ });
    await within(dialog).findByTestId("content-unchanged");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Rotation");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove capability" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Grace Whitfield is the only active administrator left, so this would leave the organization without one.",
    );
  });

  it("shows a backend block instead of the action", async () => {
    const id = MEMBER_GRACE_ADMIN.member.id;
    const sole = {
      ...MEMBER_GRACE_ADMIN,
      lifecycle: { ...MEMBER_GRACE_ADMIN.lifecycle, revoke_administrator: { available: false, blocked_by: "last_administrator" as const } },
    };
    mockFetch({ [`GET ${MEMBERS}/${id}`]: [200, sole] });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    expect(await screen.findByTestId("blocked-last_administrator")).toHaveTextContent("Grace Whitfield is the only active administrator.");
    expect(screen.queryByRole("button", { name: "Remove administrator capability" })).toBeNull();
  });
});

describe("Reinstate", () => {
  it("previews least access, says nothing is restored, and applies with a reason", async () => {
    const id = MEMBER_RAE_REMOVED.member.id;
    const calls = mockFetch({
      [`GET ${MEMBERS}/${id}`]: [200, MEMBER_RAE_REMOVED],
      [`POST ${MEMBERS}/${id}/reinstate/preview`]: [200, PREVIEW_REINSTATE],
      [`POST ${MEMBERS}/${id}/reinstate`]: [200, MEMBER_RAE_REINSTATED],
    });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    expect(await screen.findByTestId("sign-in-identifier")).toHaveTextContent("Signs in as 00u4rae");
    expect(screen.queryByRole("button", { name: "Change access" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Reinstate" }));
    const dialog = await screen.findByRole("dialog", { name: "Reinstate Rae Lindqvist" });
    expect(dialog).toHaveTextContent("is not restored");
    expect(dialog).toHaveTextContent("Their earlier sessions stay ended.");
    const compare = await within(dialog).findByRole("table", { name: "Now and after this change, rule by rule" });
    expect(within(compare).getByRole("row", { name: /Status/ })).toHaveTextContent("Active");
    expect(within(compare).getByRole("row", { name: /Clearance/ })).toHaveTextContent("Loses");
    expect(within(compare).getByRole("row", { name: /^Administrator/ })).toHaveTextContent("Not an administrator");
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Re-hired");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reinstate" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const post = calls.find((c) => c.url.endsWith("/reinstate"));
    expect(JSON.parse(post!.body)).toEqual({ version: MEMBER_RAE_REMOVED.version, reason: "Re-hired" });
  });

  it("explains why a removed member can't be reinstated here", async () => {
    const id = MEMBER_RAE_REMOVED.member.id;
    const replaced = {
      ...MEMBER_RAE_REMOVED,
      lifecycle: { ...MEMBER_RAE_REMOVED.lifecycle, reinstate: { available: false, blocked_by: "identity_not_current" as const } },
    };
    mockFetch({ [`GET ${MEMBERS}/${id}`]: [200, replaced] });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    expect(await screen.findByTestId("blocked-identity_not_current")).toHaveTextContent("Your Lattice operator can help.");
    expect(screen.queryByRole("button", { name: "Reinstate" })).toBeNull();
  });
});
