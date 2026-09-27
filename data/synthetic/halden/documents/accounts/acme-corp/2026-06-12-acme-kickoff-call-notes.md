---
id: acme-kickoff-call-notes
title: Acme Corp — Okta migration kickoff call notes
author: Maya Okafor
created: 2026-06-12T16:40:00Z
updated: 2026-06-12T17:15:00Z
account: acme-corp
authority: working
---
Call on 12 June 2026, 45 minutes. Acme: Derek Holt, Wen Zhao. Halden: Maya Okafor,
Marcus Feld (SE), Rachel Kim (CSM).

## What Acme is doing

Acme moves all workforce SSO from Azure AD to Okta. Cutover for Halden Flow is planned
for **1 August**. Wen will build the Okta app from our SAML metadata; Marcus will join
their test session in the Okta preview tenant the week of 15 June.

## Provisioning and SCIM

Derek's main ask: once they are on Okta, users removed in Okta must lose Halden access
without manual steps. Their SOX auditors want evidence of timely deprovisioning before
year-end fieldwork.

Maya told them Directory Sync (SCIM 2.0 provisioning) is planned for **Q3** on the
current roadmap and that Acme would be among the first customers on it. Derek asked
whether it would cost extra; Maya said it would be included in their Enterprise plan.

Until Directory Sync is available, Marcus will send the Admin API bulk-deactivation
script as an interim control.

## Action items

- Marcus — SP metadata and test plan for the Okta preview tenant, by 16 June.
- Marcus — interim bulk-deactivation script, by 19 June.
- Maya — confirm Directory Sync timing with Product and send Derek a written summary.
- Rachel — move the June QBR into this cadence; next check-in 10 July.
