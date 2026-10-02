"use client";

/**
 * D2 primitives on the specimen: every shared primitive in its states, interactive where
 * it is interactive. Example content is the synthetic company Halden.
 */
import { Copy, GitCompare, History, MoreHorizontal, Pencil, Plus, RotateCw, Trash2, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Cluster, Stack } from "@/components/layout/Layout";
import { BoundaryBand, BoundaryEdge, BoundaryInline } from "@/components/primitives/Boundary";
import { Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { DisagreementAxis } from "@/components/primitives/DisagreementAxis";
import { ExhibitButton, ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { Checkbox, ChoiceGroup, DateInput, Field, Radio, SearchInput, Select, TextArea, TextInput } from "@/components/primitives/Field";
import { FilterLine, FilterToken } from "@/components/primitives/FilterLine";
import { EgressGlyph, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { Jump, useJumpShortcut, type JumpItem } from "@/components/primitives/Jump";
import { Kbd } from "@/components/primitives/Kbd";
import { cellKey, Lattice, LatticeLegend, latticeAddsInformation, type LatticeCell } from "@/components/primitives/Lattice";
import { LevelMeter } from "@/components/primitives/LevelMeter";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/primitives/Menu";
import { Modal } from "@/components/primitives/Modal";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { Popover } from "@/components/primitives/Popover";
import { ProvenanceTrail } from "@/components/primitives/ProvenanceTrail";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { Tag } from "@/components/primitives/Tag";
import { useToast } from "@/components/primitives/Toast";
import { Tooltip } from "@/components/primitives/Tooltip";
import { Drawer } from "@/components/primitives/Drawer";

import { Section } from "./Section";
import styles from "./Primitives.module.css";

export function PrimitiveSections() {
  return (
    <>
      <Section n="09" title="Buttons" note="Primary (one per region), secondary, quiet, danger. Loading keeps the label; pressed is a fill plus an ink edge.">
        <Buttons />
      </Section>
      <Section n="10" title="Fields" note="One anatomy: label, control, help, error (icon plus text). Field owns the wiring; controls own the look.">
        <Fields />
      </Section>
      <Section n="11" title="Tags, levels, status" note="Tags are categories (monochrome). Status marks are states: always a word and an icon.">
        <Marks />
      </Section>
      <Section n="12" title="Exhibit markers and trails" note="One squared numeral everywhere a claim meets its source; a trail is the chain of custody.">
        <Exhibits />
      </Section>
      <Section n="13" title="Lattices" note="Rows × columns of relationships that hold. Shown only when they add information. Tab in, arrow between cells.">
        <Lattices />
      </Section>
      <Section n="14" title="Boundaries and notices" note="Hatching means not crossed, and always says so in words. A tinted callout only for one real state.">
        <Boundaries />
      </Section>
      <Section n="15" title="Disagreement axis" note="Oldest to newest with equal weight. The interval is a fact; the conclusion is the only verdict.">
        <Axes />
      </Section>
      <Section n="16" title="State families" note="Quiet, boundary, fault, wait. The family is written out, never only coloured.">
        <States />
      </Section>
      <Section n="17" title="Filters, menus, overlays" note="Filters read as a sentence. Menus perform actions. Dialogs trap focus and return it.">
        <Overlays />
      </Section>
      <Section n="18" title="Data table" note="One table for every record list: ink-ruled header, hairline rows, stacked blocks below 1024 px, inline expansion.">
        <Tables />
      </Section>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      <div className={styles.rowBody}>{children}</div>
    </div>
  );
}

function Buttons() {
  const [pressed, setPressed] = useState(false);
  return (
    <Stack space="4">
      {(["primary", "secondary", "quiet", "danger"] as const).map((v) => (
        <Row key={v} label={v}>
          <Cluster space="2">
            <Button variant={v} icon={v === "danger" ? <Icon as={Trash2} /> : <Icon as={Plus} />}>
              {v === "danger" ? "Revoke" : v === "primary" ? "Apply change" : "Grant a document"}
            </Button>
            <Button variant={v} size="sm">
              Small
            </Button>
            <Button variant={v} loading>
              Syncing
            </Button>
            <Button variant={v} disabled>
              Disabled
            </Button>
            <Button variant={v} icon={<Icon as={X} />} aria-label="Close" />
          </Cluster>
        </Row>
      ))}
      <Row label="toggle">
        <Button aria-pressed={pressed} onClick={() => setPressed((p) => !p)}>
          {pressed ? "Showing failures" : "Show failures"}
        </Button>
      </Row>
      <Row label="large">
        <Button variant="primary" size="lg">
          Sign in
        </Button>
      </Row>
    </Stack>
  );
}

function Fields() {
  const [search, setSearch] = useState("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const reasonError = touched && !reason.trim() ? "Give a reason; it's recorded in the audit log." : null;
  return (
    <div className={styles.fieldGrid}>
      <Field label="Find a member" hideLabel>
        {(c) => <SearchInput {...c} value={search} onValueChange={setSearch} placeholder="Find a member by name or e-mail" />}
      </Field>
      <Field label="Display name" help="As shown on the member page.">
        {(c) => <TextInput {...c} defaultValue="Maya Okafor" />}
      </Field>
      <Field label="Reason" required help="Recorded in the audit log with your name. Don't paste document content." error={reasonError}>
        {(c) => <TextArea {...c} value={reason} onChange={(e) => setReason(e.target.value)} onBlur={() => setTouched(true)} placeholder="Why is this change needed?" />}
      </Field>
      <Field label="Clearance" help="The highest level this member may read.">
        {(c) => (
          <Select {...c} defaultValue="internal">
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </Select>
        )}
      </Field>
      <Field label="Evidence dated from" hint="optional">
        {(c) => <DateInput {...c} />}
      </Field>
      <Field label="Organization key" help="Set by the identity provider.">
        {(c) => <TextInput {...c} value="halden" readOnly />}
      </Field>
      <Field label="E-mail">
        {(c) => <TextInput {...c} value="pat.rivera@halden.example" disabled />}
      </Field>
      <Field label="Small search" hideLabel>
        {(c) => <SearchInput {...c} size="sm" value={search} onValueChange={setSearch} placeholder="Filter accounts" />}
      </Field>
      <ChoiceGroup legend="Search only" help="None selected searches every source type you can read.">
        <Checkbox label="Documents" defaultChecked />
        <Checkbox label="Tickets" />
        <Checkbox label="Chat messages" disabled />
      </ChoiceGroup>
      <ChoiceGroup legend="Treat this question as" error="Choose how to treat this question.">
        <Radio name="specimen-level" label="Organization default" help="The level your organization sets for questions." />
        <Radio name="specimen-level" label="Confidential" help="Not sent to AI providers approved only for Internal data." />
        <Radio name="specimen-level" label="Restricted" help="Sent only to providers approved for Restricted data." />
      </ChoiceGroup>
    </div>
  );
}

function Marks() {
  return (
    <Stack space="4">
      <Row label="tags">
        <Cluster space="2">
          <Tag icon={<Icon as={SOURCE_GLYPHS.doc} size={12} />}>Document</Tag>
          <Tag icon={<Icon as={SOURCE_GLYPHS.ticket} size={12} />}>Ticket</Tag>
          <Tag icon={<Icon as={SOURCE_GLYPHS.chat} size={12} />}>Chat</Tag>
          <Tag>Identity provider</Tag>
          <Tag tone="quiet">Suspended</Tag>
          <Tag tone="inverse">Restricted</Tag>
          <span className={styles.narrow}>
            <Tag>Northpeak Logistics International Holdings</Tag>
          </span>
        </Cluster>
      </Row>
      <Row label="level meter">
        <Cluster space="6">
          <LevelMeter level="internal" />
          <LevelMeter level="confidential" />
          <LevelMeter level="restricted" />
        </Cluster>
      </Row>
      <Row label="status">
        <Cluster space="5">
          <StatusMark tone="healthy">Synced</StatusMark>
          <StatusMark tone="time">Partial</StatusMark>
          <StatusMark tone="fault">Failed</StatusMark>
          <StatusMark tone="disagreement">Potential conflict</StatusMark>
          <StatusMark tone="neutral">Never synced</StatusMark>
        </Cluster>
      </Row>
      <Row label="keyword">
        <Cluster space="5">
          <StatusMark tone="neutral" keyword icon={null}>
            Answered
          </StatusMark>
          <StatusMark tone="time" keyword>
            Partial
          </StatusMark>
          <StatusMark tone="fault" keyword>
            Failed
          </StatusMark>
        </Cluster>
      </Row>
      <Row label="keys">
        <Cluster space="2">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
          <Kbd>/</Kbd>
          <Kbd>[</Kbd>
          <Kbd>]</Kbd>
          <Kbd>Esc</Kbd>
        </Cluster>
      </Row>
    </Stack>
  );
}

function Exhibits() {
  const [selected, setSelected] = useState<number | null>(1);
  return (
    <Stack space="5">
      <Row label="static">
        <Cluster space="3">
          <ExhibitMarker n={1} />
          <ExhibitMarker n={2} linked />
          <ExhibitMarker n={3} state="selected" />
          <ExhibitMarker n={null} />
          <ExhibitMarker n={12} size="lg" />
          <ExhibitMarker n={4} size="lg" state="skipped" />
          <ExhibitMarker n={5} size="lg" state="failed" />
        </Cluster>
      </Row>
      <Row label="interactive">
        <p className="t-prose">
          At the June kickoff, Halden told Acme it was targeting SCIM provisioning in Q3.{" "}
          <span className={styles.chips}>
            {[2, 3].map((n) => (
              <ExhibitButton key={n} n={n} selected={selected === n} aria-label={`Citation ${n}: example exhibit`} onClick={() => setSelected(selected === n ? null : n)} />
            ))}
          </span>
        </p>
      </Row>
      <Row label="trail">
        <Stack space="3">
          <ProvenanceTrail
            segments={[
              { key: "source", kind: "Source", label: "Halden Drive" },
              { key: "doc", kind: "Document", label: "DR-112 · Identity roadmap decision" },
              { key: "version", kind: "Version", label: "v1" },
              { key: "range", kind: "Range", label: "chars 622–888" },
            ]}
            verified="quote verified"
          />
          <ProvenanceTrail
            segments={[
              { key: "source", kind: "Source", label: "Halden Support (Zendesk)" },
              { key: "doc", kind: "Document", label: "HAL-4821 · SCIM provisioning ETA for the Acme Corp production tenant and its sandbox, raised during the August renewal call" },
              null,
              { key: "range", kind: "Range", label: "chars 1,204–1,377" },
            ]}
          />
        </Stack>
      </Row>
    </Stack>
  );
}

const CLAIMS = [
  { key: "L", label: "Lead", name: "Lead claim", text: "A decision record dated 18 Sep moves SCIM general availability to December, with a November beta for Acme's pilot group." },
  { key: "2", label: "2", name: "Claim 2", text: "At the June kickoff, Halden told Acme it was targeting SCIM provisioning in Q3." },
  { key: "3", label: "3", name: "Claim 3", text: "On 14 Aug, Support told Acme that SCIM was expected in October." },
  { key: "4", label: "4", name: "Claim 4", text: "The beta is limited to Acme's pilot group of 40 users." },
];
const EXHIBITS = [
  { key: "1", label: "1", name: "Exhibit 1, DR-112", conflict: true },
  { key: "2", label: "2", name: "Exhibit 2, Acme kickoff notes" },
  { key: "3", label: "3", name: "Exhibit 3, HAL-4821", conflict: true },
];
const CITES: Record<string, string[]> = { L: ["1"], "2": ["2"], "3": ["3"], "4": ["1", "2"] };

function Lattices() {
  const cells = useMemo(() => new Map<string, LatticeCell>(Object.entries(CITES).flatMap(([c, es]) => es.map((e) => [cellKey(c, e), "filled" as const]))), []);
  const diagonal = useMemo(() => new Map<string, LatticeCell>([["L|1", "filled"], ["2|2", "filled"], ["3|3", "filled"]]), []);
  const [hover, setHover] = useState<{ row?: string | null; col?: string | null }>({});
  const [open, setOpen] = useState<{ row: string; col: string } | null>(null);
  const litClaims = new Set(hover.row ? [hover.row] : hover.col ? Object.keys(CITES).filter((k) => CITES[k]!.includes(hover.col!)) : []);
  const litExhibits = new Set(hover.col ? [hover.col] : hover.row ? CITES[hover.row] ?? [] : []);

  const egressRows = [
    { key: "internal", label: "Internal", name: "Internal data" },
    { key: "confidential", label: "Confidential", name: "Confidential data" },
    { key: "restricted", label: "Restricted", name: "Restricted data" },
  ];
  const egressCols = [
    { key: "ollama", label: "ollama", name: "ollama (generation, local)", detail: "local" },
    { key: "gemini", label: "gemini", name: "gemini (generation, external)", detail: "external" },
    { key: "lattice", label: "Lattice", name: "Stays in Lattice", detail: "readers" },
  ];
  const egress = new Map<string, LatticeCell>([
    ["internal|ollama", "filled"],
    ["internal|gemini", "filled"],
    ["internal|lattice", "filled"],
    ["confidential|ollama", "filled"],
    ["confidential|gemini", "hatched"],
    ["confidential|lattice", "filled"],
    ["restricted|ollama", "hatched"],
    ["restricted|gemini", "hatched"],
    ["restricted|lattice", "filled"],
  ]);

  return (
    <Stack space="6">
      <div className={styles.latticeDemo}>
        <Lattice
          caption="Which claims cite which exhibits"
          corner="Claim"
          rows={CLAIMS}
          cols={EXHIBITS}
          cells={cells}
          cellLabel={(r, c, s) => (s ? `${r.name} cites ${c.name}` : `${r.name} doesn't cite ${c.name}`)}
          onActivate={(row, col) => setOpen({ row, col })}
          onHighlight={(cell) => setHover(cell ?? {})}
          highlight={hover}
          selected={open ?? undefined}
          legend={
            <LatticeLegend
              items={[
                { state: "filled", label: "cites" },
                { state: "conflict", label: "underlined: in a potential conflict" },
              ]}
            />
          }
        />
        <ol className={styles.claims}>
          {CLAIMS.map((c) => (
            <li key={c.key} className={litClaims.has(c.key) ? styles.claimLit : undefined}>
              <span className={styles.claimMarkers}>
                {CITES[c.key]!.map((e) => (
                  <ExhibitMarker key={e} n={Number(e)} linked={litExhibits.has(e)} state={open?.col === e ? "selected" : "rest"} />
                ))}
              </span>
              <span className="t-prose">{c.text}</span>
            </li>
          ))}
        </ol>
      </div>
      <p className={styles.caption} data-testid="lattice-gate">
        Shown for this answer: {String(latticeAddsInformation(cells))} (exhibit 1 backs two claims; claim 4 rests on two exhibits). A one-to-one answer
        would not show it: {String(latticeAddsInformation(diagonal))}.
        {open && ` Opened exhibit ${open.col} for ${CLAIMS.find((c) => c.key === open.row)?.name}.`}
      </p>
      <Lattice
        caption="Which data levels each provider may receive"
        corner="Data level"
        headers="interface"
        rows={egressRows}
        cols={egressCols}
        cells={egress}
        cellLabel={(r, c, s) => (s === "filled" ? `${r.name}: may be sent to ${c.name}` : `${r.name}: kept from ${c.name}`)}
        legend={
          <LatticeLegend
            items={[
              { state: "filled", label: "may be sent" },
              { state: "hatched", label: "kept from this provider; shown to readers as evidence only" },
            ]}
          />
        }
      />
    </Stack>
  );
}

function Boundaries() {
  return (
    <Stack space="4">
      <BoundaryBand>
        Read-only here. Providers and the AI data policy change only through the audited <code>lattice provider</code> and <code>lattice org egress</code> commands.
      </BoundaryBand>
      <BoundaryEdge>
        <p className={styles.edgeTitle}>Acme — executive escalation notes</p>
        <p className={styles.caption}>You can read this. Not sent to the AI model · AI data policy</p>
      </BoundaryEdge>
      <p>
        Question: <BoundaryInline>Content not visible to administrators</BoundaryInline>
      </p>
      <NoticeLine icon={<Icon as={EgressGlyph} />} tone="boundary">
        1 source you can read wasn&apos;t sent to the AI model because of Halden&apos;s AI data policy, including the most recent evidence.
      </NoticeLine>
      <NoticeLine icon={<Icon as={History} />} tone="time">
        The most recent evidence available to you is from 12 Mar 2026.
      </NoticeLine>
      <NoticeLine icon={<Icon as={Pencil} />} tone="neutral">
        Some statements couldn&apos;t be verified against sources and were removed.
      </NoticeLine>
      <NoticeLine icon={<Icon as={GitCompare} />} tone="disagreement">
        Refund policy v1 was replaced by Refund policy v2 on 3 Mar 2026 (per the source).
      </NoticeLine>
      {(["neutral", "plain", "time", "fault", "disagreement", "boundary"] as const).map((tone) => (
        <Callout key={tone} tone={tone} label={tone === "fault" ? "Model unavailable" : tone === "time" ? "Out of date" : tone === "boundary" ? "AI data policy" : tone === "disagreement" ? "Potential conflict" : "Answer withheld"}>
          <p>{tone === "fault" ? "Lattice found evidence but couldn't draft an answer. You can review the sources below or try again." : "Lattice couldn't verify an answer against the sources it found, so none is shown."}</p>
        </Callout>
      ))}
    </Stack>
  );
}

function Axes() {
  const marker = (n: number) => <ExhibitButton n={n} aria-label={`Citation ${n}: example`} />;
  return (
    <Stack space="8">
      <DisagreementAxis
        topic="SCIM general availability date"
        id="axis-two"
        positions={[
          { key: "a", at: "2026-08-14T00:00:00Z", dateLabel: "14 Aug 2026", source: "Ticket · Working document", title: "SCIM provisioning ETA", children: <>Support told Acme SCIM was expected in October. {marker(3)}</> },
          { key: "b", at: "2026-09-18T00:00:00Z", dateLabel: "18 Sep 2026", source: "Decision record · System of record", title: "DR-112 · Identity roadmap decision", children: <>SCIM general availability moves to December. {marker(1)}</> },
        ]}
        conclusion="These sources may disagree. The most recent is the decision record DR-112 (18 Sep 2026). Lattice can't confirm which is current."
      />
      <DisagreementAxis
        topic="Pilot group size"
        id="axis-three"
        positions={[
          { key: "a", at: "2026-06-12T00:00:00Z", dateLabel: "12 Jun 2026", source: "Call notes · Working document", children: <>Acme will pilot with 40 users. {marker(2)}</> },
          { key: "b", at: "2026-06-12T00:00:00Z", dateLabel: "12 Jun 2026", source: "Chat · Working document", children: <>The pilot is 25 seats. {marker(4)}</> },
          { key: "c", at: null, dateLabel: "Undated", source: "Document · Reference", children: <>Pilots cover up to 50 users. {marker(5)}</> },
        ]}
        conclusion="These sources may disagree. Lattice can't confirm which is current."
      />
      <DisagreementAxis
        kind="superseded"
        topic="Refund policy"
        id="axis-superseded"
        positions={[
          { key: "a", at: "2025-11-02T00:00:00Z", dateLabel: "2 Nov 2025", source: "Policy · System of record", title: "Refund policy v1", children: <>Refunds within 14 days. {marker(6)}</> },
          { key: "b", at: "2026-03-03T00:00:00Z", dateLabel: "3 Mar 2026", source: "Policy · System of record", title: "Refund policy v2", children: <>Refunds within 30 days. {marker(7)}</> },
        ]}
        conclusion="Refund policy v1 was replaced by Refund policy v2 on 3 Mar 2026 (per the source)."
        actions={
          <>
            <Button size="sm">Inspect earlier</Button>
            <Button size="sm">Inspect replacement</Button>
          </>
        }
      />
    </Stack>
  );
}

function States() {
  return (
    <div className={styles.stateGrid}>
      <StateBlock family="quiet" heading="Nothing you can access answers this" headingLevel="h3">
        <p>Searched every source type you can read, all dates.</p>
      </StateBlock>
      <StateBlock family="boundary" heading="This evidence isn't available" headingLevel="h3">
        <p>It doesn&apos;t exist, or you don&apos;t have access to it. Lattice doesn&apos;t say which.</p>
      </StateBlock>
      <StateBlock
        family="fault"
        heading="Couldn't reach Lattice"
        headingLevel="h3"
        reference="req_8c21f0"
        actions={
          <Button icon={<Icon as={RotateCw} />} size="sm">
            Try again
          </Button>
        }
      >
        <p>Check your connection and try again. Your question is still in the box.</p>
      </StateBlock>
      <StateBlock family="wait" heading="Too many questions in a row" headingLevel="h3">
        <p>Try again in 30 seconds. Your question is still in the box above.</p>
      </StateBlock>
    </div>
  );
}

const SOURCE_TYPES = [
  ["doc", "documents"],
  ["ticket", "tickets"],
  ["chat", "chat"],
] as const;

function Overlays() {
  const notify = useToast();
  const [types, setTypes] = useState<string[]>([]);
  const [level, setLevel] = useState<string | null>(null);
  const [dialog, setDialog] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [jump, setJump] = useState(false);
  const [asked, setAsked] = useState<string | null>(null);
  const toggleJump = useCallback(() => setJump((j) => !j), []);
  useJumpShortcut(toggleJump);
  const items: JumpItem[] = [
    { id: "ask", group: "Go to", label: "Ask", href: "/specimen#specimen-17" },
    { id: "activity", group: "Go to", label: "Activity", href: "/specimen#specimen-17" },
    { id: "sources", group: "Go to", label: "Sources", href: "/specimen#specimen-17" },
    { id: "q1", group: "Recent questions (example)", label: "When will SCIM ship for Acme, and what did we tell them?", detail: "Answered", href: "/specimen#specimen-17" },
    { id: "q2", group: "Recent questions (example)", label: "What did Kestrel agree to in the renewal?", detail: "Partial", href: "/specimen#specimen-17" },
    { id: "m1", group: "Members (example)", label: "Maya Okafor", detail: "Account Manager", keywords: "maya.okafor@halden.example", href: "/specimen#specimen-17" },
  ];
  const sources = types.length ? types.map((t) => SOURCE_TYPES.find(([k]) => k === t)![1]).join(", ") : "all my sources";

  return (
    <Stack space="6">
      <Row label="filter line">
        <FilterLine lead="Search" label="Search scope">
          <FilterToken name="Sources" value={sources} set={types.length > 0} onClear={() => setTypes([])}>
            <ChoiceGroup legend="Search only" help="None selected searches every source type you can read.">
              {SOURCE_TYPES.map(([key, label]) => (
                <Checkbox key={key} label={label} checked={types.includes(key)} onChange={(e) => setTypes((t) => (e.target.checked ? [...t, key] : t.filter((x) => x !== key)))} />
              ))}
            </ChoiceGroup>
          </FilterToken>
          <FilterToken name="Account" value="any account">
            <p className={styles.caption}>Only the accounts assigned to you are listed. (Example: no accounts loaded on the specimen.)</p>
          </FilterToken>
          <FilterToken name="Dates" value="any date" invalid>
            <p className={styles.caption}>The start date must be on or before the end date.</p>
          </FilterToken>
          <FilterToken name="Sensitivity" prefix="as" value={level ?? "Internal"} set={level !== null} onClear={() => setLevel(null)}>
            <ChoiceGroup legend="Treat this question as">
              {["Confidential", "Restricted"].map((l) => (
                <Radio key={l} name="specimen-filter-level" label={l} checked={level === l} onChange={() => setLevel(l)} />
              ))}
            </ChoiceGroup>
          </FilterToken>
        </FilterLine>
      </Row>
      <Row label="menu">
        <Menu label="Row actions" trigger={<Button variant="quiet" size="sm" icon={<Icon as={MoreHorizontal} />} aria-label="Actions for Employee handbook" />}>
          <MenuLabel>Employee handbook</MenuLabel>
          <MenuItem icon={<Icon as={Copy} />} onSelect={() => notify({ title: "Link copied" })} hint="⌘C">
            Copy link
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<Icon as={Trash2} />} danger onSelect={() => notify({ title: "Revoke would open a confirmation" })}>
            Revoke grant
          </MenuItem>
        </Menu>
      </Row>
      <Row label="popover, tooltip">
        <Cluster space="3">
          <Popover label="Why this source" trigger={<Button size="sm">Why this source?</Button>}>
            <p className={styles.popoverText}>Retrieved as a loose match only, so it was not sent to the AI model.</p>
          </Popover>
          <Tooltip content="Opens the trace for this answer">
            <Button size="sm" variant="quiet">
              Trace
            </Button>
          </Tooltip>
        </Cluster>
      </Row>
      <Row label="dialog, sheet">
        <Cluster space="3">
          <Button onClick={() => setDialog(true)}>Open dialog</Button>
          <Button onClick={() => setSheet(true)}>Open sheet</Button>
          <Modal
            open={dialog}
            onOpenChange={setDialog}
            title="Change Maya Okafor's access"
            description="Maya Okafor will gain access to Kestrel Bank content."
            footer={
              <>
                <Button onClick={() => setDialog(false)}>Back</Button>
                <Button variant="primary" onClick={() => setDialog(false)}>
                  Apply change
                </Button>
              </>
            }
          >
            <Field label="Reason" required help="Recorded in the audit log with your name.">
              {(c) => <TextArea {...c} />}
            </Field>
          </Modal>
          <Drawer open={sheet} onOpenChange={setSheet} side="bottom" title="Exhibit 1">
            <div className={styles.sheetBody}>
              <p className="t-subtitle">DR-112 · Identity roadmap decision</p>
              <Button onClick={() => setSheet(false)}>Close</Button>
            </div>
          </Drawer>
        </Cluster>
      </Row>
      <Row label="toast">
        <Button onClick={() => notify({ title: "Change applied", description: "Maya Okafor's access was updated." })}>Show a toast</Button>
      </Row>
      <Row label="jump">
        <Cluster space="3">
          <Button onClick={() => setJump(true)} icon={<Icon as={History} />}>
            Open Jump
          </Button>
          <span className={styles.caption}>
            or press <Kbd>⌘</Kbd> <Kbd>K</Kbd>
          </span>
          {asked && <span className={styles.caption} data-testid="jump-asked">Would ask: “{asked}”</span>}
        </Cluster>
        <Jump
          open={jump}
          onOpenChange={setJump}
          items={items}
          groups={["Go to", "Recent questions (example)", "Members (example)"]}
          statuses={[{ group: "Sources", message: "Loading sources…" }]}
          onAsk={setAsked}
        />
      </Row>
    </Stack>
  );
}

interface SourceRow {
  id: string;
  name: string;
  connector: string;
  documents: number;
  status: "healthy" | "time" | "neutral";
  statusLabel: string;
  synced: string;
  failures: number;
}

const SOURCES: SourceRow[] = [
  { id: "drive", name: "Halden Drive", connector: "drive", documents: 58, status: "healthy", statusLabel: "Synced", synced: "28 Sep 2026", failures: 0 },
  { id: "support", name: "Halden Support (Zendesk)", connector: "support", documents: 214, status: "time", statusLabel: "Partial", synced: "28 Sep 2026", failures: 1 },
  { id: "slack", name: "Halden Slack", connector: "slack", documents: 96, status: "neutral", statusLabel: "Never synced", synced: "—", failures: 0 },
];

function Tables() {
  const [open, setOpen] = useState<Set<string>>(new Set(["support"]));
  const columns: Column<SourceRow>[] = [
    { key: "name", header: "Source", primary: true, cell: (r) => r.name },
    { key: "connector", header: "Connector", mono: true, cell: (r) => r.connector },
    { key: "documents", header: "Documents", align: "end", mono: true, cell: (r) => r.documents.toLocaleString("en-GB") },
    { key: "status", header: "Last sync", cell: (r) => <StatusMark tone={r.status}>{r.statusLabel}</StatusMark> },
    { key: "synced", header: "Date", mono: true, cell: (r) => r.synced },
    {
      key: "actions",
      header: "Actions",
      hideHeader: true,
      align: "end",
      cell: (r) =>
        r.failures > 0 ? (
          <Button
            size="sm"
            variant="quiet"
            aria-expanded={open.has(r.id)}
            onClick={() =>
              setOpen((o) => {
                const next = new Set(o);
                if (next.has(r.id)) next.delete(r.id);
                else next.add(r.id);
                return next;
              })
            }
          >
            {open.has(r.id) ? "Hide failures" : "Show failures"}
          </Button>
        ) : null,
    },
  ];
  return (
    <Stack space="8">
      <DataTable
        caption="Connected sources (example)"
        showCaption
        columns={columns}
        rows={SOURCES}
        rowKey={(r) => r.id}
        expanded={open}
        renderExpansion={() => (
          <p className={styles.caption}>
            map_access · access_unmappable:account · <span className="t-meta">rec_3f9a0c1d2e4b5a67</span>
          </p>
        )}
      />
      <DataTable caption="Dense, loading" showCaption density="dense" columns={columns.slice(0, 4)} rows={[]} rowKey={(r) => r.id} loading />
      <DataTable
        caption="Empty"
        showCaption
        columns={columns.slice(0, 3)}
        rows={[]}
        rowKey={(r) => r.id}
        empty={
          <StateBlock family="quiet" heading="Lattice hasn't indexed any sources you can access yet" headingLevel="h3" compact>
            <p>Once an administrator connects a source holding content you can read, it appears here.</p>
          </StateBlock>
        }
      />
    </Stack>
  );
}
