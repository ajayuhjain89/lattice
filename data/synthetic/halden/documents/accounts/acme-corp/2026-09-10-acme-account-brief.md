---
id: acme-account-brief
title: Acme Corp — account brief
author: Maya Okafor
created: 2025-01-09T10:00:00Z
updated: 2026-09-10T15:45:00Z
account: acme-corp
authority: working
---
Living brief for anyone working with Acme Corp. Last reviewed by Maya Okafor,
10 September 2026.

## Snapshot

- Industrial manufacturer (hydraulic components), headquartered in Columbus, Ohio;
  about 4,300 employees across six plants.
- Halden customer since 15 December 2024. Enterprise plan, 1,850 seats.
- ARR $186,400. Current term ends **15 December 2026**.
- Not to be confused with Acme Health (a different customer, clinics network).

## People

| Name | Role | Notes |
| ---- | ---- | ----- |
| Derek Holt | Director, IT Operations | Day-to-day owner; files most tickets |
| Priscilla Vance | CIO | Executive sponsor; joins QBRs twice a year |
| Wen Zhao | IAM engineer | Built the Okta integration |
| Ana Ruiz | Procurement lead | Runs renewal paperwork |

Halden team: Maya Okafor (AM), Rachel Kim (CSM), Marcus Feld (SE). Executive sponsor:
Grace Whitfield.

## How they use Halden Flow

ERP-to-warehouse inventory sync, supplier invoice routing, plant KPI exports. Audit log
export to S3 feeds their SIEM. SSO moved from Azure AD to Okta on 1 August 2026.

## Open topics

- **Directory Sync (SCIM).** Acme needs automated deprovisioning before their SOX
  fieldwork. At the June kickoff we said Directory Sync was planned for Q3 and would be
  included in their Enterprise plan. On 14 August Support told Derek it was expected in
  October (HAL-4821). I am confirming current timing with Product.
- **Interim control.** Acme runs our Admin API bulk-deactivation script nightly.
- **Renewal.** See the renewal plan. Risk: SCIM timing.

## History

- Dec 2024 — go-live on Azure AD SSO, 1,200 users.
- Mar 2026 — audit log export delivered (release 2026.03).
- Jun 2026 — Okta migration kickoff.
- Aug 2026 — Okta cutover; deprovisioning gap raised in HAL-4821.
