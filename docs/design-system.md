# Lattice — Design System (v2)

Status: **Current.** This describes the system as implemented in `apps/web` at the close of
the product-design overhaul (D1–D7, 2026-10). It is the reference for any new screen.
How the system was decided — the proposal, the decisions and every deviation from it —
is recorded in [design-direction.md](design-direction.md); design v1 (the Phase 7–10
system measured from a reference handout) is retired and survives only there and in the
git history.

Where the code is the authority: tokens in `apps/web/src/styles/tokens.css`, type roles in
`src/styles/globals.css`, primitives in `src/components/primitives`, and the rendered
specimen of every token, role, glyph and primitive at `/specimen` (development only).

---

## 1. Principles

**Lattice shows its work, and shows its edges.** Every statement can be followed back to
its source in one gesture, and every boundary — what policy kept from a model, what is
read-only, what isn't available — is drawn and named. Four words set the voice: *exact,
calm, candid, legible.*

1. **The answer is a record, not a chat.** Briefs are set like documents people quote;
   there are no bubbles, avatars or personas.
2. **Nothing decorative carries meaning, and nothing meaningful is decorative.** Every
   lattice cell, count, date and colour comes from the response on screen.
3. **One signal at a time.** Each colour has one job; at most one tinted region competes
   for attention above the fold.
4. **Candid in the first sentence.** A state says what happened and why; a policy notice
   says the person *is* authorized when they are.
5. **Ruled paper, not cards.** Surfaces are rows, rules and margins. A container exists only
   for interaction, a state, or a boundary.
6. **The API decides.** The interface presents permission decisions; it never computes
   them (§8).

---

## 2. Foundations

### 2.1 Surfaces and colour

Three layers of tokens: **palette** (`--p-*`, used only inside `tokens.css`) → **semantic**
(the only colours components may use) → **component** (values a primitive owns). Stylelint
forbids raw colours outside `tokens.css`; a unit test forbids palette tokens in components
and checks that every `var()` resolves.

| Semantic token | Light value | Use |
| -------------- | ----------- | --- |
| `--surface-paper` | `#F4F3EF` | App and page ground |
| `--surface-sheet` | `#FCFCFA` | Reading surfaces: inspector, dialogs, inputs, specimens |
| `--surface-well` / `-well-strong` | `#EBEAE4` / `#E6E4DD` | Hover, sunken rows / pressed, selected |
| `--surface-inverse` | `#16181D` | Primary buttons |
| `--text-primary` / `-secondary` / `-muted` | `#16181D` / `#474A52` / `#63666D` | Text (muted ≥ 4.5:1 on paper, sheet and well) |
| `--rule` | `#D6D4CC` | Hairlines (decorative only) |
| `--rule-strong` | `#878479` | Control boundaries (≥ 3:1) |
| `--rule-ink` | `#16181D` | Rules of authority: a status line, a table header, a dialog title |
| `--focus-ring` | verdigris 700 | Focus outline |

**Signals — one job each.** Each has `-text` (≥ 4.5:1), `-mark` (≥ 3:1 for rules and
markers) and `-tint`.

| Signal | Text | Job |
| ------ | ---- | --- |
| Provenance (verdigris) | `#0D6560` | Citations, links to evidence, trails, the lit lattice cell, focus. *"This can be followed."* |
| Passage (marker) | ink on `#F6DE79` / tint `#FBF0C2` | The verified quote and the open exhibit. Always behind ink. |
| Disagreement (plum) | `#76336E` | Potential conflicts and supersession: a rule and a mark, never a filled box |
| Time (ochre) | `#7D5300` | Stale evidence, old syncs, limits (a wait, not a fault) |
| Fault (vermilion) | `#B0301C` | Errors, failed syncs, invalid input |
| Healthy (moss) | `#3C6223` | Sync succeeded — always with a check icon |

Rules: colour never carries meaning alone (an icon and a word always accompany it);
plum, ochre and vermilion are never tinted at the same time (a lower-priority signal
steps down to a line with an icon); sensitivity is monochrome (Internal: nothing;
Confidential: outlined tag with a lock; Restricted: ink-filled tag with a lock).

**Boundary is a texture, not a colour:** `--boundary-hatch` (45° hatching in ink at 14 %,
6 px pitch, a CSS gradient) plus a dashed `--boundary-edge`. In forced colours the hatch
is dropped and the dashed edge kept. Its meaning is fixed in §3.5 and §8.

### 2.2 Themes

Light is the shipped theme. A complete dark map exists under `[data-theme="dark"]`, with
the same semantic keys, checked by the contrast test and shown on the specimen; it is not
exposed as a preference. A new semantic token must be added to both maps.

### 2.3 Typography — three voices

| Voice | Family | Job |
| ----- | ------ | --- |
| **Record** | Newsreader (variable, optical sizes) | Answer claims, quoted passages, display headlines (landing, sign-in, Ask desk), the access sentence |
| **Interface** | Schibsted Grotesk (variable) | Navigation, controls, tables, forms, admin copy, page titles |
| **Ledger** | IBM Plex Mono | Dates, IDs, references, counts, codes, exhibit numbers, paths |

Self-hosted (`public/fonts`, built by `scripts/build-fonts.py`): Latin and Latin Extended
split by `unicode-range` (Latin Extended loads only on a page that uses it), metric-matched
fallback faces so the swap never moves text, the three Latin files preloaded, ≈ 158 KiB in
all. `font-src 'self'` only.

| Role (class) | Voice | Size / line | Use |
| ------------ | ----- | ----------- | --- |
| `t-display` | Record 500 | 44/48 (phone 34/40; landing hero 56/60, 48/52 below 1280) | Landing, sign-in and Ask desk headlines |
| `t-title` | Interface 650 | 26/32 (phone 22/28) | Page titles |
| `t-subtitle` | Interface 650 | 18/24 | Inspector and dialog titles, viewer titles |
| `t-lead` | Record 450 | 21/32 (phone 19/28) | The brief's direct-answer claims |
| `t-prose` | Record 400 | 17/28 | Supporting claims, passages, the access sentence |
| `t-heading` | Interface 600 | 15/22 | Section and evidence headings |
| `t-body` / `t-body-strong` | Interface 400 / 600 | 14/20 | Default UI |
| `t-small` | Interface | 13/18 | Excerpts, secondary rows, help |
| `t-section` | Interface 600 | 13/18 | Section headings (with a count in `t-meta`) |
| `t-caption` | Interface | 12/16 | Field help, legends, labels |
| `t-meta` | Ledger | 12/16, tabular | Dates, refs, counts, codes |
| `t-mark` | Ledger 500, uppercase +0.06em | 11/14 | **Only** the brief's / trace's status line and exhibit tags |
| `t-code` | Ledger | 13/20 | Commands |

Forbidden: serif in controls, tables or labels; sans for answer prose; tracked uppercase
anywhere but `t-mark`'s two uses; mono for sentences. Status words outside a status line
are sentence case ("Answered", "Partial"), the same in Activity, Jump and the Ask desk.

### 2.4 Space

4 px base: `--space-0-5` (2) · 1 (4) · 2 (8) · 3 (12) · 4 (16) · 5 (20) · 6 (24) · 8 (32) ·
10 (40) · 12 (48) · 16 (64) · 20 (80, landing section rhythm). Stylelint forbids raw px
in spacing properties of every rebuilt component directory.

### 2.5 Layout and widths

| Axis | Value |
| ---- | ----- |
| Breakpoints (`lib/use-media`) | phone < 768 · tablet 768–1023 · desktop ≥ 1024 · docked inspector ≥ 1280 · wide ≥ 1440 |
| Gutters (`--gutter`) | 32 / 24 / 16 at ≥ 1280 / ≥ 768 / < 768, never less than the safe-area inset |
| Record grammar (`components/record`) | `[margin 56][measure 36rem][notes 220]`: exhibit numbers in the margin, prose at the measure, sidenotes at ≥ 1440 when nothing is docked. Shared by the brief, the inspector, the evidence page and the trace |
| Admin width (`--admin-max`) | 1200 px: Sources, Activity, Access, Audit, AI providers |
| Public width (`--public-max`) | 1200 px: landing, sign-in masthead, not found |
| Shell | masthead 52 px (rule included); phone top bar 52 px + bottom tab bar 56 px; docked inspector 400 / 440 px |

Pages own their grids in CSS modules. `Page` (admin or full width) is the shared page
container; `Stack`, `Cluster`, `Inline`, `Split` and `MarginGrid` (`components/layout`)
exist and are used by the specimen, but the product surfaces build their own grids —
reach for them only where they fit as they are.

### 2.6 Rules, radii, elevation, layers

- Rules: 1 px hairlines structure everything; a 1 px ink rule marks only headers of
  authority. No ink keyline around containers.
- Radii: 2 px (marks, highlights) · 4 px (controls, tags, markers) · 8 px (sheets,
  dialogs, popovers). No pills.
- One elevation, for floating layers only: `--elevation-overlay`.
- Layers: sticky 10 · shell 20 · drawer 30 · dialog 40 · popover 50 · toast 60.
- Controls: 20 / 28 / 36 / 44 px; rows 40 (dense 32); touch targets 44 px on phones.

### 2.7 Motion

Motion explains a relationship or doesn't happen. `--motion-quick` 100 ms (hover, press)
· `--motion-base` 160 ms (marker fill, passage sweep, link highlight) · `--motion-arrive`
180 ms with 40 ms stagger (a brief arriving) · `--motion-panel` 200 ms (inspector, dialog,
sheet); one easing. Boundaries never move. Nothing loops (the progress line's seconds
counter is text). Under `prefers-reduced-motion` every duration is 0 and translations and
sweeps are removed.

### 2.8 Icons and glyphs

Lucide at 16 px, 1.5 px stroke, never alone (a label or accessible name always). Six
Lattice glyphs on the same grid (`primitives/glyphs`): exhibit, passage, boundary,
egress, trail, lattice — plus the source silhouettes (document, ticket, chat).

---

## 3. Signature language

Use each where it carries meaning, and nowhere else. Restraint is part of the system.

1. **The mark** (`components/brand`): three-by-three crossing lines with the centre cell lit
   in verdigris. Always decorative; the name travels as text or a label.
2. **Exhibit marker** (`ExhibitMarker`, `ExhibitButton`): a squared mono numeral. Rest
   (sheet, control edge) · linked (verdigris edge and tint) · selected (marker fill, ink
   edge, `aria-pressed`) · withheld (an en dash: not cited) · skipped (hatched: a trace
   stop that didn't run) · failed (fault, a cross). The same object in the brief's
   margin, exhibit rows, the inspector, the trace and the landing page. 24 px on touch.
3. **Provenance trail** (`ProvenanceTrail`): source › document › version › range ›
   *quote verified*, an ordered list of facts the API returned; absent segments are
   omitted, never filled in (D-8 for the account segment). A wrapped trail ends a line
   with its chevron, never starts one.
4. **Verified passage**: `<mark>` in the passage signal — ink on marker yellow with a
   2 px underline; the claim whose exhibit is open takes the same underline. Selection
   uses the provenance tint, never the marker.
5. **Boundary hatching** (`BoundaryBand`, `BoundaryEdge`, `BoundaryInline`, the hatched
   lattice cell): "not crossed". Always labelled in words. See §8 for where it may appear.
6. **Lattices** (`Lattice`): a real `<table>` whose filled cells are relationships that
   hold, every cell named. Two in the product — claims × exhibits on a brief (only when
   the relationship isn't one-to-one: `latticeAddsInformation`) and data levels ×
   providers on AI providers — plus the landing page's example. Never decorative; never
   computed authorization.
7. **Disagreement axis** (`DisagreementAxis`): positions oldest → newest, evenly spaced
   (distance would imply weight), the interval written ("35 days later"), the API's
   conclusion sentence beneath, unchanged. Vertical below 768 px or with more than three
   positions. Superseded is the neutral ink form, only when a source records it.
8. **The status line**: `ANSWERED · 3 CLAIMS · 3 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S`
   under an ink rule — the one place tracked uppercase sets words.

---

## 4. Components (`src/components/primitives`)

| Component | What it is | Notes |
| --------- | ---------- | ----- |
| `Button`, `ButtonLink`, `ExternalLinkButton` | primary (ink, one per region) · secondary (control edge) · quiet · danger; sm 28 / md 36 / lg 44 | `loading` = `aria-busy` + `aria-disabled`, keeps width and focus; forced colours name their system colours |
| `Field` + `TextInput`, `TextArea`, `Select`, `SearchInput`, `DateInput`, `Checkbox`, `Radio`, `ChoiceGroup` | One anatomy: label, control, help, error (icon + text) | `Field` owns ids, `aria-describedby`, `aria-invalid`, `required`; choices sit in a `fieldset` |
| `FilterLine`, `FilterToken` | Filters read as a sentence ("Search **all my sources** · **any account** …"), each token a popover | Ask's scope tokens |
| `Tag` | Squared label for stable categories (source type, sensitivity, identity provider) | Never a signal colour |
| `LevelMeter` | Internal · Confidential · Restricted as three squares + the word | Monochrome |
| `StatusMark` | A state word with its icon; `keyword` for the status line | healthy · time · fault · disagreement · neutral |
| `Kbd` | A key hint | |
| `ExhibitMarker`, `ProvenanceTrail`, `Lattice` + `LatticeLegend`, `DisagreementAxis`, `Boundary*` | §3 | |
| `Callout` | A margin bar in the state's mark plus its tint; with a label it is a region, without one a single message (sign-in errors) | neutral · plain · time · fault · disagreement · boundary |
| `NoticeLine` | One-line notice: icon + text | neutral · time · disagreement · boundary (hatched leading edge) |
| `StateBlock` | The four state families (§5.13) | quiet · boundary · fault · wait; family written out |
| `RuleSkeleton` | Loading as the page's own rules: `prose` (reading rhythm) or `ledger` (rows under an ink header) | One status names what loads; rules are hidden from assistive tech |
| `DataTable` | Header, rows, dense mode, stacked rows below 1024 or 768, inline expansion, footer | A table that scrolls becomes a named, focusable region (`useOverflow`); no client-side sorting or filtering (no endpoint sorts) |
| `Modal`, `Drawer` | Dialogs (560 / 440 px) and sheets (right drawer; bottom sheet on phones) | Radix focus trap; focus returns to the opener; `data-modal` blocks Jump |
| `Menu`, `Popover`, `Tooltip` | Radix | Menus are non-modal |
| `Jump` | The command palette (⌘K on Apple platforms, Ctrl K elsewhere) | Lists only what the viewer's endpoints return |
| `Toast` | `role="status"`, 4 s | Echoes a result that is also written on the page; never the only confirmation |

Shared non-primitive pieces: `components/record` (the record grammar), `components/ops`
(`OpsPage`, `OpsHead`, `ViewTabs`, `AdminGate`, `OpsSection` for admin pages),
`components/public` (`PublicMasthead`), `components/states` (`ErrorState`,
`NotAvailable`, `PageFault`).

---

## 5. Patterns

1. **Shell.** Masthead (≥ 768): mark → Ask, the organization after a hairline, sections
   (Workspace; Administration with the admin capability) separated by a hairline, Jump,
   the account menu. The current page is ink, weight 600 and a 2 px verdigris rule on the
   masthead rule, with `aria-current`. It fits by measurement, folding in a fixed order
   (Jump's word; later sections into a named menu; the organization; everything into
   Menu). Phones: top bar (mark, page name over organization, page action, Jump) and a
   bottom tab bar (Ask · Activity · Sources · More); administration and the account live
   in the More sheet. Deep pages carry an inline back trail. One navigation model
   (`shell/nav.ts`) feeds all of it.
2. **Ask desk.** Display headline, the composer (a sheet; Enter asks, Shift+Enter breaks;
   `/` focuses) with scope tokens, *Your reach* (sources and accounts from the API) and
   your recent questions.
3. **Ask brief.** Status line → notices (API order and wording; the AI-policy notice is the
   one hatched line) → claims in the record grammar with margin markers → the provenance
   lattice when it adds information → disagreement axes → what the evidence doesn't
   establish → exhibits (cited, then *Not sent to the AI model* as one hatched group the
   reader can open, then *Also retrieved*). A claim citing evidence absent from the
   response is not rendered.
4. **Evidence inspector.** Docked at ≥ 1280, a right drawer at 768–1279, a bottom sheet on
   phones; reads the evidence endpoint on every opening. Trail, title, the cited passage
   with every verified quote marked, *Cited by*, source details, copy link; `[` `]` move
   between exhibits.
5. **Evidence page** (`/evidence/{id}`): the same viewer on a sheet at the record width.
6. **Trace.** The question, ledger line, scope as a sentence, the pipeline as numbered
   stops (skipped hatched, failed vermilion), claims rejected by validation, the answer as
   recorded (hidden, as a neutral callout, when anything it used is no longer readable),
   exhibits, run details. The administrator's operational view shows no content, under
   one boundary band.
7. **Sources register.** One headline sentence of totals, then a DataTable: source, last
   sync (word + icon + time; the age of the last full sync only when it adds information),
   permissions as a sentence, documents, failed records (inline expansion by opaque
   reference), *Sync now* (busy but focusable, progress announced from the listing).
   **P12.6:** *Add source* under the headline (a dialog: export format as radios, name and
   key, defaults, reason); each name opens the source's record; the last cell is the
   server's next action — *Sync now* where a sync can run (with *Map account references*
   under it when the server says so), otherwise a link to where the step happens.
   **Source record** (`/sources/{id}`): name with its type glyph, key · export format ·
   who manages it, status facts; one *next step* band (margin bar, a sentence, one control);
   an actions row (Sync now / Retry sync, Check export again, Edit settings, Pause/Resume,
   Disconnect) with "Managing this source gives you no access to its content."; then
   ruled sections — *Export* (what to upload beside the upload: native file input, real
   bytes-sent progress, then an honest "validating" with no number), *Check* (counts as
   margin-barred figures, failure codes, references without an account linking to Access),
   *Sync*, *Settings*. Dialogs reuse the Now → After table and reason field; a widening
   settings change needs an explicit acknowledgement; disconnect lists what is deleted and
   kept and asks for the key typed.
8. **Activity chronology.** Your questions grouped by day on a ruled rail: time, status
   word, the question (opens its re-authorized trace), *Ask again*. Organization runs
   (administrators) are their own page under one boundary band.
9. **Access explanation.** The member's identity, the access sentence in the record voice,
   actions or the reason there are none, then *How this access is made up*: a ledger of
   rule · value · what it gives. Private groups and unreadable grants appear once each as
   an inline boundary, never named, listed or counted.
10. **Change preview.** Edit → review. The review is the preview endpoint's reading: one row
    per rule (Now → After, + / − items, *Gains* / *Loses* with a verdigris or vermilion
    margin bar), effect sentences, the exact counts the API returns, a required reason,
    *Apply change* in a sticky action row. Every write leaves a persistent result line.
11. **Audit ledger.** "Entries can't be edited or deleted", days under ink rules, each
    entry time · action and subject · the change · actor and quoted reason; keyset paging.
12. **Egress lattice** (AI providers; editable since P12.5). The API's AI status line, a
    hatched band stating the rule (raising needs a fresh sign-in, lowering applies at once),
    the policy as ruled facts with *Change policy*, levels × providers drawn from the
    server's reading (filled may be sent, hatched not sent, each cell named and explained on
    focus), the levels that never leave Lattice, one ruled record per generation provider
    (a status line in words — *Sends nothing above Internal*, never a green "connected" —
    and configuration, approval, trust boundary, API key and latest test kept apart; *Test*
    and an actions menu), the deployment's embedding ledger. Changes reuse the access Now →
    After table; an escalation adds a boundary callout, the organization's name typed and
    *Continue to sign in again*; the return from the identity provider is the page's one
    tinted region until applied or dismissed. Keys go into empty masked fields only.
13. **States: four families** (`StateBlock`). *Quiet* (nothing to show), *Boundary* (not
    available — identical for nonexistent and unreadable), *Fault* (the API's fixed
    message, its request reference, *Try again*), *Wait* (a limit, in time-signal ochre;
    nothing is lost). Loading is `RuleSkeleton`. A page that fails to render shows
    `PageFault` (fault family, nothing from the error, Next's digest as the only
    reference, *Try again* reloads): inside the shell for a workspace page
    (`app/(workspace)/error.tsx`), under the public masthead otherwise (`app/error.tsx`).
    An address that matches nothing gets the public masthead over the not-available
    state.
14. **Public landing** (`/`). Static, server-rendered, no API call or session probe,
    `noindex`. Hero (record headline, deck, *Sign in*, a rendered brief), then the answer
    path with two readers, evidence, disagreement, boundaries and administrators, each
    demonstrated with the labelled synthetic Halden example (quoted verbatim from the
    synthetic corpus, a test checks it). No invented customers, metrics or claims.
15. **Sign-in.** Production (`/sign-in`): headline, "There is no separate Lattice
    password", the route sign-in takes; the API's closed set of three errors (fault alerts
    or a wait), also in the title; a named `next`. Development: served at the same address
    from `/sign-in/development` by the proxy when `LATTICE_WEB_DEV_SIGN_IN=true` (404
    otherwise): a hatched band, personas grouped by organization, seeded role labels only.

---

## 6. Responsive behaviour

- **Shell:** full masthead ≥ 768 (measured fit), phone bars below. Content never sits under
  the bars (measured reserve, `scroll-padding`).
- **Inspector:** docked ≥ 1280 (the reading column keeps ≥ 520 px), drawer 768–1279,
  bottom sheet < 768.
- **Tables:** stacked label/value rows below 1024 (or 768 where the columns fit); the name
  column never narrows below 11rem; a table or lattice that can't fit scrolls inside a
  named region, never the page.
- **Record grammar:** markers move inline below 768; sidenotes only at ≥ 1440.
- **Axes and paths:** the disagreement axis and step paths turn vertical on phones.
- **Landing:** hero and split sections stack below 1024, specimens at the full content
  width; the section links take their own wrapping row on phones.
- Every page reflows at 320 px and at 200 % zoom (640 CSS px) with no page-level
  horizontal scroll (tested on every route).

---

## 7. Accessibility (WCAG 2.2 AA)

- **Focus:** a 2 px verdigris outline, 2 px offset, on `:focus-visible`; never removed
  without a replacement. Skip link first on every page. After a route change focus moves
  to the page unless the page placed it. Menus, dialogs, sheets and Jump trap and return
  focus; busy controls keep it.
- **Semantics:** one `main` and one `h1` per page; sections headed in order; lattices and
  tables are tables with header cells and named cells; trails are ordered lists; markers
  are named buttons with `aria-pressed`; regions and figures are labelled.
- **Announcements:** one polite status for progress and results, one alert for a failure;
  a toast never carries the only confirmation; errors on arrival are in the page title.
- **Touch:** 44 px targets on phones (24 px for inline markers, spaced so neighbours can't
  steal taps).
- **Reduced motion and forced colours:** honoured everywhere (§2.7, §2.1); hatches keep
  their dashed edges; selected markers use system Highlight.
- **Contrast:** text ≥ 4.5:1 and boundaries ≥ 3:1 in both theme maps, checked by
  `tokens.test.ts` for every declared pair.
- **Verification:** axe (WCAG 2.2 AA tags) on every state; `e2e/closure.spec.ts` runs every
  route at rest, in forced colours with reduced motion, at 320 px and at 200 % zoom.

---

## 8. Security-sensitive UI rules

These carry the force of the security model ([security-model.md](security-model.md)) and
are tested.

1. **Hatching never stands in for something the viewer may not know exists.** It marks
   only readable evidence that policy kept from a model, a not-available page (identical
   whether nonexistent or unreadable, SEC-5), the viewer's own read-only settings, and
   one-line acknowledgements without names, counts or ids. No hatched row, slot or count
   in a list (SEC-14).
2. **Unreadable is nonexistent.** Not-available pages, trails and back crumbs are identical
   for a missing and an unreadable resource; a page never echoes the requested id.
3. **D-8:** account names appear only where the API returns them; no substitute or
   inferred account.
4. **No side-channel counts.** Counts appear only as the API computes them inside the
   viewer's access (Sources, change previews).
5. **Audit privacy (D-18):** the audit view shows only what the API returns; private
   groups and unreadable documents are never named, listed or counted.
6. **Administrators ≠ readers (SEC-15):** operational views show no question, answer or
   evidence, under a boundary band that says so.
7. **Sign-in safety:** production sign-in has no organization lookup, e-mail field or
   provider button; its messages are the API's closed set; `next` follows the API's
   same-origin rule (`lib/next-path.ts`); the development picker exists only where the
   server enables it.
8. **No client-side authorization.** Lattices, ledgers, previews and egress cells render
   the API's decisions; the interface never reconstructs who may read what, and a
   capability only hides navigation — every endpoint still enforces its own rule.
9. **The landing page makes no API request** and names no organization but the labelled
   synthetic example.

---

## 9. Anti-patterns

Lattice deliberately avoids: chat bubbles, avatars and "AI sparkle"; card soup and stat
cards; gradients, glass and glows; semantic colours used as decoration; invented data,
metrics, customers or testimonials; lattices drawn for their look; hatching as texture or
as a placeholder for hidden content; mono for sentences and tracked uppercase as a
labelling style; serif in controls; toasts as the only confirmation; client-side sorting
or filtering of one page of results; an interface that guesses permissions.

---

## 10. Working on the system

- **Add a token** to the semantic layer (both maps) and, if it pairs text with a ground, to
  `styles/contrast-pairs.ts`.
- **Add a surface** from primitives and the record or ops frame; add its states to the
  screen harness and its routes to `e2e/closure.spec.ts`.
- **Lint:** Stylelint (no raw colours, fonts only by role variable, no raw px spacing in
  rebuilt directories), ESLint with `jsx-a11y`.
- **Screens:** `pnpm screens` renders every screen and state at 1440 / 1280 / 1024 / 768 /
  390 into `e2e/screens/` (git-ignored) as real tall windows — no stitched captures —
  with reduced motion, no focus and a hidden caret. `pnpm screens:canonical` writes the
  committed reference set to [`docs/screens/`](screens/README.md).
