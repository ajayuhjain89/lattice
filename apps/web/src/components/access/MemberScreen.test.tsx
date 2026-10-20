import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ID,
  ME_LENA,
  ME_MAYA,
  MEMBER_LENA_SELF,
  MEMBER_MAYA,
  MEMBER_PAT_MANAGED,
  NOT_FOUND,
  PREVIEW_ADD_KESTREL,
  DIRECTORY,
  DOCUMENTS,
  EXPLANATION_DENIED,
} from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { MemberScreen } from "./MemberScreen";

afterEach(() => vi.unstubAllGlobals());

const MAYA = `/v1/admin/access/members/${ID.maya}`;

describe("MemberScreen", () => {
  it("explains a member's access without naming what the administrator can't see", async () => {
    mockFetch({ [`GET ${MAYA}`]: [200, MEMBER_MAYA] });
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    const summary = await screen.findByTestId("access-summary");
    expect(summary).toHaveTextContent("Maya Okafor can see Acme Corp and Northpeak Logistics content (assigned accounts)");
    expect(screen.getByText(/^Administrator-defined · 2 members/)).toBeInTheDocument();
    // D-18: one acknowledgement of private channels -- no name, id or count.
    const reach = screen.getByTestId("reach");
    expect(within(reach).getByText("Also in private channels you aren't in")).toBeInTheDocument();
    expect(reach.textContent).not.toMatch(/\d+ private channels?|Private channel \(/);
    expect(within(reach).getByText("Also documents you can't read")).toBeInTheDocument();
    expect(summary).toHaveTextContent("is also in private channels you aren't in");
    expect(summary).toHaveTextContent("They aren't named, listed or counted here.");
    expect(document.body).not.toHaveTextContent(/rec_|Private channel \(/);
    expect(summary).toHaveTextContent("also has direct access to documents you can't read");
    expect(screen.getByText("Granted by Lena Brandt", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("“Onboarding cover”")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke grant: Employee handbook" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Change access" })).toBeEnabled();
  });

  it("leaves suspension of a provisioned member to the identity provider", async () => {
    const id = MEMBER_PAT_MANAGED.member.id;
    mockFetch({ [`GET /v1/admin/access/members/${id}`]: [200, MEMBER_PAT_MANAGED] });
    renderInWorkspace(<MemberScreen id={id} />, { me: ME_LENA });
    expect(await screen.findByTestId("managed-note")).toHaveTextContent(
      "Pat Rivera's name, e-mail and whether they're active come from your identity provider.",
    );
    expect(screen.getByText("Identity provider")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
    expect(screen.getByRole("button", { name: "Change access" })).toBeEnabled();
  });

  it("offers no change to your own access", async () => {
    mockFetch({ [`GET /v1/admin/access/members/${ID.lena}`]: [200, MEMBER_LENA_SELF] });
    renderInWorkspace(<MemberScreen id={ID.lena} />, { me: ME_LENA });
    expect(await screen.findByText("This is you. Another administrator must change your access or administrator capability.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change access" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Suspend" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Grant a document" })).toBeNull();
  });

  it("shows non-administrators the same page and never asks the API", async () => {
    const calls = mockFetch({});
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_MAYA });
    expect(await screen.findByRole("heading", { name: "You need administrator access to view this page" })).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("treats a missing member and a malformed link alike", async () => {
    mockFetch({ [`GET ${MAYA}`]: [404, NOT_FOUND] });
    const { unmount } = renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "This member isn't available" })).toBeInTheDocument();
    unmount();
    renderInWorkspace(<MemberScreen id="not-a-uuid" />, { me: ME_LENA });
    expect(await screen.findByRole("heading", { name: "This member isn't available" })).toBeInTheDocument();
  });

  it("previews a change, requires a reason, and applies it against the reviewed version", async () => {
    const calls = mockFetch({
      [`GET ${MAYA}`]: [200, MEMBER_MAYA],
      "GET /v1/admin/access/directory": [200, DIRECTORY],
      [`POST ${MAYA}/preview`]: [200, PREVIEW_ADD_KESTREL],
      [`PATCH ${MAYA}`]: [200, MEMBER_MAYA],
    });
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Change access" }));
    const dialog = await screen.findByRole("dialog", { name: "Change Maya Okafor's access" });
    const review = within(dialog).getByRole("button", { name: "Review change" });
    expect(review).toBeDisabled(); // nothing changed yet
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Kestrel Bank" }));
    expect(within(dialog).getByText("1 rule changed")).toBeInTheDocument();
    await userEvent.click(review);
    expect(await within(dialog).findByText("Maya Okafor will gain access to Kestrel Bank content.")).toBeInTheDocument();
    // Now → After, from the preview: the rule, its direction in words, what is added.
    const compare = within(dialog).getByRole("table", { name: "Now and after this change, rule by rule" });
    const accounts = within(compare).getByRole("row", { name: /Assigned accounts/ });
    expect(accounts).toHaveTextContent("Gains");
    expect(within(accounts).getAllByRole("cell").at(-1)).toHaveTextContent("Kestrel Bank (added)");
    expect(within(dialog).getByRole("heading", { name: /What will change/ })).toHaveFocus();
    expect(within(dialog).getByText(/Counted only among documents you can read/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
    expect(await within(dialog).findByText("Give a reason for this change.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Renewal cover");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.headers.get("X-Lattice-Request")).toBe("1");
    // The result stays on the page (a toast would only echo it).
    expect(screen.getByTestId("applied-change")).toHaveTextContent("Maya Okafor's access changed");
    expect(screen.getByTestId("applied-change")).toHaveTextContent("Assigned Kestrel Bank");
  });

  it("says when someone else changed the member first", async () => {
    mockFetch({
      [`GET ${MAYA}`]: [200, MEMBER_MAYA],
      [`POST ${MAYA}/preview`]: [200, { effects: [{ kind: "suspended" }], documents_gained: 0, documents_lost: 2 }],
      [`PATCH ${MAYA}`]: [409, { error: { code: "conflict", message: "This changed since you opened it.", request_id: "r" } }],
    });
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Suspend" }));
    const dialog = await screen.findByRole("dialog", { name: "Suspend Maya Okafor" });
    expect(await within(dialog).findByText("Maya Okafor will immediately lose access to everything in Lattice.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Left the team");
    await userEvent.click(within(dialog).getByRole("button", { name: "Suspend" }));
    expect(await within(dialog).findByText(/Someone changed Maya Okafor's access after you opened this/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Suspend" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Close and review the latest" })).toBeEnabled();
  });

  it("explains a suspended member, offers reactivation, and keeps the rules", async () => {
    const suspended = {
      ...MEMBER_MAYA,
      member: { ...MEMBER_MAYA.member, status: "suspended" as const },
      lifecycle: { ...MEMBER_MAYA.lifecycle, suspend: null, reactivate: { available: true, blocked_by: null } },
    };
    mockFetch({
      [`GET ${MAYA}`]: [200, suspended],
      [`POST ${MAYA}/preview`]: [200, { effects: [{ kind: "reactivated" }], documents_gained: 2, documents_lost: 0 }],
    });
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    expect(await screen.findByTestId("access-summary")).toHaveTextContent("Maya Okafor is suspended and can't read anything in Lattice.");
    expect(screen.getByText(/none of these rules are in effect until they're reactivated/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Reactivate Maya Okafor" });
    const row = await within(dialog).findByRole("row", { name: /Status/ });
    expect(row).toHaveTextContent("Gains");
    expect(row).toHaveTextContent("Suspended");
    expect(row).toHaveTextContent("Active");
  });

  it("grants a document and keeps the result on the page", async () => {
    mockFetch({
      [`GET ${MAYA}`]: [200, MEMBER_MAYA],
      "GET /v1/admin/access/documents": [200, DOCUMENTS],
      [`GET ${MAYA}/documents/${ID.doc}`]: [200, EXPLANATION_DENIED],
      [`POST ${MAYA}/grants`]: [201, MEMBER_MAYA.grants[0]],
    });
    renderInWorkspace(<MemberScreen id={ID.maya} />, { me: ME_LENA });
    await userEvent.click(await screen.findByRole("button", { name: "Grant a document" }));
    const dialog = await screen.findByRole("dialog", { name: "Grant Maya Okafor a document" });
    await userEvent.type(within(dialog).getByRole("searchbox", { name: "Document to grant" }), "handbook");
    await userEvent.click(await within(dialog).findByRole("radio", { name: /Employee handbook/ }));
    expect(await within(dialog).findByText("Maya Okafor can't read “Employee handbook”")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Grant" }));
    expect(await within(dialog).findByText("Give a reason for this grant.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Onboarding cover");
    await userEvent.click(within(dialog).getByRole("button", { name: "Grant" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByTestId("applied-change")).toHaveTextContent("Granted “Employee handbook” to Maya Okafor");
  });
});
