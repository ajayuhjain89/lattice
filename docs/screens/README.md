# Canonical screens

The committed reference set for [design-system.md](../design-system.md) v2: one representative
state per surface, at the widths that matter for it. Generated from the production build
with the API mocked from typed fixtures (synthetic Halden data), at a fixed clock, with
reduced motion, no focus and a hidden caret. Each image is a real window as tall as the
page, so the sticky masthead, the docked inspector and the phone tab bar sit where they
would; dialogs and sheets are captured at the device's own height.

Regenerate (after `pnpm build` in `apps/web`):

```bash
pnpm --dir apps/web screens:canonical
```

Every other screen and state, at all five widths, is in the review harness
(`pnpm --dir apps/web screens`, written to the git-ignored `apps/web/e2e/screens/`).

## Public

| Screen | Widths |
| ------ | ------ |
| Public landing page | [1440](landing@1440.jpg) · [390](landing@390.jpg) |
| Production sign-in (identity provider) | [1440](sign-in-production@1440.jpg) · [390](sign-in-production@390.jpg) |
| Production sign-in, `sso_failed` | [1440](sign-in-production-error@1440.jpg) |
| Development sign-in (the third organization is the test-only long-names manifest) | [1440](sign-in-dev@1440.jpg) |
| A workspace page that failed to render (fault state inside the shell) | [1440](error-workspace@1440.jpg) |
| An address that matches no page | [1440](not-found@1440.jpg) |

## Ask

| Screen | Widths |
| ------ | ------ |
| Ask desk: composer, scope tokens, your reach, recent questions | [1440](ask-intro@1440.jpg) · [390](ask-intro@390.jpg) |
| A brief: status line, claims, conflict, exhibits, the AI-policy boundary | [1440](ask-answered@1440.jpg) · [390](ask-answered@390.jpg) |
| A brief with the provenance lattice (claims × exhibits) | [1440](ask-many-to-many@1440.jpg) |
| Several disagreement axes | [1440](ask-conflicts@1440.jpg) |
| Partial answer: unverified claims removed | [1440](ask-partial@1440.jpg) |
| Evidence only: everything kept from the model by policy | [1440](ask-evidence-only@1440.jpg) |
| Evidence inspector: docked at 1440, a drawer at 1024 | [1440](ask-inspector@1440.jpg) · [1024](ask-inspector@1024.jpg) |

## Technical

| Screen | Widths |
| ------ | ------ |
| Trace, the asker's view | [1440](trace-asker@1440.jpg) |
| Evidence page | [1440](evidence@1440.jpg) |

## Operations

| Screen | Widths |
| ------ | ------ |
| Sources register (administrator) | [1440](sources-admin@1440.jpg) · [390](sources-admin@390.jpg) |
| Activity chronology | [1440](activity-member@1440.jpg) |

## Access

| Screen | Widths |
| ------ | ------ |
| Access: forty members | [1440](access-members@1440.jpg) · [390](access-members@390.jpg) |
| A member: the access sentence and ledger | [1440](access-member@1440.jpg) |
| Change review: Now → After, counts, reason | [1440](access-change@1440.jpg) |
| Audit ledger | [1440](access-audit@1440.jpg) |

## Trust

| Screen | Widths |
| ------ | ------ |
| AI providers: policy, egress lattice, provider ledger | [1440](providers@1440.jpg) · [390](providers@390.jpg) |
