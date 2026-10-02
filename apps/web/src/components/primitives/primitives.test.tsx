/**
 * D2 shared primitives: semantics, keyboard behaviour and states.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { BoundaryBand, BoundaryInline } from "./Boundary";
import { Button } from "./Button";
import { DataTable, type Column } from "./DataTable";
import { DisagreementAxis } from "./DisagreementAxis";
import { ExhibitButton, ExhibitMarker } from "./ExhibitMarker";
import { Checkbox, ChoiceGroup, Field, SearchInput, TextInput } from "./Field";
import { FilterLine, FilterToken } from "./FilterLine";
import { Jump, type JumpItem } from "./Jump";
import { cellKey, Lattice, latticeAddsInformation, type LatticeCell } from "./Lattice";
import { LevelMeter } from "./LevelMeter";
import { ProvenanceTrail } from "./ProvenanceTrail";
import { StateBlock } from "./StateBlock";
import { StatusMark } from "./StatusMark";
import { ToastProvider, useToast } from "./Toast";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));

describe("Button", () => {
  it("keeps its label while busy, and stops clicks", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Syncing
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Syncing" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("announces a toggle's pressed state", () => {
    render(<Button aria-pressed>Show failures</Button>);
    expect(screen.getByRole("button", { name: "Show failures", pressed: true })).toBeInTheDocument();
  });
});

describe("Field", () => {
  it("ties the label, help and error to the control", () => {
    render(
      <Field label="Reason" help="Recorded in the audit log." error="Give a reason." required>
        {(c) => <TextInput {...c} />}
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Reason (required)" });
    expect(input).toBeRequired();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Give a reason. Recorded in the audit log.");
  });

  it("has no error wiring without an error", () => {
    render(<Field label="Name">{(c) => <TextInput {...c} />}</Field>);
    const input = screen.getByRole("textbox", { name: "Name" });
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("search: a clear button once there is text, and Escape clears", async () => {
    function Harness() {
      const [v, setV] = useState("");
      return <SearchInput aria-label="Find a member" value={v} onValueChange={setV} />;
    }
    render(<Harness />);
    const box = screen.getByRole("searchbox", { name: "Find a member" });
    expect(screen.queryByRole("button", { name: "Clear search" })).toBeNull();
    await userEvent.type(box, "maya");
    await userEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(box).toHaveValue("");
    await userEvent.type(box, "grace{Escape}");
    expect(box).toHaveValue("");
  });

  it("choices: a legend names the group; help describes each choice; the group error is announced", () => {
    render(
      <ChoiceGroup legend="Search only" error="Pick at least one.">
        <Checkbox label="Tickets" help="Support tickets and their notes." />
      </ChoiceGroup>,
    );
    const group = screen.getByRole("group", { name: "Search only" });
    expect(group).toHaveAttribute("aria-invalid", "true");
    expect(group).toHaveAccessibleDescription("Pick at least one.");
    expect(within(group).getByRole("checkbox", { name: "Tickets" })).toHaveAccessibleDescription("Support tickets and their notes.");
  });
});

describe("Marks", () => {
  it("status marks and levels always carry their word", () => {
    render(
      <>
        <StatusMark tone="fault">Failed</StatusMark>
        <LevelMeter level="confidential" />
        <LevelMeter level="restricted" showLabel={false} />
      </>,
    );
    expect(screen.getByText("Failed")).toBeVisible();
    expect(screen.getByText("Confidential")).toBeVisible();
    expect(screen.getByText("Restricted")).toHaveClass("visually-hidden");
  });

  it("exhibit markers: decorative unless labelled; buttons announce selection", () => {
    render(
      <>
        <ExhibitMarker n={1} />
        <ExhibitMarker n={2} label="Citation 2" />
        <ExhibitMarker n={null} />
        <ExhibitMarker n={4} state="failed" label="Stage 4, failed" />
        <ExhibitButton n={3} selected aria-label="Citation 3: HAL-4821, 14 Aug 2026" />
      </>,
    );
    expect(screen.getAllByRole("img").map((i) => i.getAttribute("aria-label"))).toEqual(["Citation 2", "Stage 4, failed"]);
    expect(screen.getByText("–")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("img", { name: "Stage 4, failed" })).not.toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "Citation 3: HAL-4821, 14 Aug 2026", pressed: true })).toBeInTheDocument();
  });

  it("a provenance trail is an ordered list of only the facts given, ending verified", () => {
    render(
      <ProvenanceTrail
        segments={[{ key: "source", kind: "Source", label: "Halden Drive" }, null, { key: "range", kind: "Range", label: "chars 622–888" }]}
        verified="quote verified"
      />,
    );
    const items = within(screen.getByRole("list", { name: "Provenance" })).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual(["Source: Halden Drive", "Range: chars 622–888", "quote verified"]);
  });

  it("boundaries always say what they are", () => {
    render(
      <>
        <BoundaryBand>Read-only here.</BoundaryBand>
        <BoundaryInline>Content not visible to administrators</BoundaryInline>
      </>,
    );
    expect(screen.getByText("Read-only here.")).toBeVisible();
    expect(screen.getByText("Content not visible to administrators")).toBeVisible();
  });
});

const ROWS = [
  { key: "L", label: "Lead", name: "Lead claim" },
  { key: "2", label: "2", name: "Claim 2" },
  { key: "4", label: "4", name: "Claim 4" },
];
const COLS = [
  { key: "1", label: "1", name: "Exhibit 1", conflict: true },
  { key: "2", label: "2", name: "Exhibit 2" },
  { key: "3", label: "3", name: "Exhibit 3" },
];
const cells = (pairs: [string, string][]) => new Map<string, LatticeCell>(pairs.map(([r, c]) => [cellKey(r, c), "filled"]));

describe("Lattice", () => {
  it("is shown only when the relationship isn't one-to-one", () => {
    expect(latticeAddsInformation(cells([["L", "1"], ["2", "2"]]))).toBe(false);
    expect(latticeAddsInformation(cells([["L", "1"], ["4", "1"]]))).toBe(true);
    expect(latticeAddsInformation(cells([["4", "1"], ["4", "2"]]))).toBe(true);
    expect(latticeAddsInformation(new Map([[cellKey("L", "1"), "hatched"], [cellKey("L", "2"), "hatched"]]))).toBe(false);
  });

  function renderLattice(onActivate = vi.fn(), onHighlight = vi.fn()) {
    render(
      <Lattice
        caption="Which claims cite which exhibits"
        rows={ROWS}
        cols={COLS}
        cells={cells([["L", "1"], ["2", "2"], ["4", "1"], ["4", "3"]])}
        cellLabel={(r, c, s) => (s ? `${r.name} cites ${c.name}` : `${r.name} doesn't cite ${c.name}`)}
        onActivate={onActivate}
        onHighlight={onHighlight}
      />,
    );
    return { onActivate, onHighlight };
  }

  it("is a table with named headers and named cells", () => {
    renderLattice();
    const table = screen.getByRole("table", { name: "Which claims cite which exhibits" });
    for (const name of ["Exhibit 1, in a potential conflict", "Exhibit 2", "Exhibit 3"]) expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    for (const name of ["Lead claim", "Claim 2", "Claim 4"]) expect(within(table).getByRole("rowheader", { name })).toBeInTheDocument();
    expect(within(table).getByText("Claim 2 doesn't cite Exhibit 1")).toBeInTheDocument();
    expect(within(table).getAllByRole("button")).toHaveLength(4);
  });

  it("is one Tab stop; arrows move between related cells and focus reports the cell", async () => {
    const { onActivate, onHighlight } = renderLattice();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Lead claim cites Exhibit 1" })).toHaveFocus();
    expect(screen.getAllByRole("button").filter((b) => b.tabIndex === 0)).toHaveLength(1);
    expect(onHighlight).toHaveBeenLastCalledWith({ row: "L", col: "1" });

    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: "Claim 2 cites Exhibit 2" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: "Claim 4 cites Exhibit 1" })).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("button", { name: "Claim 4 cites Exhibit 3" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("button", { name: "Claim 4 cites Exhibit 1" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("button", { name: "Claim 2 cites Exhibit 2" })).toHaveFocus();

    await userEvent.keyboard("{Enter}");
    expect(onActivate).toHaveBeenCalledWith("2", "2", expect.any(HTMLElement));
    await userEvent.tab();
    expect(onHighlight).toHaveBeenLastCalledWith(null);
  });

  it("without onActivate, cells are text, not controls", () => {
    render(
      <Lattice
        caption="Which data levels each provider may receive"
        rows={[{ key: "confidential", label: "Confidential", name: "Confidential data" }]}
        cols={[{ key: "gemini", label: "gemini", name: "gemini" }]}
        cells={new Map([[cellKey("confidential", "gemini"), "hatched"]])}
        cellLabel={(r, c) => `${r.name}: kept from ${c.name}`}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Confidential data: kept from gemini")).toBeInTheDocument();
  });
});

describe("DisagreementAxis", () => {
  it("keeps the order given, states the interval, and names the kind", () => {
    render(
      <DisagreementAxis
        id="c1"
        topic="SCIM GA date"
        positions={[
          { key: "a", at: "2026-08-14T00:00:00Z", dateLabel: "14 Aug 2026", source: "Ticket", children: "October" },
          { key: "b", at: "2026-09-18T00:00:00Z", dateLabel: "18 Sep 2026", source: "Decision record", children: "December" },
          { key: "c", at: "2026-09-18T00:00:00Z", dateLabel: "18 Sep 2026", source: "Chat", children: "December too" },
          { key: "d", at: null, dateLabel: "Undated", source: "Document", children: "Q1" },
        ]}
        conclusion="Lattice can't confirm which is current."
      />,
    );
    const section = screen.getByRole("region", { name: /^Potential conflict\s*:\s*SCIM GA date$/ });
    const items = within(section).getAllByRole("listitem");
    expect(items.map((i) => i.querySelector("time")?.getAttribute("datetime") ?? null)).toEqual(["2026-08-14T00:00:00Z", "2026-09-18T00:00:00Z", "2026-09-18T00:00:00Z", null]);
    expect(items[1]).toHaveTextContent("35 days later");
    expect(items[2]).toHaveTextContent("Same day");
    expect(items[3]).not.toHaveTextContent("later");
    expect(section).toHaveTextContent("Lattice can't confirm which is current.");
  });

  it("superseded is the neutral form", () => {
    render(<DisagreementAxis id="s" kind="superseded" positions={[]} conclusion="Replaced on 3 Mar 2026 (per the source)." />);
    expect(screen.getByRole("region", { name: "Superseded" })).toBeInTheDocument();
  });
});

describe("StateBlock", () => {
  it("writes the family out; faults carry a reference and alert", () => {
    render(
      <StateBlock family="fault" role="alert" heading="Couldn't reach Lattice" reference="req_1">
        <p>Try again.</p>
      </StateBlock>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Error");
    expect(alert).toHaveTextContent("Reference req_1");
    expect(within(alert).getByRole("heading", { name: "Couldn't reach Lattice" })).toBeInTheDocument();
  });

  it("boundary reads 'Not available' unless relabelled", () => {
    render(<StateBlock family="boundary" heading="This evidence isn't available" />);
    expect(screen.getByText("Not available")).toBeInTheDocument();
  });
});

describe("FilterToken", () => {
  it("names its value, offers a clear once set, and opens its choices", async () => {
    const onClear = vi.fn();
    render(
      <FilterLine lead="Search" label="Search scope">
        <FilterToken name="Sources" value="tickets" set onClear={onClear}>
          <p>Choices</p>
        </FilterToken>
        <FilterToken name="Dates" value="any date" invalid>
          <p>Date choices</p>
        </FilterToken>
      </FilterLine>,
    );
    const group = screen.getByRole("group", { name: "Search scope" });
    await userEvent.click(within(group).getByRole("button", { name: "Remove sources filter" }));
    expect(onClear).toHaveBeenCalledTimes(1);
    expect(within(group).getByRole("button", { name: "Dates: any date, needs a fix. Change" })).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: "Sources: tickets. Change" }));
    expect(await screen.findByText("Choices")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("Choices")).toBeNull();
  });
});

const ITEMS: JumpItem[] = [
  { id: "ask", group: "Go to", label: "Ask", href: "/ask" },
  { id: "sources", group: "Go to", label: "Sources", href: "/sources" },
  { id: "q", group: "Recent questions", label: "When will SCIM ship for Acme?", detail: "Answered", href: "/traces/1" },
  { id: "m", group: "Members", label: "Maya Okafor", keywords: "maya.okafor@halden.example", href: "/access/members/1" },
];

describe("Jump", () => {
  function Harness({ onAsk = vi.fn() }: { onAsk?: (q: string) => void }) {
    const [open, setOpen] = useState(true);
    return (
      <>
        <span data-testid="state">{open ? "open" : "closed"}</span>
        <Jump open={open} onOpenChange={setOpen} items={ITEMS} groups={["Go to", "Recent questions", "Members"]} onAsk={onAsk} />
      </>
    );
  }

  it("is a combobox over grouped options; arrows move the active option", async () => {
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "Jump to" });
    expect(input).toHaveFocus();
    const listbox = screen.getByRole("listbox", { name: "Results" });
    for (const name of ["Go to", "Recent questions", "Members"]) expect(within(listbox).getByRole("group", { name })).toBeInTheDocument();
    expect(within(listbox).getAllByRole("option")).toHaveLength(4);
    const first = within(listbox).getAllByRole("option")[0]!;
    expect(input).toHaveAttribute("aria-activedescendant", first.id);
    expect(first).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", within(listbox).getAllByRole("option")[1]!.id);
    await userEvent.keyboard("{ArrowUp}{ArrowUp}");
    expect(input).toHaveAttribute("aria-activedescendant", within(listbox).getAllByRole("option")[3]!.id);
    expect(screen.getByRole("status")).toHaveTextContent("4 results");
  });

  it("filters on labels and hidden keywords; Enter opens the active item", async () => {
    push.mockClear();
    render(<Harness />);
    await userEvent.type(screen.getByRole("combobox"), "okafor@");
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Ask “okafor@”", "Maya Okafor"]);
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(push).toHaveBeenCalledWith("/access/members/1");
    expect(screen.getByTestId("state")).toHaveTextContent("closed");
  });

  it("asks the typed question; says when nothing matches", async () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);
    await userEvent.type(screen.getByRole("combobox"), "zzz");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    await userEvent.keyboard("{Enter}");
    expect(onAsk).toHaveBeenCalledWith("zzz");
  });

  it("Escape closes", async () => {
    render(<Harness />);
    await userEvent.keyboard("{Escape}");
    expect(screen.getByTestId("state")).toHaveTextContent("closed");
  });
});

describe("Toast", () => {
  it("announces a confirmation", async () => {
    function Notifier() {
      const notify = useToast();
      return <Button onClick={() => notify({ title: "Change applied" })}>Apply</Button>;
    }
    render(
      <ToastProvider>
        <Notifier />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("Change applied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss notification" })).toBeInTheDocument();
  });
});

interface Row {
  id: string;
  name: string;
  documents: number;
}
const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Source", primary: true, cell: (r) => r.name },
  { key: "documents", header: "Documents", align: "end", mono: true, cell: (r) => r.documents },
  { key: "actions", header: "Actions", hideHeader: true, cell: () => <Button size="sm">Sync now</Button> },
];

describe("DataTable", () => {
  it("is a table with headers, rows and named cells", () => {
    render(<DataTable caption="Connected sources" columns={COLUMNS} rows={[{ id: "a", name: "Halden Drive", documents: 58 }]} rowKey={(r) => r.id} />);
    const table = screen.getByRole("table", { name: "Connected sources" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Source", "Documents", "Actions"]);
    const [, row] = within(table).getAllByRole("row");
    expect(within(row!).getAllByRole("cell")[0]).toHaveTextContent("Halden Drive");
    // The stacked labels are visual only; the header already names the cell.
    expect(within(row!).getAllByRole("cell")[1]!.querySelector("[aria-hidden='true']")).toHaveTextContent("Documents");
  });

  it("empty, loading and expanded rows", () => {
    const { rerender } = render(<DataTable caption="Sources" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty={<p>Nothing indexed yet.</p>} />);
    expect(screen.getByText("Nothing indexed yet.")).toBeInTheDocument();
    rerender(<DataTable caption="Sources" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} loading empty={<p>Nothing indexed yet.</p>} />);
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("Nothing indexed yet.")).toBeNull();
    rerender(
      <DataTable
        caption="Sources"
        columns={COLUMNS}
        rows={[{ id: "a", name: "Halden Support", documents: 214 }]}
        rowKey={(r) => r.id}
        expanded={new Set(["a"])}
        renderExpansion={() => <p>access_unmappable:account</p>}
      />,
    );
    expect(screen.getByText("access_unmappable:account")).toBeInTheDocument();
  });
});
