import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ID, ME_LENA, ME_MAYA, MEMBER_LENA_SELF, MEMBER_MAYA, NOT_FOUND, PREVIEW_ADD_KESTREL, DIRECTORY } from "@/test/fixtures";
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
    expect(screen.getByText("Administrator-defined")).toBeInTheDocument();
    expect(summary).toHaveTextContent("is also in private channels you aren't in");
    expect(screen.getByText(/Private channels you aren't in aren't named, listed or counted here/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/rec_|Private channel \(/);
    expect(summary).toHaveTextContent("also has direct access to documents you can't read");
    expect(screen.getByText("Granted by Lena Brandt", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("“Onboarding cover”")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke grant: Employee handbook" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Change access" })).toBeEnabled();
  });

  it("offers no change to your own access", async () => {
    mockFetch({ [`GET /v1/admin/access/members/${ID.lena}`]: [200, MEMBER_LENA_SELF] });
    renderInWorkspace(<MemberScreen id={ID.lena} />, { me: ME_LENA });
    expect(await screen.findByText("This is you. Another administrator must change your access.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change access" })).toBeNull();
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
    await userEvent.click(review);
    expect(await within(dialog).findByText("Maya Okafor will gain access to Kestrel Bank content.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Counted only among documents you can read/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
    expect(await within(dialog).findByText("Give a reason for this change.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Renewal cover");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply change" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.headers.get("X-Lattice-Request")).toBe("1");
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
  });
});
