---
id: acme-qbr-2026-03
title: Acme Corp — quarterly business review, March 2026
author: Rachel Kim
created: 2026-03-24T18:10:00Z
updated: 2026-03-25T08:30:00Z
account: acme-corp
authority: working
---
Attendees — Acme: Derek Holt (Director, IT Operations), Wen Zhao (IAM engineer).
Halden: Maya Okafor, Rachel Kim.

## Usage

- 1,640 active users (up from 1,210 at go-live). Finance is now the heaviest user.
- 312 active flows; the supplier invoice routing flow alone ran 41,000 times last quarter.
- Two P2 tickets this quarter, both resolved within SLA.

## Delivered since the last review

- Audit log export to S3 shipped in release 2026.03, as we told them in December. Wen has
  it feeding their SIEM already.
- Flow run history retention raised to 400 days for Enterprise.

## Acme's plans

- Acme is replacing Azure AD with **Okta Workforce Identity** this summer. Derek expects
  the cutover in late July or early August.
- With Okta they want automated provisioning and deprovisioning. Derek asked whether
  Halden will support SCIM. Maya said Directory Sync is on our H2 roadmap and that she
  would share timing once Product publishes it. No date was given.

## Renewal

Current term ends 15 December 2026. Derek expects a straightforward renewal "if the Okta
move goes smoothly". Next QBR: June.
