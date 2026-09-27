---
id: acme-onboarding-plan-2024
title: Acme Corp — onboarding plan (Enterprise go-live)
author: Rachel Kim
created: 2024-11-18T15:20:00Z
updated: 2024-12-02T10:05:00Z
account: acme-corp
authority: working
---
Owner: Rachel Kim (CSM). Account manager: Maya Okafor. Solutions engineer: Marcus Feld.
Target go-live: Monday 16 December 2024 (contract term starts 15 December).

## Scope

Acme Corp is rolling Halden Flow out to its plant operations and finance teams first:
about 1,200 users at go-live, growing to roughly 1,800 over 2025. Initial flows cover
ERP-to-warehouse inventory sync, supplier invoice routing and the weekly plant KPI
export.

## Identity and access

- SSO: SAML 2.0 with Acme's **Azure AD** tenant. Acme IT (Derek Holt) owns the IdP
  configuration; Marcus will send our SP metadata by 25 Nov.
- Users are created just-in-time on first SSO login. Acme deactivates leavers by hand
  in the Halden admin console for now; they asked about automated provisioning and we
  said it is not on the 2025 roadmap.
- Two Acme admins: Derek Holt and Wen Zhao.

## Milestones

| Date | Milestone |
| ---- | --------- |
| 25 Nov | SP metadata sent, Azure AD app registered |
| 2 Dec | SSO tested with 20 pilot users |
| 9 Dec | Flow templates migrated from the pilot workspace |
| 16 Dec | Go-live for plant operations and finance |

## Risks

- Acme's change freeze runs 18 Dec – 5 Jan. Anything not live by 16 Dec slips to January.
- Derek is the only person at Acme who can change the Azure AD app. Ask for a backup.

## Update, 2 Dec

SSO pilot passed with 20 users. Go-live on track.
