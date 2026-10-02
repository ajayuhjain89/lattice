# Lattice — Design Direction (product-design overhaul)

Status: **Accepted (D0, 2026-10-02)** — the locked direction for this overhaul and the
source of truth for its implementation (D1–D7). It is not reopened while the overhaul is in
progress; the system is judged and refined only once it is implemented across the product.
On completion (D7), `design-system.md` is rewritten as v2 to describe the implemented system,
and Parts C–F of the old system are retired.
Scope: the web app (`apps/web`) and a new public landing page. Frontend only — no API,
schema or authorization change; if an existing API deficiency makes an approved UX
impossible, implementation stops and reports it instead of inventing frontend data.
Phase 11 is not started.
Owner: Product design / product engineering. Drafted: 2026-10-02.

---

## 1. Audit of the current frontend

Inspected: every route and component in `apps/web/src`, `tokens.css`, `globals.css`, the
screenshot harness output (31 screens × 5 widths), `design-system.md` Parts A–F,
`product-brief.md`, `security-model.md` §1 and §10, and `implementation-plan.md` Phases 7–10.

### 1.1 What is right and must survive any redesign

These are product decisions, not styling. The redesign must keep all of them:

- **Answers read as a brief, not a chat.** The composer sits at the top as a research query;
  there are no bubbles, avatars or personas.
- **The trust copy.** "Lattice can't confirm which is current", "you are authorized — this
  is a policy about the model, not about you", "Lattice doesn't say which": the words carry
  the security model and are among the best parts of the product.
- **Every state is designed** (B13). This covers 13 Ask states plus the rate-limit, identity
  and not-available pages, all with fixtures.
- **Honest progress.** There are no invented pipeline stages (D-6 is still open).
- **The security rules in the UI.** SEC-5 (unreadable = nonexistent), SEC-14 (no
  side-channel enumeration), SEC-15 (admin ≠ content) and D-8 (no substitute account
  names) are enforced in components and tested.
- **The engineering discipline.** Token-only colour (Stylelint), CSS Modules, Radix for
  focus management, axe on every state, geometry assertions at five widths, `en-GB`
  absolute dates and tabular figures.

### 1.2 Why the current UI doesn't reach the bar

| # | Finding | Evidence |
| - | ------- | -------- |
| 1 | **The identity is borrowed.** The palette, keylines, mono labels and pill tags were measured from a third party's portfolio handout (`docs/references/FDE_Portfolio_…pdf`, design-system Part A). Lime/lavender/pink belong to that document, not to permission-aware evidence. Take the logo away and the screens read as "tasteful developer handout", not Lattice. | `tokens.css` accents; Part A2 |
| 2 | **Geist is the default typeface of a generation of AI SaaS products.** Answers, tables and headings share one neutral grotesque, so an answer looks like UI chrome instead of a document you can quote. | `layout.tsx` |
| 3 | **The mono uppercase label is everywhere** (20 module uses, plus every table header, crumb, status line and section). When everything is a label, nothing is. It makes the app shout in small caps. | `t-label` |
| 4 | **Colour meanings collide.** Lavender is links, citations, focus, info notices *and* the sign-in headline. Lime is the selected citation, the cited passage, stage markers *and* success. A pink tint fills the potential-conflict callout, the loudest object on the page — for information the product says is *not* a failure. | Part B2; `ask-answered@1440` |
| 5 | **The signature ideas are invisible.** Permission boundaries, AI egress, provenance and verification are Lattice's whole value, yet they appear only as sentences and small tags. Nothing visual is unique to Lattice. | All screens |
| 6 | **Chrome spends space the work needs.** A 240 px sidebar holds 3–5 links. The 56 px page header shows one mono word ("ASK"). At 1280 px, nav + reading column + inspector leave the answer about 600 px. | `AppShell`, `PageHeader` |
| 7 | **Navigation is shallow, and inconsistent between widths.** "This session" history lives in tab memory although `/v1/activity` exists (Phase 8). There is no way to jump to a member, a source or a past question without the mouse. The rail (768–1279) hides labels. | `Sidebar.tsx`, `nav.ts` |
| 8 | **Each data surface is its own one-off.** There are four tables (Sources, Activity, Access, Providers), three input styles (sign-in, scope filter, access search) and seven separate "stack below 1024 px" rules. The `Stack`/`Cluster` layout primitives promised in B16 were never built, and 162 raw px values live in component CSS. | grep of `src/components` |
| 9 | **The evidence interaction stops at "open a panel".** There is no view of how claims and sources relate across the whole answer: you open citations one at a time. The inspector's metadata is a generic definition list; provenance (source → document → version → passage → verified quote) is never shown as a chain. | `EvidenceBody.tsx` |
| 10 | **Admin surfaces explain in prose what a diagram would show at a glance.** The egress policy ("Up to Internal", "Not allowed") and member access ("clearance, scope, departments, groups, grants") are inherently two-dimensional, but they are rendered as lists. | `providers@1440`, `access-member@1440` |
| 11 | **There is no public face.** `/` redirects to `/ask`, and sign-in is a two-column page that repeats the Ask intro's three "principles" verbatim in different words. | `page.tsx`, `SignIn.tsx`, `AskIntro.tsx` |
| 12 | **Empty, loading and error states are correct but anonymous.** They are generic blocks that don't feel like the same product as the answer. | `States.module.css`, `NotAvailable` |

(The harness screenshots in `e2e/screens/` predate the Phase 9 review closure: the member
page still shows a private channel's reference and member list. Current code doesn't render
them, and the screens are regenerated in step D7.)

---

## 2. The idea

**Lattice shows its work, and shows its edges.**

Every other knowledge tool hides two things: *where* a statement came from, and *what it
couldn't use*. Lattice's product exists to make both visible. So the visual system is built
from two recurring forms:

1. **The thread: provenance made visible.** Claims, exhibits, passages and traces are
   connected by numbered **exhibit markers** and drawn **provenance trails**. Anything
   Lattice says can be followed back to its source in one gesture.
2. **The edge: boundaries made visible.** What sits outside a boundary — content the AI data
   policy kept from the model, a page you can't open, a read-only setting — is drawn with
   one texture: **boundary hatching**. Hatching is Lattice's visual word for "not crossed".

These forms meet in the **lattice**: a grid of rows and columns where filled cells are the
relationships that hold. Lattice draws real relationships this way, and only real ones:

| Lattice | Rows × columns | Where |
| ------- | -------------- | ----- |
| Provenance lattice | claims × exhibits (cell = this claim cites this exhibit) | Ask brief |
| Egress lattice | data levels (Internal / Confidential / Restricted) × providers (+ "stays in Lattice") | AI providers |
| Access lattice | levels × reach (accounts, departments, groups, grants) for one member | Access → member |
| Brand mark | 3 × 3 crossing lines, one cell lit | Everywhere |

That is the logo test. A screenshot shows marginal exhibit numbers, hatched edges, a quiet
paper ground with ink type, a serif answer and a small lattice of filled cells — and none of
those appear together in any other product.

### What this direction refuses

- No gradients, glass, glows, blobs or "AI sparkle" iconography. Lattice is not magic. It
  is checked.
- No card grids. Surfaces are ruled paper: rows, rules and margins. A container exists only
  for state, interaction or a boundary (keeps B1.1).
- No decorative data. Every lattice cell, count and date comes from the API response on
  screen. The landing page uses one labelled synthetic example and no invented metrics,
  customers or testimonials.
- No oversized empty heroes. The landing hero *is* a product moment: a real brief, rendered.
- Colour never carries meaning alone (keeps B15), and hatching never stands in for something
  the viewer may not know exists (§6.4).

---

## 3. Personality and voice

Four words: **exact, calm, candid, legible.**

- *Exact*: tabular figures, absolute dates, verbatim quotes, version and character offsets
  where they help.
- *Calm*: one signal colour at a time, and disagreement shown without alarm.
- *Candid*: the product says what it couldn't do, and why, in the first sentence.
- *Legible*: reading comes first. The answer is set like a document, not a widget.

Copy keeps today's rules: first person is never used for Lattice in UI chrome ("Lattice
can't confirm…"); the one exception, "I couldn't find evidence…", comes from the API and is
kept verbatim. Buttons are verbs. Policy notices always say the person *is* authorized when
they are.

---

## 4. Foundations

### 4.1 Typography — three voices with three jobs

| Voice | Family | Job | Why |
| ----- | --------------- | --- | --- |
| **Record** (serif) | **Newsreader** (Production Type, OFL, variable, optical sizes 6–72) | Answer prose, quoted passages, display headlines on landing, sign-in and Ask | Answers are documents people quote to customers. A text serif with optical sizing reads as *the record*, and separates generated-and-verified text from interface chrome at a glance. No mainstream AI or SaaS app sets its answers in a serif. |
| **Interface** (sans) | **Schibsted Grotesk** (OFL, variable) | Navigation, controls, tables, forms, admin copy | A newspaper-born grotesque with more character than Inter or Geist (open apertures, distinct `a`/`g`), dense enough for tables, and it pairs with Newsreader's news-text heritage |
| **Ledger** (mono) | **IBM Plex Mono** (OFL) | IDs, refs, dates, durations, codes, exhibit numbers | Clear `0/O`, `1/l/I` at 11–12 px; slab serifs give machine facts their own texture beside the grotesque |

Rules:
- **Mono is sentence case by default.** Uppercase tracked mono is kept for exactly two uses:
  the status keyword of a brief (`ANSWERED`, `PARTIAL`…) and the exhibit tag. Section headings
  become sans 13/600 with a count in mono. This one change removes most of the "handout" feel.
- The serif never appears in controls, tables or labels. The sans never sets answer prose.
- Fonts are self-hosted with `next/font/local` from `@fontsource-variable/*` files: the CSP
  allows `font-src 'self'` only. Subset to Latin + Latin-1 + punctuation; target ≤ 160 KB for
  all three families combined (variable files). Fallbacks: `Iowan Old Style, Georgia, serif`
  / `system-ui` / `ui-monospace`.
- **Gate:** a specimen route renders all three families at every size in the scale, with the
  `I l 1 | O 0`, tabular-digit and 200 % zoom checks, before D2 starts. If Schibsted Grotesk
  fails density in the 40 px table rows, the fallback is **Public Sans**; if Newsreader's
  small sizes fail on Windows rendering, the fallback is **Source Serif 4**.

Scale (px, line-height, weight). Role names stay close to today's so the migration is
mechanical:

| Role | Voice | Size / line | Weight | Use |
| ---- | ----- | ----------- | ------ | --- |
| `display` | Record | 44 / 48 (landing 56 / 60) | 500, opsz 60 | Landing hero, Ask desk heading, sign-in |
| `title` | Interface | 26 / 32 | 650 | Page titles |
| `lead` | Record | 21 / 32 | 450 | Direct-answer claims (lead paragraph of the brief) |
| `prose` | Record | 17 / 28 | 400 | Supporting claims, passages in the inspector (max 66ch) |
| `heading` | Interface | 15 / 22 | 600 | Section, dialog, evidence titles |
| `body` | Interface | 14 / 20 | 400 | Default UI |
| `small` | Interface | 13 / 18 | 400 | Excerpts, secondary rows, help |
| `section` | Interface | 13 / 18 | 600 | Section headings (replaces mono uppercase labels) |
| `meta` | Ledger | 12 / 16 | 400 | Dates, IDs, refs, counts, codes |
| `mark` | Ledger | 11 / 14 | 500, uppercase +0.06em | Status keyword, exhibit tag — only |

### 4.2 Colour — paper, ink, and five signals

The ground changes from yellow cream (`#FFFCF4`) to a **neutral archival paper**. It is
quieter and cooler, and separates Lattice from the warm-cream look common to AI products.
Ink is a blue-black. One brand hue, **verdigris** — the oxidised-copper green-blue of archive
fittings and survey instruments — does one job: *provenance* (citations, links to evidence,
trails, focus). It is not success green and not SaaS blue.

**Neutrals**

| Token | Value | Use |
| ----- | ----- | --- |
| `paper` | `#F4F3EF` | App and page ground |
| `sheet` | `#FCFCFA` | Reading surfaces: the brief, inspector, dialogs, inputs |
| `well` | `#EBEAE4` | Hover, sunken blocks, table header |
| `rule` | `#D6D4CC` | Hairlines and row dividers (decorative only) |
| `rule-strong` | `#878479` | Control boundaries (3.4 / 3.7 / 3.1:1 on paper, sheet, well) |
| `ink` | `#16181D` | Text, keylines, primary buttons (16.0:1 on paper) |
| `ink-2` | `#474A52` | Secondary text |
| `ink-3` | `#63666D` | Metadata, placeholders (5.2 / 5.6 / 4.8:1 on paper, sheet, well; 4.9:1 on the ochre tint) |

**Signals — one job each**

| Signal | Text / mark | Tint | Job |
| ------ | ----------- | ---- | --- |
| **Provenance** (verdigris) | `#0D6560` (6.2:1 paper, 5.7:1 tint) | `#DCEDEA` | Citations, exhibit links, trails, focus ring, the lit lattice cell. *"This can be followed."* |
| **Passage** (marker) | ink on `#F6DE79` | `#FBF0C2` | The verified quote in an excerpt or inspector, and the selected citation. *"This exact text."* Always behind ink, never a boundary. |
| **Disagreement** (plum) | `#76336E` | `#F1E4EE` | Potential conflict and supersession marks. Used on a rule and marker, not a filled box. |
| **Time** (ochre) | `#7D5300` | `#F6EBD2` | Stale evidence, old syncs |
| **Fault** (vermilion) | `#B0301C` | `#F8E2DC` | Errors, failed syncs, invalid input |
| **Healthy** (moss) | `#3C6223` | `#E5EDD9` | Sync succeeded. Always with a check icon, and visually distinct from verdigris in hue *and* by icon |

**Boundary** is not a colour. It is a texture: 45° hatching in `ink` at 14 % opacity on a 6 px
pitch, drawn as a CSS `repeating-linear-gradient` (no image, CSP-safe), plus a `rule-strong`
dashed edge. With `forced-colors: active`, the hatch is dropped and the dashed edge is kept.

Sensitivity stays monochrome, as today (Internal: no badge; Confidential: outlined + lock;
Restricted: ink-filled + lock). Sensitivity is stable and serious, and should not compete
with the signals.

Budget: at most one tinted region above the fold on Ask. Verdigris and marker can coexist
(that's the thread). Plum, ochre and vermilion are never tinted at the same time: a lower
priority signal steps down to a line with an icon.

**Dark theme:** the token architecture (§4.6) is built with a dark mapping from day one,
because three-layer tokens make it nearly free. It is not shipped: there is no user-facing
theme preference in this overhaul (§10, decision 3).

### 4.3 Space, grid and layout

- 4 px base, unchanged token names. New: **`Stack`, `Cluster`, `Inline`, `Page`, `Split`**
  primitives (they replace the per-module margins and the seven separate stacked-row rules)
  and a Stylelint rule that forbids raw px in component modules outside an allow-list
  (borders, icon sizes).
- **The margin column.** Reading surfaces get a 3-column grammar:
  `[margin 56][measure ≤ 66ch][notes 220]`. Exhibit numbers sit in the *margin*; source
  sidenotes sit in the *notes* column at ≥ 1440 px when the inspector is closed. Below that,
  notes fold inline. This grammar is shared by the brief, the inspector, the evidence page,
  the trace and the landing hero, so they align like pages of one book.
- **Admin pages** use a 12-column grid, max 1200 px, with the same left margin column so
  page titles, table first columns and the brief's prose share an x-axis.
- Gutters 32 / 24 / 16 at ≥1280 / ≥768 / <768 (unchanged).

### 4.4 Shape, rule and elevation

- Radii are smaller and more deliberate: 2 px (marks, highlights), 4 px (controls, tags),
  8 px (popovers, dialogs, sheets). Pills disappear: tags become squared 4 px labels, so
  exhibit markers and tags share one geometry.
- Rules: 1 px hairlines (`rule`) structure everything; a 1 px `ink` rule marks only *headers
  of authority* (the brief's status line, a table header, a dialog title). Today's ink keyline
  on every container goes away.
- One elevation, for floating layers only: `0 1px 0 rule, 0 12px 28px rgb(22 24 29 / 10%)`.

### 4.5 Iconography

- Lucide stays as the base set (1.5 px stroke at 16 px; ISC). It is consistent and complete,
  and replacing it gains nothing.
- **Six Lattice glyphs** are drawn on the same 16 px grid and stroke, to carry the product's
  own concepts: `exhibit` (numbered square), `passage` (lines with one marked), `boundary`
  (hatched square), `egress` (arrow crossing a hatched edge), `trail` (three linked nodes) and
  `lattice` (3 × 3, the brand mark). The source types get distinct silhouettes: document =
  folded page, ticket = notched ticket, chat = thread.
- Icons are never alone (unchanged B9).

### 4.6 Token architecture

Three layers in `tokens.css`, enforced by Stylelint:
1. **Palette**: raw ramps (`--verdigris-700`), used only inside `tokens.css`.
2. **Semantic**: roles (`--signal-provenance-text`, `--surface-sheet`, `--boundary-hatch`).
   Components may use only these.
3. **Component**: the few values a primitive owns (`--citation-size`, `--row-height-dense`).

A light theme and a dark theme each map palette → semantic. A contrast script (Vitest) checks
every declared text/background pair at ≥ 4.5:1, and every control boundary at ≥ 3:1, in both
themes.

### 4.7 Motion — motion explains a relationship or it doesn't happen

| Moment | Motion | Duration |
| ------ | ------ | -------- |
| Citation selected | Marker fill sweeps left→right under the claim; the matching lattice cell and exhibit row light together | 160 ms |
| Inspector opens | Slides 12 px + fade; the passage highlight sweeps in after the panel settles | 200 ms + 160 ms |
| Brief arrives | Status line, then lead, then exhibits fade-rise 4 px, 40 ms apart | 180 ms each |
| Boundary crossed (egress notice, withheld exhibit) | None. Boundaries are static by principle. | — |
| Hover / press | Background and colour only | 100 ms |

`prefers-reduced-motion`: no translation and no sweeps; state changes are instant. Nothing
loops. The progress line's elapsed counter ticks once per second, which is not an animation.

---

## 5. Signature patterns (the parts that make it Lattice)

1. **Exhibit marker.** A squared 18 px mono numeral, `sheet` fill with a `rule-strong` edge
   at rest, verdigris edge on hover, marker fill when selected. It is the same object in the
   brief, the margin, the exhibit list, the inspector header, the trace and the landing hero.
   It replaces today's lavender chip and lime circle.
2. **Provenance trail.** A mono, chevron-separated chain:
   `Halden Drive › DR-112 · Identity roadmap decision › v1 › chars 622–888 › quote verified ✓`.
   It sits at the top of the inspector and the evidence page, and in the trace. Each segment is
   a fact from the API; nothing is inferred (D-8 rules apply to the account segment).
3. **Provenance lattice** (Ask). A compact grid under the status line: rows = claims (short
   labels "Lead", "1", "2"…), columns = exhibits by number. A filled cell means the claim cites
   the exhibit; a plum-ringed cell means the exhibit is part of a potential conflict. Hover or
   focus a cell, and the claim and exhibit highlight together; activate it to open the inspector
   at that passage. It is an HTML `<table>` with row and column headers, so screen readers get
   "Claim 2 cites exhibit 3". It appears only when the relationship isn't one-to-one: some
   claim cites more than one exhibit, or some exhibit backs more than one claim. A diagonal
   (each claim one exhibit) says nothing the margin markers don't, so it would be decoration. On phones it collapses to a one-line "3 claims · 4 exhibits · 1
   conflict" that expands.
4. **Boundary hatching.** Used for: evidence the AI data policy kept from the model (on the
   row's leading edge, and in the "Not sent to the model" group header); the "not available"
   state; read-only regions in admin; the "private channels you aren't in" acknowledgement; and
   the edge of the egress lattice. **Never** as a placeholder for an item the viewer can't
   read (§6.4).
5. **Disagreement axis** (replaces the pink conflict box). Positions are laid out on a
   horizontal date axis, oldest left, newest right, with equal weight. Each position is a
   column: date, source kind and authority, the claim with its exhibit marker. A plum fork
   glyph sits at the top-left, and the conclusion sentence sits beneath it, unchanged ("Lattice
   can't confirm which is current"). The axis *shows* chronology without crowning it. Below
   768 px the axis turns vertical.
6. **The status keyword.** `ANSWERED · 3 CLAIMS · 4 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S` stays
   as the brief's ledger line, the one place where tracked uppercase mono remains. Exclusivity
   is what makes it recognisable.

---

## 6. Surfaces

### 6.1 Public landing page (`/`, new)

Purpose: say what Lattice is, why it can be trusted, and get the right people to sign in. It
is static: no API calls, no session probe, no organization names other than the labelled
synthetic example.

1. **Masthead** (56 px): mark + wordmark, three anchors (How it works · Boundaries · For
   administrators), and a *Sign in* button.
2. **Hero, split 5/7.** Left: serif display headline — "Answers you can follow back to the
   page." — a two-sentence deck, *Sign in* (primary) and *How it works* (text link). Right: a
   real rendered brief from the Halden SCIM scenario (lead claim, two supporting claims,
   exhibit markers, the disagreement axis and the provenance lattice). It is captioned in mono
   "Example · synthetic company Halden", not a screenshot, and is built from the same
   components and fixtures as the app, so it can't drift.
3. **The problem, in four rows** (fragmented, stale, contradictory, unprovenanced, from
   product-brief §4), each a one-line ledger row with a tiny glyph. It is not a card grid.
4. **How an answer is made**: one horizontal trail with five stops (*Your access* → *Search
   inside it* → *Policy check* → *Draft* → *Verify every quote*), with the boundary hatch drawn
   at the policy stop. Each stop has one sentence. This is the trace view's own geometry.
5. **Boundaries**: the egress lattice (levels × "approved external model / your
   infrastructure / stays in Lattice"), with the sentence "The model never receives what you
   can't read — and by default, nothing above Internal leaves Lattice." Plus an
   administrators-are-not-readers line (SEC-15).
6. **For administrators**: three rows covering access explained in sentences, every change
   previewed and audited, and source health without content.
7. **Footer**: sign-in, the security-model summary, © line. No logos, testimonials, metrics or
   "trusted by".

Indexing: `robots` stays `noindex` until there is an intentional public deployment (§10, decision 1).

### 6.2 Sign-in (`/sign-in`)

- One centred **credential sheet** (480 px) on paper, with a faint full-bleed lattice grid
  behind it: 1 px `rule` lines on a 48 px pitch, fading at the edges. This is the only
  decorative use of the grid, and it ties sign-in to the landing page and the mark.
- **Production:** wordmark → serif headline "Sign in to Lattice" → the SSO explanation (kept
  verbatim) → a quiet "What is Lattice?" link to `/`. The errors stay a closed set in one
  `role="alert"` row with the vermilion edge.
- **Development:** a hatched "Development sign-in" band at the top of the sheet (a boundary:
  this path cannot run in production), then personas grouped by organization as dense rows
  (name, role, e-mail in mono). The email form becomes a disclosure ("Use another seeded
  e-mail").
- After a redirect with `?next=`, the sheet says where you were going ("Continue to Access →
  Maya Okafor") — the path only, which is the viewer's own.

### 6.3 Application shell and navigation

**Top masthead, not a sidebar.** Lattice has five destinations. A sidebar spends 240 px of
every screen on them, and that width belongs to the brief and the inspector.

```
┌ masthead 52 ─────────────────────────────────────────────────────────────────────────┐
│ ▦ Lattice  Halden │ Ask  Activity  Sources │ Access  AI providers │   ⌘K Jump…   MO ▾ │
└──────────────────────────────────────────────────────────────────────────────────────┘
  [margin]  page title / brief                                   │ inspector (docked)
```

- Groups are separated by a hairline. The admin group renders only from `/v1/me`
  (unchanged). Active item: ink text with a 2 px verdigris underline on the masthead rule.
- **Jump (⌘K / Ctrl K):** a command palette for *Ask a question…* (prefills the composer),
  destinations, **your recent questions** (from `/v1/activity`, your own only), and for
  administrators, members (from `/v1/admin/access/members`, already admin-visible) and sources.
  It searches only lists the viewer's own endpoints return. There is no document or title
  search (security-model §10 "Autocomplete: none in the slice").
- **User menu** (initials): name, role label, organization, sign out.
- **"This session" goes away.** Recent questions come from `/v1/activity` (persistent, own
  only) and show on the Ask desk and in Jump. The in-memory history store is removed.
- **Page anatomy:** title (sans 26) + one-line deck + right-aligned actions, with no
  breadcrumb bar on top-level pages. Deep pages (member, trace, evidence) get an inline back
  crumb above the title.
- **Widths:** ≥1024: full masthead. 768–1023: destinations collapse into a "Menu" sheet; Jump
  and the user menu stay. <768: 52 px top bar (mark, page name, Jump) plus a **bottom tab bar**
  (Ask · Activity · Sources · More), 56 px with 44 px targets. Admin destinations sit under
  More.

### 6.4 Rules the new visuals must obey (security)

These are design rules with the force of SEC requirements. Each becomes a test in D7.

1. **Hatching never stands in for an unreadable item.** It is drawn only around things the
   viewer is entitled to know exist: their own readable evidence that policy withheld from the
   model, a page that is "not available" (identical for nonexistent, SEC-5), and their own
   read-only settings. No hatched "hidden row", count or slot ever appears in a list (SEC-14).
2. **Every lattice is built only from the response on screen.** The provenance lattice comes
   from `answer.claims` × `answer.evidence`; the egress lattice from `/v1/admin/providers`; the
   access lattice from the member endpoint, with the private-channel acknowledgement kept as
   one hatched cell *without* a name, count or reference (D-18).
3. **The landing page makes no API calls and names no organization** except the synthetic
   example. It does not reveal which organizations use Lattice (Phase 10 sign-in rule).
4. **Jump only lists what the viewer's existing endpoints return.** It adds no enumeration.
5. **Provenance trails omit absent segments.** The account segment follows D-8.

### 6.5 Ask

**Desk** (before a question):
- Serif display: "Ask what Halden knows." The deck is unchanged in substance.
- The composer, unchanged in behaviour (Enter / Shift+Enter, `/` to focus, limits), restyled
  as a `sheet` with a 1 px `rule-strong` edge that turns ink on focus. Scope controls become
  **scope tokens** inside the composer footer, read as a sentence: "Search **all my sources**
  · **any account** · **any date** · as **Internal**". Each bold phrase opens its popover
  (same Radix popovers and the same options as today, so the API-listed accounts and readable
  types are unchanged).
- **Your reach**, beneath the composer, replaces the three principles. It shows the sources you
  can search (name, type glyph, readable-document count, freshness from `last_succeeded_at`)
  and your accounts, from `/v1/sources` and `/v1/accounts`. It answers "what can I ask
  about?". Clearance is not shown because `/v1/me` doesn't return it, and the UI must not guess.
- **Recent questions** (5, from `/v1/activity`) with status keywords; each opens its trace or
  re-asks.

**Progress:** unchanged honesty (one line, elapsed seconds, no stages; D-6 open). The line
names the sources being searched (from "Your reach") and holds the brief's geometry in a
skeleton of rules, not grey bars.

**Brief** (answered / partial):
```
[margin] ANSWERED · 3 CLAIMS · 4 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S        Copy · Trace
         ─────────────────────────────────────────────────────────── (ink rule)
         ▧ 1 source you can read wasn't sent to the AI model (AI data policy)   ← hatch-edged line
         ┌ provenance lattice ┐
 [1]     A decision record dated 18 Sep moves SCIM GA to December, with a      │ DR-112
         November beta for Acme's pilot group.  (lead, serif 21)                │ 18 Sep 2026
 [2][3]  At the June kickoff Halden told Acme it was targeting Q3. On 14 Aug,   │ Kickoff notes
         Support told Acme October.  (prose, serif 17)                          │ HAL-4821
         ── Disagreement · SCIM GA date ── axis: 14 AUG ──────── 18 SEP ──
         What the evidence doesn't establish — …
         Exhibits  3 cited · 1 not sent · 2 also retrieved
```
- The margin holds exhibit markers aligned to the first line of each claim. Inline markers
  remain after the sentence for reading flow and accessibility (each is a `button` with the
  same accessible name as today).
- Notices keep their order and wording. The stale notice is the one ochre-tinted line; the
  egress notice gets the hatch edge.
- **Exhibits list:** rows on `sheet` with a margin marker, type glyph, title (sans 15/600),
  provenance meta (mono), and the excerpt (serif 15) with the quote in marker tint. The "Not
  sent to the model" group has a hatched leading edge; "Also retrieved" stays collapsed.
- **States:** each B13 state keeps its logic and copy, mapped to the four state families in
  §7.3. Insufficient and restricted remain identical (SEC-5).

**Inspector → Exhibit viewer:** header (marker, type glyph, ←/→, close), then the provenance
trail, the title (sans 18/650), and the passage set as a page: serif 17 on `sheet`, the
verified quote in marker tint, with a verdigris bar in the margin beside the cited range.
Below: "Cited by" (the claims, each with its marker), then metadata as a compact two-column
ledger. Keys stay `[`, `]`, `Esc`. The geometry is the same docked / drawer / sheet as today,
but docked from 1280 px with the brief keeping ≥ 600 px now that there is no sidebar.

**Evidence page (`/evidence/{id}`)** and **Trace (`/traces/{id}`)** reuse the exhibit viewer
and the brief components respectively. The trace's pipeline becomes the horizontal-trail
language from the landing page (vertical on phones): exhibit-style numbered stops, durations
in mono, failures in vermilion, skipped stops hatched. The administrator's trace view shows
only the stops, with one hatched "Question, answer and evidence aren't visible to
administrators" band (SEC-15).

### 6.6 Sources

- **Members:** a ledger of the sources they can read. Each row has the type glyph, name, "N
  documents you can read", and a **freshness rule**: a thin horizontal bar whose fill and label
  show age since `last_succeeded_at` (moss < 1 day, ink < 7 days, ochre after), with the
  absolute date in mono. This is a reading of one fact the API gives, not a chart of invented
  history.
- **Administrators:** the same ledger plus permission mode (sentence form: "Follows the
  source's sharing" / "By account"), default level, totals, last-run status, failed records,
  and *Sync now*. Failures expand inline under the row (rather than in a separate section
  below the table), with an opaque-reference explanation that the mono `rec_…` makes
  self-evident.
- One `DataTable` primitive (§7.2) serves all four data surfaces.

### 6.7 Activity

- **Your questions:** grouped by day (Today / Yesterday / date), each row a status keyword +
  question (sans 15) + time (mono); it opens the trace. A filter by status is client-side
  over the loaded page only.
- **Organization activity (admins):** the same table anatomy. The question column is
  *present but hatched*, labelled "Content not visible to administrators", which makes SEC-15
  visible instead of merely explained. Every row has it, so it reveals nothing about any row.

### 6.8 Access

- **Members:** the DataTable with name and e-mail, status (Active / Suspended / Identity
  provider tag), clearance as a 3-step **level meter** (three squares, filled to the level),
  accounts, departments. Search stays.
- **Member:** the access sentence stays as the page's lead (it is the best thing on the page),
  set in serif 21. Under it sits the **access lattice**. Rows: *Clearance*, *Accounts*,
  *Departments*, *Groups & channels*, *Direct grants*; cells: the values as tags. One hatched
  cell says "Also in private channels you aren't in" (no name, count or reference, D-18). The
  identity-provider notice, actions and *Check a document* follow.
- **Change access dialog:** edit → **review as a diff**. Two columns, *Now* and *After*, rule by
  rule, with changed rows marked by a verdigris (gain) or vermilion (loss) margin bar and the
  effect sentences beneath. Then the counts (still "Counted only among documents you can
  read"), the required reason, and *Apply change*. A sheet on phones.
- **Audit log:** a ledger timeline. A date rail in the margin; each event is actor → action →
  subject, with detail rows ("Name: Pat Doe → Pat Rivera") in mono. Keyset paging stays ("Load
  older").

### 6.9 AI providers

- The read-only statement becomes a hatched band at the top ("Changed only through the audited
  `lattice provider` and `lattice org egress` commands"), so read-only is visible, not just
  said.
- **Egress lattice:** rows Internal / Confidential / Restricted; columns each configured
  provider (grouped Generation / Embedding) and a final "Stays in Lattice" column. A filled
  cell means "may be sent", a hatched cell "kept from this provider". It is computed from the
  policy and each provider's approval, exactly as the table states today. A one-line legend
  sits under it.
- Below it, the provider DataTable (trust class, approval, status, credential state, data
  terms), with credential problems in ochre and an icon.

### 6.10 Empty, loading and error states — four families

| Family | Look | Used for |
| ------ | ---- | -------- |
| **Quiet** | Sans heading, one sentence, what was searched (mono ledger), suggestions | No evidence, empty lists, nothing indexed |
| **Boundary** | Hatched square glyph, heading, one sentence; identical for nonexistent and unreadable | Not available (evidence, trace, member, admin route) |
| **Fault** | Vermilion margin bar, heading, sentence, request reference (mono), *Try again* | Network, provider failure, unexpected |
| **Wait** | Ochre margin bar, heading, the wait in plain words, draft preserved | Rate limit |

Loading uses **rule skeletons**: the real layout's hairlines and margin markers are drawn
immediately, and text arrives into them. There are no grey bars and no shimmer. The shell
never shows a spinner.

### 6.11 Forms and filters

One field anatomy: label (sans 13/600) above, control on `sheet` with a `rule-strong` edge,
help (sans 12) below, and an error with an icon plus text in vermilion below the help. Primitives
for Input, Textarea, Select, Search, Checkbox, Radio and Date. Filters in admin tables are a
**filter line** above the table that reads as a sentence, like the scope tokens.

---

## 7. Design-system primitives (target inventory)

### 7.1 Foundations
`tokens.css` (palette / semantic / component, light + dark maps) · type roles · `Stack`,
`Cluster`, `Inline`, `Split`, `Page`, `MarginGrid` · `VisuallyHidden` · contrast test ·
Stylelint rules (no raw colour, no raw spacing, no `t-label` outside `mark`).

### 7.2 Components (new ★, rebuilt ◆, kept ○)

| Component | | Notes |
| --------- | - | ----- |
| Button, ButtonLink, ExternalLink | ◆ | Primary (ink), secondary (`rule-strong` edge), quiet, danger; sizes sm/md/lg; loading keeps width |
| Field, Input, Textarea, Select, Search, Checkbox, Radio, DateInput | ★ | One anatomy (§6.11); replaces 3 ad-hoc inputs |
| ScopeTokens / FilterLine | ★ | Sentence-style filters over Radix Popover |
| Tag, LevelMeter, StatusMark | ◆★★ | Squared tags; 3-step level meter; status keyword + icon |
| ExhibitMarker | ★ | §5.1 — replaces citation chip and stage marker |
| ProvenanceTrail | ★ | §5.2 |
| Lattice (matrix) | ★ | Accessible `<table>` grid; cell states: filled, hatched, conflict, empty |
| Hatch | ★ | Texture + dashed edge; `forced-colors` fallback |
| DisagreementAxis | ★ | §5.5 |
| Notice (line) / Callout | ◆ | One-line notices; tinted callout only for the single top-priority state |
| StateBlock (Quiet/Boundary/Fault/Wait) | ★ | §6.10 — replaces `ErrorState`, `NotAvailable`, `States.module.css` |
| DataTable | ★ | Header, sortable where the API sorts, dense mode, stacked rows < 1024, sticky first column, row actions, inline expansion — replaces 4 tables |
| Tabs, Menu, Popover, Tooltip | ◆ | Radix |
| Dialog, Sheet, Drawer | ◆ | Same focus rules as today (return focus to opener) |
| CommandPalette (Jump) | ★ | Radix Dialog + listbox |
| Toast | ★ | "Link copied", "Change applied"; `role="status"`, 4 s, never the only confirmation of a write |
| Masthead, BottomBar, UserMenu, PageHeader | ★◆ | §6.3 |
| Skeleton (rule skeleton) | ◆ | §6.10 |
| Wordmark / Mark | ◆ | 3 × 3 lattice with one lit verdigris cell; 16, 20, 32 px; favicon |
| Kbd | ★ | Keyboard hints |

### 7.3 Accessibility contract (WCAG 2.2 AA, unchanged target, stricter coverage)

- All text pairs ≥ 4.5:1 and control boundaries ≥ 3:1, tested in both themes (§4.6).
- Lattices are tables with `<th scope>`, and every cell has an accessible name ("Claim 2
  cites exhibit 3"). Hover linking has a keyboard equivalent (focus).
- Hatching is never the only signal: it always comes with text or an accessible label, and
  has a `forced-colors` fallback.
- Focus: 2 px verdigris outline, 2 px offset, ≥ 3:1 against paper and sheet.
- Targets ≥ 24 px (44 on touch layouts). Bottom bar 44 px.
- Keyboard map documented on a `?` overlay: `/` composer, `⌘K` Jump, `[ ]` exhibits, `Esc`
  close, `g a / g s / g y` go to Ask / Sources / Activity.
- Reduced motion, 200 % zoom and text-spacing overrides are checked per screen in D7.

---

## 8. Implementation plan

Frontend only. Every step keeps the API contract, the security behaviours and the existing
test suites green, and updates selectors and copy in the same commit. Each step ends with
`pnpm lint && pnpm typecheck && pnpm test && pnpm build` and the Playwright desktop and mobile
projects. Steps that change visuals regenerate the screen harness.

| Step | Scope | Exit criteria |
| ---- | ----- | ------------- |
| **D0 — Direction sign-off** | This document + the visual direction board; answers to §10 | Product owner accepts or amends; open questions closed |
| **D1 — Foundations** | Fonts self-hosted (`next/font/local`), three-layer tokens with light + dark maps, type roles, layout primitives, Stylelint rules, contrast test, the six glyphs and the new mark; a dev-only `/_specimen` route rendering every token, role and glyph | Type gate (§4.1) passed at 5 widths, on macOS and Windows-like rendering (Playwright Chromium + font-smoothing off); contrast test green in both themes; bundle font weight ≤ 160 KB |
| **D2 — Primitives** | Everything in §7.2 except surfaces; component tests + axe per primitive; specimen route shows all states | Each primitive has states, keyboard and axe tests; no surface changed yet |
| **D3 — Shell & navigation** | Masthead, user menu, Jump, bottom bar, page anatomy; remove the sidebar, rail and in-tab history; recents from `/v1/activity` | Shell, keyboard and mobile Playwright suites updated and green; geometry invariants rewritten for the new axes; SEC check: Jump lists only endpoint results |
| **D4 — Ask, evidence, trace** | Desk, scope tokens, your reach, recents, progress, brief, margin markers, provenance lattice, disagreement axis, exhibits, exhibit viewer, evidence page, trace; all B13 states | All 13 state fixtures render; citation-to-evidence and hidden-trace safety tests unchanged and green; the lattice's a11y names tested; no exhibit marker for absent evidence (existing test kept) |
| **D5 — Admin & operations** | DataTable migration for Sources, Activity, Access members, Providers; access lattice; diff review dialog; audit ledger; egress lattice | Existing access, sources, activity and provider e2e green; SEC-14/D-18 tests extended to the access lattice and hatched cells |
| **D6 — Public & identity** | Landing page (static, shared components, synthetic example), sign-in (prod, dev, errors, `next`), not-found, global error | Landing makes zero `/api` requests (asserted); no organization name other than the labelled example; identity e2e green; CSP unchanged |
| **D7 — Verification & docs** | Screen harness at 1440/1280/1024/768/390 in light (the shipped theme; the dark map is checked by the contrast test and the specimen, §10 decision 3); axe for every screen and state; reduced motion, 200 % zoom and forced-colors passes; §6.4 rules as tests; rewrite `design-system.md` v2; README screenshots | All suites green twice in a row; design-system v2 merged; this document marked Implemented |

Sequencing notes:
- D3 and D4 are the riskiest (layout axes and the most tests). The plan lands them behind no
  flag: each step is a coherent whole and merges only when green.
- Machine limits (M1, 8 GB): one heavy task at a time. Builds and Playwright run serially,
  with no local model running alongside.
- Expected size: about 15–25 focused commits. No backend or OpenAPI change. If a design
  needs data the API lacks (e.g. the member's own clearance on the Ask desk), it is listed as
  a contract observation and the design does without it.

---

## 9. Risks and how they are contained

| Risk | Containment |
| ---- | ----------- |
| Serif answers read as "old-fashioned" | Optical sizing and a modern grotesque around it; validated on the specimen and on real fixtures in D1 before any surface work |
| The provenance lattice confuses rather than helps | It shows only with ≥ 2 claims and ≥ 2 exhibits, is labelled, and is fully keyboard/AT accessible. If D4 review finds it noisy, it collapses by default to the one-line summary. |
| Hatching is read as "something hidden from you" | Each hatched region is labelled in words, and the rules in §6.4 forbid hatching for anything the viewer may not know exists |
| Removing the sidebar hurts discoverability for admins | Labelled masthead groups, Jump, and admin entries under More on phones; Playwright checks that every destination is reachable by keyboard at every width |
| Test churn | Accessible names of controls and states are kept stable where the copy doesn't change; selector changes land in the same commit as the component |
| Font licensing or availability | All three families are SIL OFL; fallbacks are named in §4.1 |

---

## 10. Decisions (D0, accepted by the product owner 2026-10-02)

| # | Decision |
| - | -------- |
| 1 | **Landing page** lives at `/` inside `apps/web`. It stays `noindex` until there is an intentional public deployment. |
| 2 | **Typography**: the three-voice system — Newsreader (record: answers, passages, display), Schibsted Grotesk (interface), IBM Plex Mono (ledger: factual metadata) — subject to the D1 implementation and type-quality gate. |
| 3 | **Dark theme**: the dark token map is built in D1 so the system is structurally ready. No user-facing dark-mode preference ships in this overhaul; light is the shipped experience. |
| 4 | **Navigation**: the masthead + Jump (⌘K) direction replaces the desktop sidebar, with the proposed phone navigation. The shell is designed so future product sections can be added without another shell redesign. |
| 5 | **Brand signal**: verdigris is the primary provenance/brand signal. Lime, lavender and pink are retired. The other colours stay strictly semantic, never decorative. |

Implementation runs D1 → D7 in order, each reviewed before the next starts.

---

## 11. Implementation log

### D1 — Foundations (2026-10-02, `phase/design-overhaul`)

**Built.** Self-hosted fonts (`apps/web/scripts/build-fonts.py` → `public/fonts/`,
`src/styles/fonts.css`, `src/lib/fonts.gen.ts`); the three-layer `tokens.css` (palette
`--p-*` → semantic → component) with the light map shipped and the dark map under
`[data-theme="dark"]` only; type roles in `globals.css`; layout primitives
(`components/layout`: Stack, Cluster, Inline, Split, Page, MarginGrid); the mark, wordmark
and favicon (`components/brand`, `app/icon.svg`); Lattice glyphs (`primitives/glyphs.tsx`,
also used by the source-type tag); the development-only `/specimen` route. Every component
moved off the v1 tokens and type classes; Geist was removed. Surfaces keep their v1 layouts
on the v2 foundation until D2–D6 rebuild them.

**Type gate (§4.1): passed.** Latin total 161,312 bytes (157.5 KiB), inside the 160 KiB
budget, by limiting variable axes to the ranges the scale uses (Newsreader wght 400–600 ·
opsz 12–72, 132 → 89 KB; Schibsted Grotesk wght 400–700). `0/O`, `1/l/I` are distinct in the
ledger voice at 12 px. In the interface voice, `I` and `l` differ only by height, as in most
grotesques; identifiers are always set in the ledger voice, which carries that burden. Both
fallbacks named in §4.1 were not needed.

| Planned | As built | Why |
| ------- | -------- | --- |
| `next/font/local` | A generated `@font-face` file: one family per voice, Latin and Latin Extended faces split by `unicode-range`, metric-matched fallback faces (Georgia / Arial / Courier New with `size-adjust` and ascent/descent overrides), content-hashed files with an immutable cache policy, the three Latin files preloaded | `next/font/local` applies one set of declarations to every file of a call and puts its fallback face ahead of any second family, so Latin Extended could not be loaded on demand without being shadowed |
| Type roles of §4.1 | Adds `subtitle` (18/24, inspector and dialog titles), `body-strong`, `caption` (12/16 help text) and `code` (13/20) | Roles the existing surfaces need; no other sizes exist |
| Palette of §4.2 | Adds `--surface-well-strong` (`#E6E4DD`, pressed/selected) and a `-mark` token per signal for rules and markers (≥ 3:1); the ochre mark is `#A57A22` | The contrast test failed the first values (`#E3E1DA` gave muted text 4.39:1, the ochre mark 2.82:1) |
| v1 "info" status colour | None: neutral notices use secondary text on `--surface-well` | §4.2 has five signals; info was a sixth hue with no single job |
| Mark: 1.5 px lines throughout | Frame 1.5, inner lines 1 | At 16 px the thicker inner lines swallowed the lit cell |
| Raw-px Stylelint rule everywhere | Enforced for the rebuilt directories (`brand`, `layout`, `specimen`); widened as D2–D6 rebuild each surface | 162 raw px values live in v1 surface modules that are rewritten later |
| Shell tokens | `--header-height`, `--sidebar-width`, `--rail-width` and friends kept, marked as v1 shell geometry | Still used by the sidebar shell until D3 replaces it |

**Verification.** `tokens.test.ts`: 61 colour pairings × 2 theme maps (4.5:1 text, 3:1
boundaries), identical semantic keys in both maps, every `var()` resolving, no palette token
in a component, no v1 name anywhere. `e2e/foundations.spec.ts`: fonts only from `/fonts`
(no third-party request), preloaded, immutable; Latin Extended requested only by a page that
uses it; the page holds with every web font blocked; the type scale measured at 1440 and
390 px; no overflow at the five review widths; axe and the live contrast ledger in both
maps; the product ships the light map with no theme attribute.
