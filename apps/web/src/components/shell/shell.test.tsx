import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { jumpChord, useJumpShortcut } from "@/components/primitives/Jump";
import { ACTIVITY_OWN, ME_LENA, ME_MAYA, MEMBERS_PAGE } from "@/test/fixtures";
import { mockFetch, renderInWorkspace } from "@/test/render";

import { currentDestination, DESTINATIONS, initials, phoneSplit, visibleNav, visibleSubpages, type VisibleSection } from "./nav";
import { ShellJump } from "./ShellJump";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/ask",
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("navigation model", () => {
  it("members see the workspace section only; capabilities from /v1/me add administration", () => {
    expect(visibleNav(ME_MAYA).map((s) => [s.section.label, s.destinations.map((d) => d.label)])).toEqual([["Workspace", ["Ask", "Activity", "Sources"]]]);
    expect(visibleNav(ME_LENA).map((s) => [s.section.label, s.destinations.map((d) => d.label)])).toEqual([
      ["Workspace", ["Ask", "Activity", "Sources"]],
      ["Administration", ["Access", "AI providers"]],
    ]);
    expect(visibleSubpages(ME_MAYA)).toEqual([]);
    expect(visibleSubpages(ME_LENA).map((p) => p.label)).toEqual(["Organization runs", "Audit log"]);
  });

  it("deep pages belong to their destination", () => {
    const at = (p: string) => currentDestination(DESTINATIONS, p)?.label;
    expect(at("/ask")).toBe("Ask");
    expect(at("/evidence/x")).toBe("Ask");
    expect(at("/traces/x")).toBe("Activity");
    expect(at("/access/members/x")).toBe("Access");
    expect(at("/access/audit")).toBe("Access");
    expect(at("/settings/providers")).toBe("AI providers");
    expect(at("/sign-in")).toBeUndefined();
  });

  it("phones: tab-priority destinations become tabs (at most four); the rest go to More, by section", () => {
    const lena = phoneSplit(visibleNav(ME_LENA));
    expect(lena.tabs.map((d) => d.label)).toEqual(["Ask", "Activity", "Sources"]);
    expect(lena.more.map((s) => [s.section.label, s.destinations.map((d) => d.label)])).toEqual([["Administration", ["Access", "AI providers"]]]);
    expect(phoneSplit(visibleNav(ME_MAYA)).more).toEqual([]);

    // Growth: a sixth and seventh workspace destination don't widen the bar.
    const extra = (label: string) => ({ ...DESTINATIONS[0]!, href: `/${label}`, label });
    const grown: VisibleSection[] = [{ section: { id: "workspace", label: "Workspace" }, destinations: [...visibleNav(ME_MAYA)[0]!.destinations, extra("Five"), extra("Six")] }];
    const split = phoneSplit(grown);
    expect(split.tabs.map((d) => d.label)).toEqual(["Ask", "Activity", "Sources", "Five"]);
    expect(split.more[0]!.destinations.map((d) => d.label)).toEqual(["Six"]);
  });

  it("initials", () => {
    expect(initials("Maya Okafor")).toBe("MO");
    expect(initials("Lena")).toBe("L");
    expect(initials("  Jean-Luc   de la Tour ")).toBe("JT");
    expect(initials("Łukasz Żak")).toBe("ŁŻ");
  });
});

describe("Jump chord", () => {
  const platform = (value: string) => vi.spyOn(navigator, "platform", "get").mockReturnValue(value);

  it("is ⌘K on Apple platforms and Ctrl K elsewhere", () => {
    platform("MacIntel");
    expect(jumpChord()).toMatchObject({ apple: true, label: "⌘K", aria: "Meta+K" });
    platform("Win32");
    expect(jumpChord()).toMatchObject({ apple: false, label: "Ctrl K", aria: "Control+K" });
  });

  function Harness({ onToggle }: { onToggle: () => void }) {
    useJumpShortcut(onToggle);
    return <textarea aria-label="field" />;
  }

  it("takes only the platform's own chord, and nothing during composition, repeat, or an open modal", () => {
    platform("MacIntel");
    const onToggle = vi.fn();
    render(<Harness onToggle={onToggle} />);
    const field = screen.getByRole("textbox", { name: "field" });
    fireEvent.keyDown(field, { key: "k", ctrlKey: true });
    expect(onToggle).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "k", metaKey: true, isComposing: true });
    fireEvent.keyDown(field, { key: "k", metaKey: true, repeat: true });
    fireEvent.keyDown(field, { key: "k", metaKey: true, shiftKey: true });
    expect(onToggle).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "k", metaKey: true });
    expect(onToggle).toHaveBeenCalledTimes(1);

    const modal = document.createElement("div");
    modal.setAttribute("data-modal", "");
    modal.setAttribute("data-state", "open");
    document.body.append(modal);
    fireEvent.keyDown(field, { key: "k", metaKey: true });
    expect(onToggle).toHaveBeenCalledTimes(1);
    modal.remove();
  });
});

describe("ShellJump", () => {
  const go = vi.fn();

  it("members: their destinations and their own recent questions; no administrator request, whatever is typed", async () => {
    const calls = mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN] });
    renderInWorkspace(<ShellJump open onOpenChange={() => {}} onGo={go} onAsk={() => {}} />, { me: ME_MAYA });
    const recent = await screen.findByRole("group", { name: "Your recent questions" });
    expect(within(recent).getAllByRole("option")[0]).toHaveTextContent("When will SCIM ship for Acme");
    expect(within(screen.getByRole("group", { name: "Go to" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["Ask", "Activity", "Sources"]);

    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(screen.getByRole("combobox", { name: "Jump to" }), { target: { value: "grace" } });
    await act(async () => vi.advanceTimersByTimeAsync(400));
    vi.useRealTimers();
    expect(screen.queryByRole("group", { name: "Members" })).toBeNull();
    expect(calls.some((c) => c.url.startsWith("/v1/admin"))).toBe(false);
  });

  it("administrators: members appear only for a typed search, from the Access endpoint", async () => {
    const calls = mockFetch({ "GET /v1/activity": [200, ACTIVITY_OWN], "GET /v1/admin/access/members": [200, MEMBERS_PAGE] });
    renderInWorkspace(<ShellJump open onOpenChange={() => {}} onGo={go} onAsk={() => {}} />, { me: ME_LENA });
    await screen.findByRole("group", { name: "Your recent questions" });
    expect(calls.some((c) => c.url.startsWith("/v1/admin"))).toBe(false);
    fireEvent.change(screen.getByRole("combobox", { name: "Jump to" }), { target: { value: "grace" } });
    const members = await screen.findByRole("group", { name: "Members" });
    expect(within(members).getByRole("option")).toHaveTextContent("Grace Whitfield");
    expect(calls.filter((c) => c.url === "/v1/admin/access/members")).not.toHaveLength(0);
    fireEvent.click(within(members).getByRole("option"));
    expect(go).toHaveBeenCalledWith(`/access/members/${MEMBERS_PAGE.items[0]!.id}`);
  });

  it("says when recent questions couldn't be loaded, and still lists destinations", async () => {
    mockFetch({ "GET /v1/activity": [500, { error: { code: "internal_error", message: "x", request_id: "r" } }] });
    renderInWorkspace(<ShellJump open onOpenChange={() => {}} onGo={go} onAsk={() => {}} />, { me: ME_MAYA });
    expect(await screen.findByText(/Couldn't load your recent questions/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Ask" })).toBeInTheDocument();
  });
});
