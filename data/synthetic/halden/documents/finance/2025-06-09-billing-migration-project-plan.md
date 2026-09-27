---
id: billing-migration-project-plan
title: Billing system migration — project plan
author: Luis Moreno
created: 2025-06-09T10:00:00Z
updated: 2025-07-14T15:30:00Z
authority: working
---
Project: move subscription billing from the legacy invoicing tool to Chargebee.
Owner: Luis Moreno. Sponsor: Elena Petrova (CFO). Target cutover: 1 October 2025.

## Scope

- Migrate 212 active subscriptions with their billing schedules.
- Rebuild invoice templates, dunning emails and the refund workflow.
- Connect Chargebee to the general ledger and to Halden Flow usage data.

## Refund workflow

Chargebee must support the Enterprise refund rules: customers can request a **full refund
within 30 days** of the start of an annual term; after that only contractual service
credits apply. The workflow routes requests from account managers to Finance
Operations for approval.

## Milestones

| Date | Milestone |
| ---- | --------- |
| 30 Jun | Chargebee sandbox configured |
| 31 Jul | Test migration of 20 subscriptions |
| 15 Sep | Full dry run |
| 1 Oct | Cutover |

## Risks

- Mid-term seat changes on legacy invoices do not map cleanly.
- Dunning emails need legal review in three jurisdictions.

## Status, 14 July 2025

Sandbox configured; test migration moved to 7 August.
