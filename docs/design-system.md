# Lattice — Design System

Status: Foundation draft (Phase 0). Implementation-oriented; tokens here become
`apps/web/src/styles/tokens.css` in Phase 7. Nothing is built yet.

---

## Part A — Reference analysis

Source: `docs/references/FDE_Portfolio_Handout_Aishwarya_Srinivasan.pdf` (9 pages,
US Letter 612×792 pt, produced from HTML by Chromium). Inspected by rendering every
page and by extracting the embedded fonts and fill/stroke colors from the PDF content
streams — the values below are measured, not guessed.

### A1. What is present

**Measured facts**

| Property | Measurement |
| -------- | ----------- |
| Typefaces | Poppins Regular + Bold (geometric sans) for everything editorial; Liberation Mono Regular + Bold (Courier-metric monospace) for labels, running heads, code, URLs |
| Canvas | `#FFFCF4` warm off-white paper |
| Ink | `#111111` text, `#000000` keylines and code-block fill |
| Secondary text | `#4A4A4A` (deck paragraphs, descriptions) |
| Muted text | `#8F8F8F` (code comments) — **3.15:1 on canvas, fails AA for text** |
| Neutral tint | `#F3EFF2` (table header rows, neutral pills, neutral callouts) |
| Cream tint | `#FAF6EC` (zebra table rows) |
| Hairline | `#D9D5D0` (row dividers) |
| Lime | `#E3F266` accent; tint `#F4F9C9` |
| Pink | `#CD56A0` accent (also `#E58BC4`); tint `#F7DCEC` |
| Lavender | `#8E84F7` accent (also `#B5AEFF`); tint `#E9E7FD` |
| Page margin | ≈ 46 pt (≈ 7.5% of width); two equal columns with ≈ 22 pt gutter |
| Container radius | ≈ 10 pt on callout boxes; labels fully rounded |
| Keylines | ≈ 1 pt ink on every container and pill; 1 pt ink rule under the running head and under table headers |

**Principles observed**

1. **Editorial hierarchy through weight and scale, not color.** Headlines are heavy
   (Bold), tightly tracked, near-black; the deck paragraph beneath is large, regular,
   and grey. Three levels are unmistakable at a glance.
2. **A mono "technical voice" for metadata.** Every label, running head, section
   number, table header, and URL is monospace, uppercase, widely tracked. Prose never
   is. The two voices never mix within a line.
3. **Keylines do the structuring.** Crisp 1 pt ink borders define containers; a single
   ink rule anchors the running header; thin warm-grey rules separate table rows. No
   drop shadows anywhere.
4. **Pastel tints carry meaning by category, not decoration per se.** Lavender,
   pink and lime tinted callouts classify content ("the rule that matters" is pink,
   "FDE signal" is lime). Accents are always paired with ink outlines, which keeps
   pale colors legible on the cream page.
5. **The pill label as a system primitive.** A mono, uppercase, ink-outlined, fully
   rounded label sits at the top-left of callouts and marks every section number.
6. **Strict two-column grid with shared axes.** Section heads, callouts and tables
   snap to the same column edges; the running head spans the full measure.
7. **Running header + footer frame every page** (title left, section marker right,
   page count "03 / 09" in mono).
8. **Numbered sequences** are shown as ink-outlined lime circles; checklists as small
   tinted squares.
9. **Code blocks** are black, rounded, with lime strings and lavender/pink keywords —
   high-contrast "developer" moments embedded in an editorial page.
10. **Disciplined density.** Generous margins, but inside the columns the content is
    dense: 11–12 pt body, tight but comfortable leading, no empty decoration.

### A2. What Lattice adopts

| Principle | Lattice adoption |
| --------- | ---------------- |
| Weight/scale hierarchy | Page titles and the Ask empty-state heading use heavy, tight display type; descriptions use secondary grey |
| Mono technical voice | IDs, dates, source refs, trace stages, table headers and section labels use mono; answers and descriptions never do |
| Keylines over shadows | 1px ink keylines on the composer, inspector edge, dialogs and table headers; warm hairlines for row dividers; shadows only for floating overlays |
| Tints carry meaning | Each accent gets exactly one semantic job (§B3) |
| Pill label | Used for categorical tags (source type, sensitivity, status) — not for everything |
| Shared axes | Composer, answer, evidence list share one column; sidebar header and page header share one horizontal keyline |
| Warm paper canvas | `#FFFCF4` canvas with ink text |
| Numbered sequences | Trace stages and citation numbers use the numbered-marker language |

### A3. What must not be copied into software

| Print idea | Why not | Instead |
| ---------- | ------- | ------- |
| Pastel-filled callout boxes everywhere | In an app this becomes card soup and dilutes state colors | Tinted containers only for real states (potential conflict, stale, error) |
| Poppins for everything | Geometric single-storey "a", wide set and ambiguous `I/l/1` reduce density and ID legibility in UI | Geist for UI (§B4), heavier weights for display to echo the editorial feel |
| Courier-style mono | Low x-height and thin strokes read poorly at 11–12 px on screen | Geist Mono, same family metrics as the sans |
| `#8F8F8F` muted text, lavender/pink as text colors | Fail WCAG AA (3.15, 3.01, 3.77:1) | Darkened text variants (§B3) |
| The pink `//` section-head motif, tri-color progress bar, author pill footer | Brand identity of the reference, and decorative | Mono uppercase section labels with a hairline rule |
| Page frame (running head/footer on every view) | Apps have persistent chrome; repeated footers waste space | One page header with a mono breadcrumb on the right |
| Two equal columns | Ask needs a reading column + an inspector, not symmetry | Asymmetric app grid (§B6) |
| Fixed page size | Screens vary | Responsive rules (§B14) |

### A4. Translation to Lattice components (summary)

- **Running head** → *Page header*: 56 px, title left (h1 at page level or
  breadcrumb), mono context marker right (`ASK · ACME CORP`), 1px ink rule below that
  continues through the sidebar header.
- **Pill label** → *Tag*: 20 px mono uppercase outlined pill for source type,
  sensitivity, sync status.
- **Tinted callout with pill** → *State callout*: potential conflict (pink), stale (amber),
  error (red) — each with a mono label and an icon.
- **Table with mono tinted header and ink rule** → *Data table* for Sources, Access,
  Traces.
- **Numbered lime circles** → *Stage marker* in traces and ingestion steps; the
  *citation chip* borrows the numbered-marker idea at inline scale.
- **Black code block** → *Trace detail / JSON viewer* (admin only).
- **Headline + grey deck** → *Page title + description*; *Ask empty state*.

---

## Part B — System

All values are tokens. Components reference tokens only; raw hex values outside
`tokens.css` fail lint (stylelint `color-no-hex` outside that file).

### B1. Principles for every screen

1. Typography, alignment and rules first; containers only when they mean grouping,
   interaction, or state.
2. One ink-keyline container per region at most. Never nest ink-bordered boxes.
3. Every accent use must answer "what does this color mean here?" using §B3.
4. Mono is for machine-ish facts (IDs, dates, counts, labels); sans is for language.
5. Every state is designed (§B13); no screen ships with an undefined failure path.

### B2. Color tokens

Light theme only in the first release; tokens are semantic so a dark theme can be
added by remapping.

**Backgrounds**

| Token | Value | Use |
| ----- | ----- | --- |
| `bg.canvas` | `#FFFCF4` | App background |
| `bg.surface` | `#FFFFFF` | Inputs, composer field, popovers, menus |
| `bg.sunken` | `#FAF6EC` | Row hover, zebra rows, inline code, inspector context blocks |
| `bg.muted` | `#F3EFF2` | Table header, neutral tags, selected nav row |
| `bg.inverse` | `#111111` | Primary button, trace JSON viewer, tooltips |

**Text** (contrast measured against `bg.canvas` / `bg.muted`)

| Token | Value | Contrast | Use |
| ----- | ----- | -------- | --- |
| `text.primary` | `#111111` | 18.4 / 16.6 | Body, headings |
| `text.secondary` | `#4A4A4A` | 8.6 / 7.8 | Descriptions, excerpts |
| `text.muted` | `#6B665F` | 5.6 / 5.0 | Metadata, placeholders, timestamps (replaces reference `#8F8F8F`) |
| `text.inverse` | `#FFFCF4` | 18.4 on ink | On `bg.inverse` |
| `text.link` | `#4F46C8` | 6.7 | Links, citation text |

**Borders**

| Token | Value | Use |
| ----- | ----- | --- |
| `border.default` | `#D9D5D0` | Row dividers, section rules (decorative; never the only boundary of a control) |
| `border.strong` | `#111111` | Keylines: composer, inputs, buttons (secondary), dialogs, inspector edge, table header rule, tags |
| `border.focus` | `#4F46C8` | Focus ring (6.7:1 on canvas) |

**Accents — one job each**

| Token | Value | Job |
| ----- | ----- | --- |
| `accent.lime` | `#E3F266` | *"This is the thing you're looking at."* Cited-passage highlight in the inspector, selected citation, stage markers. Always on ink-outlined shapes or behind ink text (1.19:1 on canvas — never a standalone boundary) |
| `accent.lime.subtle` | `#F4F9C9` | Success/verified background |
| `accent.lavender` | `#8E84F7` | Graphic use only (icons ≥ 16 px with ink context) |
| `accent.lavender.subtle` | `#E9E7FD` | Citation chip background; info callout |
| `accent.lavender.text` | `#4F46C8` | Citation chip text, links, info text |
| `accent.pink` | `#CD56A0` | Potential-conflict border and icon (3.8:1: graphics OK, not small text) |
| `accent.pink.subtle` | `#F7DCEC` | Potential-conflict callout background |
| `accent.pink.text` | `#9E2F74` | Potential-conflict label/text (5.3:1 on pink.subtle) |

**Status** (text / background / border; text contrast measured on its background)

| Token set | Text | Background | Border | Contrast | Use |
| --------- | ---- | ---------- | ------ | -------- | --- |
| `status.success` | `#2F5E14` | `#F4F9C9` | `#B9C94A` | 7.0 | Sync succeeded, citations verified |
| `status.warning` | `#7A5000` | `#FBEFD3` | `#E3B85C` | 6.2 | Stale evidence, partial sync |
| `status.error` | `#B42318` | `#FCE4E0` | `#E8A197` | 5.4 | Failures |
| `status.info` | `#4F46C8` | `#E9E7FD` | `#B5AEFF` | 5.7 | Neutral notices (AI egress-policy notices, removed-claims notice) |
| `status.conflict` | `#9E2F74` | `#F7DCEC` | `#CD56A0` | 5.3 | Potential conflicts and supersession (distinct from error — disagreement is information, not failure) |

Sensitivity is intentionally **monochrome**: `Internal` shows no badge (default;
avoid noise), `Confidential` = ink-outlined tag with lock icon, `Restricted` = ink-filled
tag with `text.inverse`. Sensitivity is serious, stable information and should not
compete with state colors.

### B3. Accent budget per screen

At most **one** tinted state callout visible above the fold on Ask (the most
important: error > potential conflict > stale), others collapse into the answer header's status
line. Lime appears at most in: the selected citation, the highlighted passage, and
stage markers.

### B4. Typography

**Families**

- `font.sans` — **Geist** (SIL OFL, variable 100–900), fallback `ui-sans-serif,
  system-ui, -apple-system, "Segoe UI", sans-serif`. Chosen for: neutral grotesque
  shapes with a slightly geometric construction that echoes the reference's editorial
  character; strong heavy weights for display; tabular figures; good small-size
  rendering. Self-hosted via the `geist` package / `next/font` (no third-party font
  requests).
- `font.mono` — **Geist Mono**, fallback `ui-monospace, "SF Mono", Menlo, monospace`.
  Same family metrics as the sans, so inline IDs (`HAL-4821`) sit on the same
  baseline and x-height as surrounding prose.
- Verify in Phase 7 type test: `I l 1 | O 0` distinctness at 12 px mono and 14 px
  sans; fallback candidate if it fails: IBM Plex Sans / IBM Plex Mono.
- `font-feature-settings: "tnum"` on all numeric columns, timestamps and counts.

**Scale** (px; rem = px/16)

| Token | Family | Size / line-height | Weight | Tracking | Use |
| ----- | ------ | ------------------ | ------ | -------- | --- |
| `display` | sans | 40 / 44 | 700 | −0.025em | Ask empty-state heading only |
| `h1` | sans | 28 / 34 | 700 | −0.02em | Page titles (Sources, Access…) |
| `h2` | sans | 20 / 28 | 650 | −0.01em | Section titles, inspector document title |
| `h3` | sans | 16 / 24 | 600 | −0.005em | Evidence titles, dialog titles |
| `prose` | sans | 16 / 26 | 400 | 0 | Answer text (max 68ch) |
| `body` | sans | 14 / 20 | 400 | 0 | Default UI text |
| `body-strong` | sans | 14 / 20 | 600 | 0 | Emphasis in UI |
| `body-small` | sans | 13 / 18 | 400 | 0 | Excerpts, secondary rows |
| `caption` | sans | 12 / 16 | 400 | 0 | Helper text under inputs |
| `label` | mono | 11 / 16 | 500 | +0.08em, UPPERCASE | Section labels, tags, table headers |
| `metadata` | mono | 12 / 16 | 400 | 0 | Dates, IDs, counts, source refs |
| `code` | mono | 13 / 20 | 400 | 0 | Trace JSON, config |

Rules: max line length 68ch for prose, 80ch for body paragraphs; headings never
exceed 2 lines in UI (truncate with tooltip in tables/panels, wrap in page headers);
no text below 11 px; uppercase only in `label`.

### B5. Spacing

4 px base. Tokens: `space.0`=0, `space.0_5`=2, `space.1`=4, `space.2`=8,
`space.3`=12, `space.4`=16, `space.5`=20, `space.6`=24, `space.8`=32, `space.10`=40,
`space.12`=48, `space.16`=64.

Rhythm rules:
- Inside components: 4/8/12. Between related blocks: 16/24. Between sections: 32/48.
- Vertical spacing between text blocks is set on the block (`margin-block-start` via
  a `Stack` primitive), never ad-hoc per instance.

### B6. Layout grid

| Region | ≥1440 | 1280–1439 | 1024–1279 | 768–1023 | <768 |
| ------ | ----- | --------- | --------- | -------- | ---- |
| Sidebar | 240 fixed | 240 fixed | 56 rail (expand as overlay 240) | 56 rail | Hidden; top bar 52 with menu → sheet |
| Page gutter | 32 | 32 | 24 | 24 | 16 |
| Ask reading column | max 760, centered in remaining space | max 720 | max 720 | fluid | fluid |
| Inspector | 440 docked | 400 docked (reading column shrinks, min 520) | 400 overlay drawer + scrim | min(400, 90vw) drawer | Bottom sheet, 92vh |
| Admin pages (tables) | 12-col, max 1200, 24 gutter | same | same | 8-col | stacked |

Shared axes (non-negotiable):
- Sidebar header and page header are both 56 px with the 1px ink rule at the same y.
- Composer, answer, and evidence list share left and right edges (the reading
  column).
- Inspector header height = page header height (56), so its bottom rule continues the
  same line.
- Table cell padding-left = 16 = page gutter within table containers → first-column
  text aligns with page title text.

### B7. Control heights

| Token | Height | Used by |
| ----- | ------ | ------- |
| `control.xs` | 20 | Tags, citation chip (18 inline variant) |
| `control.sm` | 28 | Scope chips, toolbar icon buttons, dense table actions |
| `control.md` | 36 | Default buttons, inputs, selects, search fields |
| `control.lg` | 44 | Dev-login persona buttons, mobile primary actions (touch target) |
| Nav row | 32 | Sidebar items |
| Table header | 32 | |
| Table row | 40 (dense 32) | |
| Composer | min 116 = text area 2 lines × 24 + 24 vertical padding (16 top, 8 bottom) + 44 footer row; grows to 8 lines, then scrolls internally | |

Rule: controls placed in the same row share a vertical center axis; controls of
the same role in a row have identical heights (enforced by the primitives, not by
per-instance margins). Touch targets on <768 are ≥ 44 px (sm controls get 44 px hit
areas via padding).

### B8. Borders, radii, shadows, z-index

- Border width: 1 px everywhere. 2 px only for focus rings.
- Radii: `radius.none` 0 (tables, rules), `radius.xs` 4 (citation chip, inline code,
  highlight), `radius.sm` 6 (buttons, inputs, scope chips), `radius.md` 10 (composer,
  callouts, popovers, menus), `radius.lg` 14 (dialogs, drawer/sheet outer corners),
  `radius.full` (tags). Nested radius = outer − padding (never larger than parent).
- Shadows: only `shadow.overlay` = `0 12px 32px rgba(17,17,17,0.12)` for popovers,
  menus, drawers, dialogs — always combined with a 1px `border.strong`. No shadows on
  in-flow content.
- Z-index: base 0, sticky 10, rail-overlay 20, drawer 30, dialog 40, popover 50,
  toast 60.

### B9. Icons

- **Lucide** (ISC license) — one family. Stroke 1.5 px at 16 px; 20 px only in the
  collapsed rail. `currentColor`.
- Icons sit in a square box equal to the text line-height (16 in a 20 px line) and are
  vertically centered to the text's x-height center; verify per primitive, not per
  instance.
- Source-type icons: document `file-text`, ticket `life-buoy`, chat `hash`. Status:
  `check-circle-2`, `alert-triangle`, `x-circle`, `info`, `git-compare` (conflict),
  `clock` (stale), `lock` (confidential/restricted).
- Icons never carry meaning alone: paired with text or an accessible label.

### B10. Motion

| Token | Duration | Easing | Use |
| ----- | -------- | ------ | --- |
| `motion.fast` | 120 ms | `cubic-bezier(0.2, 0, 0, 1)` | Hover, press, chip selection |
| `motion.base` | 180 ms | same | Popovers, tooltips, accordions |
| `motion.panel` | 220 ms | same | Inspector/drawer slide (translateX 16 px + fade) |
| `motion.result` | 180 ms | same | Evidence and answer arrival (fade + 4 px rise) |

`prefers-reduced-motion: reduce` → no translation; opacity transitions ≤ 120 ms or
none. No looping animations except the progress indicator's stage text change (not a
spinner shimmer).

### B11. Application shell and navigation

Evaluated against real workflows (see `implementation-plan.md` for timing):

```
┌ Sidebar (240) ─────────┬ Page header (56): title ···· mono context marker ┐
│ Halden          ▾ org   ├──────────────────────────────────────────────────┤ ← one ink rule
│─────────────────────────│                                                  │
│ Ask             (⌘K)    │                 page content                     │
│ Sources                 │                                                  │
│                         │                                                  │
│ ADMIN                   │                                                  │
│ Access                  │                                                  │
│ Activity                │                                                  │
│                         │                                                  │
│ ─────────────────────── │                                                  │
│ Maya Okafor             │                                                  │
│ Account Manager    ⋯    │                                                  │
└─────────────────────────┴──────────────────────────────────────────────────┘
```

Changes from the suggested IA, with reasons:
- **Search merged into Ask.** Ask already shows ranked evidence; a separate Search
  duplicates it. A retrieval-only "Find" mode inside Ask (no generation, instant) is a
  cheap later addition for ticket-ID lookups.
- **Knowledge merged into Sources.** "What Lattice knows" is best expressed per
  source (health, counts, freshness, permission mode).
- **Accounts, Saved deferred.** Accounts (an account timeline + scoped Ask) is the
  strongest next workflow for AMs, but it isn't needed to prove the slice.
- **Activity** = the user's own recent questions (links to traces) for everyone;
  admins see an operational view of org activity (member, time, status, durations —
  never question, answer, evidence, counts or scope; D-5 is off). **Evaluations** are a CLI + report
  in the first release, not an admin screen. **Ingestion** is part of Sources.
- Admin section renders only for members with the capability (server-driven via
  `/v1/me`), not hidden by CSS.

Sidebar: nav rows 32 px, 12 px horizontal padding, 16 px icon + 8 px gap + body
label. Active row: `bg.muted`, `body-strong`, 2 px ink bar at the left edge inside
the row. Hover: `bg.sunken`. Section label `ADMIN` in `label` style, `text.muted`.
Org name at top (h3-sized, 600) with the header rule; user block at bottom shows name
+ role label (the role label here helps users understand why they see what they see).

### B12. Components

**Buttons** — heights sm/md; radius `sm`; label `body-strong`; icon 16 + gap 8.

| Variant | Rest | Hover | Active | Disabled |
| ------- | ---- | ----- | ------ | -------- |
| Primary | `bg.inverse`, `text.inverse`, ink border | `#2A2A2A` | `#000` | `bg.muted`, `text.muted`, `border.default` |
| Secondary | transparent, ink border, `text.primary` | `bg.sunken` | `bg.muted` | `text.muted`, `border.default` |
| Ghost | transparent, no border | `bg.muted` | `#E9E4E6` | `text.muted` |
| Danger | transparent, `status.error` border/text | `status.error` bg tint | — | as secondary |

One primary button per view region. Loading state keeps width (spinner replaces
icon, label stays), `aria-busy`.

**Focus** (all interactive elements): `outline: 2px solid border.focus;
outline-offset: 2px`, visible for keyboard (`:focus-visible`) only.

**Inputs / selects** — md height, `bg.surface`, 1 px ink border, radius `sm`, 12 px
horizontal padding, placeholder `text.muted`. Error: border and helper text
`status.error.text`, icon + message (not color alone). Label above in `body-strong`,
helper below in `caption`. Disabled: `bg.sunken`, `border.default`.

**Composer** (Ask)
- Container: `bg.surface`, 1 px ink border, radius `md`, width = reading column.
- Textarea: `prose` size (16/24), padding 16 16 8; placeholder
  "Ask about a customer, commitment, policy, or ticket…".
- Footer row 44 px, padding 0 8 0 12: left = scope chips (sm, 28): `Account ▾`,
  `Sources ▾`, `Date ▾`; right = submit (md 36, primary, icon `arrow-up`, label
  "Ask" visible ≥ 768). Chips and submit share the row's center axis.
- Enter submits, Shift+Enter newline; `/` focuses the composer from anywhere on Ask.
- Placed at the **top** of the Ask page (a research query, not a chat input); after
  an answer, it stays at the top with the question editable for refinement.
- Detected account mention appears under the composer as a mono line:
  `INTERPRETED · Acme Corp  [Scope to Acme Corp]` — only for accounts inside the
  member's account scope; out-of-scope names produce no interpretation line.

**Scope chips / filters** — sm height, radius `sm`, 1 px `border.default` at rest; ink
border + `bg.muted` when a value is set, label shows value ("Account: Acme Corp") with
an `x` clear button (own hit target). Popover menus list only values the user can
see: accounts in their account scope, source types with readable documents; any
counts shown are computed inside their authorization predicate.

**Tags** — `control.xs` 20 px, `label` type, radius full, 1 px ink border, padding 0 8,
`bg.canvas` (or `bg.muted` for neutral emphasis). Source-type tags include the type
icon at 12 px.

**Citation chip (inline)**
- 18 px tall, min-width 18, padding 0 4, `metadata` mono 11/16, radius `xs`.
- Rest: `accent.lavender.subtle` bg, `accent.lavender.text` text. Hover: 1 px
  `accent.lavender.text` border + tooltip (source title, type, date) after 300 ms.
  Selected (its evidence open): `accent.lime` bg, ink border, `text.primary`.
- Baseline-aligned to prose: digits sit on the prose baseline; the chip is a
  `button` with `aria-label="Citation 3: HAL-4821, 14 Aug 2026"`.
- Multiple citations: chips separated by 2 px, never merged into ranges (each must be
  independently inspectable).

**Answer block**
```
PARTIAL · 3 SOURCES CITED · NEWEST 18 SEP 2026 · 4.2 S           [Copy] [Trace]
───────────────────────────────────────────────────────────────── (hairline)
ⓘ 1 Confidential source you can access — including the most recent evidence —
  wasn't sent to the AI model because of Halden's AI data policy. It's listed
  below under "Not used to draft this answer."
ⓘ Some statements couldn't be verified against sources and were removed.

During the June kickoff call, Acme was told SCIM would ship in Q3 [1]. On 14 Aug,
Support told Acme SCIM was expected in October [2]. A Product decision record dated
18 Sep says GA moves to December, with an Acme beta in November [3].

┌ POTENTIAL CONFLICT ───────────────────────────────────────────────┐ (pink callout)
│ SCIM delivery date                                                 │
│ 14 AUG 2026 · TICKET · WORKING            "expected in October" [2]│
│ 18 SEP 2026 · DECISION RECORD · SYSTEM OF RECORD                   │
│                                          "GA moves to December" [3]│
│ These sources may disagree. The most recent is the Product         │
│ decision record (18 Sep 2026). Lattice can't confirm which is      │
│ current.                                                           │
└────────────────────────────────────────────────────────────────────┘

WHAT THE EVIDENCE DOESN'T ESTABLISH
– Whether Acme has been told about the December date.
```
- Status line: `label` style, `text.muted`, with status word colored only for
  non-`answered` states (plus icon).
- Prose: `prose` style; claims flow as paragraphs (not bullet soup); max 68ch. Every
  sentence shown is a validated claim; there is no uncited prose and no rendering of
  removed claims or their positions (no "[removed]" placeholders).
- Notices (`status.info`, icon + text; at most three, in this order):
  - AI egress: "N Confidential sources you can access weren't sent to the AI model
    because of Halden's AI data policy." Wording always makes clear the user *is*
    authorized — this is a policy about the model, not about them. Adds "…including
    the most recent evidence" when applicable.
  - Removed claims: "Some statements couldn't be verified against sources and were
    removed."
  - Sensitivity (monochrome, `lock`): "Uses Confidential sources — share only with
    people who have access."

**Potential-conflict callout** — `status.conflict` background/border, radius `md`,
padding 16, `label` header `POTENTIAL CONFLICT` with `git-compare` icon. Positions are
a 3-column grid: date (mono, tabular), source kind + authority (mono), quote (sans) +
citation chip. Rows ordered oldest → newest. Last line states facts only (which is
most recent, which has higher authority) and "Lattice can't confirm which is
current." It never uses "supersedes", "correct" or "resolved".

**Supersession notice** — used only when source metadata establishes supersession:
same callout geometry in neutral styling (`bg.sunken`, ink border), label
`SUPERSEDED`, text "Refund policy v1 was replaced by Refund policy v2 on 3 Mar 2026
(per the source)." Both documents remain inspectable.

**Stale notice** — inline row above prose: `clock` icon, `status.warning` text on
`status.warning` bg, radius `sm`, padding 8 12: "The most recent evidence available
to you is from 12 Mar 2026."

**Evidence list** (below answer; rows, not cards)
```
EVIDENCE ─────────────────────────────────────── 3 cited · 4 also retrieved
[1] ▢ DOC     Acme kickoff — call notes                        12 JUN 2026
              Acme Corp · Maya Okafor
              "…we're targeting SCIM provisioning in Q3…"
──────────────────────────────────────────────────────────────────────────
[2] ▢ TICKET  HAL-4821 · SCIM provisioning ETA                 14 AUG 2026
…
NOT USED TO DRAFT THIS ANSWER (AI data policy)                    1 source
[–] ▢ DOC · CONFIDENTIAL  Acme — executive escalation notes    20 SEP 2026
ALSO RETRIEVED (not cited)   ▸ expand
```
- Row grid: `[28px chip][auto tag][1fr title+meta+excerpt][auto date]`, padding 12 0,
  divider `border.default`. Title `h3` at 14/20 600 (evidence titles use body-strong
  size to keep density); meta `metadata` `text.muted`; excerpt `body-small`
  `text.secondary`, 2-line clamp, quote portion marked with a lime underline (2 px,
  `text-decoration-color: accent.lime`… plus `mark` semantics).
- Whole row is a button opening the inspector; hover `bg.sunken`; selected row has
  2 px ink bar at left.
- Uncited-but-retrieved evidence is collapsed by default (progressive disclosure).
- Evidence excluded from the prompt by the AI egress policy is shown expanded in its
  own group (the user is authorized to read it, and it may matter), without a
  citation number.
- The evidence list is built only from the response's `evidence[]`; citation numbers
  map 1:1 to its items, so a chip can never point to evidence that isn't present.

**Source inspector**
- Docked panel (see grid), `bg.canvas`, 1 px ink left border; header 56 px: source-type
  tag, citation number, close (`x`, Esc). Title `h2` wraps to 3 lines max.
- Metadata: definition list in 2 columns — `label` keys (`SOURCE`, `TYPE`, `DATE`,
  `AUTHOR`, `ACCOUNT`, `SENSITIVITY`, `AUTHORITY`, `REF`) and `metadata`/`body` values.
  Rows 28 px. Absent values are omitted, not "N/A". `ACCOUNT` comes from
  `authz.visible_document_account` (decision D-8): for a document the viewer reads
  through an explicit grant while its account is outside their account scope, the row
  is omitted -- never replaced by a substitute name. The same rule applies to the
  evidence row's meta line and to any account facet.
- Context: previous chunk (muted, 3-line clamp, expandable) → cited chunk (full,
  `bg.sunken`, radius `sm`, padding 12) with the verified quote in
  `<mark>` = `accent.lime` bg + `text.primary`, preceded by a mono label
  `CITED PASSAGE` → next chunk (muted, clamp).
- Footer: `Open in source ↗` (secondary button, only when a validated `uri` exists),
  `Copy link`.
- Superseded / stale / confidential notices appear under the metadata as one-line
  notices with icons.
- Keyboard: `[`/`]` previous/next citation; focus moves to the inspector title on
  open, returns to the originating chip on close.

**Tables** (Sources, Access, Activity)
- Header 32 px, `bg.muted`, `label` type, 1 px ink bottom border (reference echo).
- Rows 40 px, 1 px `border.default` dividers, hover `bg.sunken`; no zebra (hover is
  enough in apps; zebra conflicts with hover and selection).
- Numbers right-aligned, `tnum`. Dates in `metadata` style, absolute + relative on
  hover ("14 Aug 2026" / tooltip "43 days ago").
- Truncate single-line cells with ellipsis + tooltip; never wrap IDs.
- <768: tables become stacked row blocks (label/value pairs) — never horizontal page
  scroll; ≥768 with overflow: horizontal scroll inside the table container with a
  sticky first column.

**Forms** — single column, max 560, labels above, 16 px between fields, 32 px between
groups, actions right-aligned at the bottom (primary rightmost). Inline validation on
blur; submit-level error summary at top with links to fields.

**Dialogs** — max 560 (confirm 440), radius `lg`, ink border + `shadow.overlay`,
header h3, body `body`, footer actions. Destructive confirms restate the effect in
plain words ("Maya will immediately lose access to Kestrel Bank content.").
Focus trapped (Radix Dialog), Esc closes, return focus.

**Drawers/sheets** — see grid. Scrim `rgba(17,17,17,0.24)`.

**Tooltips** — `bg.inverse`, `text.inverse`, `caption`, radius `sm`, 300 ms delay,
never the only way to get information needed to complete a task.

**Stage marker** (traces, sync steps) — 20 px circle, `accent.lime` fill, 1 px ink
border, mono 11 digit; failed stage uses `status.error` bg with `x` icon; skipped uses
`bg.muted`.

**Trace view — asker's own** — vertical list of stages: marker · stage name (body) ·
one-line human summary ("46 candidates from 2 source types; 3 claims verified") ·
duration right-aligned (mono, tnum). Expanding a stage reveals details; raw JSON in a
`bg.inverse` code block. Evidence the asker can no longer read shows "No longer
accessible", and if the answer cited any such evidence the answer text is hidden.

**Trace view — admin (other members)** — the same stage list with names, statuses,
error codes and durations only. No question, answer, evidence, counts, scope, account
or source names; a one-line explanation says why ("Content of other members' questions
isn't visible to administrators").

**Access explanation** (Access page) — sentences, not policy objects:
"Maya can see **Acme Corp** and **Northpeak Logistics** content (assigned accounts),
up to **Internal** sensitivity, plus **1 private channel synced from Slack**." A
source-synced group is named only if the admin is a member of it; otherwise it
appears as "Private channel (synced from Slack)" with its opaque reference and member
list. Change-preview dialogs describe effects as rules ("Maya will lose access to all
Kestrel Bank content"); document counts appear only over documents the admin can
read (`security-model.md` §10).

**Sources page** — admins: all sources, aggregate totals, templated failures with
opaque references for records they can't read. Members: only sources containing
documents they can read, with counts computed inside their predicate.

### B13. Product states (Ask and supporting pages)

| State | Presentation | Copy (starting point) |
| ----- | ------------ | --------------------- |
| First-time user | `display` heading "Ask across Halden's docs, tickets and Slack." + 1-line description of what answers include (citations, dates, potential conflicts) + a mono line listing *their* accessible source types and account scope. No generated "suggested questions" (they would be placeholder AI content) | "You can search 3 sources · Acme Corp, Northpeak Logistics" |
| Empty knowledge base | Same layout; composer disabled; explanation + (admin) link to Sources | "Lattice hasn't indexed any sources you can access yet." |
| Loading | Staged progress line under the composer (`label` style): `SEARCHING` → `FOUND 7 SOURCES` → `DRAFTING` → `VERIFYING CITATIONS`; evidence renders as soon as retrieval finishes; `aria-live="polite"` | — |
| Answered | Answer block + evidence | — |
| Partial | Answer + removed-claims notice and/or "What the evidence doesn't establish" | "Some statements couldn't be verified against sources and were removed." |
| Potential conflict | Potential-conflict callout (at most one expanded; others listed) | "These sources may disagree… Lattice can't confirm which is current." |
| Superseded | Supersession notice (only from source metadata) | "Refund policy v1 was replaced by v2 on 3 Mar 2026 (per the source)." |
| Evidence only (AI egress policy) | Neutral block + evidence list; no answer prose | "Lattice found 3 sources you can access, but Halden's AI data policy doesn't allow sending Confidential content to the AI model, so no summary was drafted. Review the sources below." |
| Stale | Stale notice | "The most recent evidence available to you is from March 2026." |
| Ambiguous | Question + choice buttons (secondary, md) | "Which account do you mean? Acme Corp · Acme Health" |
| Insufficient / no accessible evidence | Neutral block (no alarming color): what was searched + suggestions | "I couldn't find evidence you have access to that answers this. Searched: docs, tickets, Slack · Acme Corp · all dates." |
| Restricted request | **Identical to insufficient** (SEC-5) | — |
| Model/provider failure | `status.error` callout; evidence still shown if retrieval succeeded; Retry | "Lattice found evidence but couldn't draft an answer. You can review the sources below or try again." |
| Claims unverified (answer withheld) | Neutral block (`status.info`) + evidence list; no answer prose | "Lattice couldn't verify an answer against the sources it found, so none is shown. Review the evidence below." |
| Network failure | Inline error with Retry; question preserved | "Couldn't reach Lattice. Check your connection and try again." |
| Session expired (401) | Redirect to sign-in preserving the draft question in sessionStorage | — |
| Permission denied (admin route) | Page-level message | "You need administrator access to view this page." |
| Source unavailable / partially unavailable | Notice row on Ask: "Slack hasn't synced since 2 days ago; answers may miss recent messages." Sources page shows status | — |
| Ingestion failure | Sources table status `Partial` / `Failed` tags, failure codes list | — |

### B14. Responsive behaviour

- Review widths: 1440, 1280, 1024, 768, 390.
- <768: top bar (52 px) with menu button, page title, and user menu. The composer is
  full-width at the top of Ask but **not sticky** — a sticky composer steals reading
  space on phones; once the user scrolls past it, a 44 px "New question" button
  appears in the top bar and returns focus to the composer.
- Inspector: docked ≥1280, drawer 768–1279, bottom sheet <768 with drag handle and a
  visible close button.
- Reading column never exceeds 68ch prose; on wide screens extra space goes to margins
  or the inspector, never to wider prose.
- Tables: see §B12.

### B15. Accessibility (WCAG 2.2 AA target)

- Contrast: all text tokens ≥ 4.5:1 on their intended backgrounds (measured above);
  non-text UI boundaries ≥ 3:1 (ink borders on inputs; hairlines are never the only
  boundary of a control).
- Keyboard: full operation without mouse; visible focus; skip link to main; logical
  order (composer → answer → evidence → inspector).
- Semantics: landmarks (`nav`, `main`, `aside` for inspector, `header`), headings in
  order, lists for evidence, `table` for tables, `mark` for cited passages, `button`
  for citation chips.
- Live regions: stage progress `polite`; errors `assertive` only for blocking failures.
- State never by color alone: icon + text on all status indicators.
- Reduced motion honored (§B10). Zoom to 200% without loss of content; text spacing
  overrides don't break layout.
- Target size ≥ 24×24 CSS px (AA 2.5.8); 44 px on touch layouts.

### B16. Implementation notes

- CSS: CSS Modules + CSS custom properties from `tokens.css`. No utility-class
  framework: tokens + a small set of layout primitives (`Stack`, `Cluster`, `Grid`,
  `Box`) keep spacing systematic and prevent arbitrary values. Stylelint forbids raw
  colors and non-token spacing values in component styles.
- Accessible primitives: **Radix UI** (Dialog, Popover, DropdownMenu, Tooltip,
  VisuallyHidden) for focus management — the concrete need is dialogs, drawers,
  popovers and menus with correct focus trapping and ARIA.
- Visual QA checklist per screen (Phase 7): shared axes checked with an overlay grid;
  equal control heights; icon/text centering; truncation with long account names and
  200-character titles; all states rendered with fixtures; five review widths.
