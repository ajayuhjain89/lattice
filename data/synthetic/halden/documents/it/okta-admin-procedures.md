---
id: okta-admin-procedures
title: Halden internal Okta — admin procedures
author: Lena Brandt
created: 2025-01-15T09:00:00Z
updated: 2026-06-30T10:00:00Z
departments: [it]
authority: working
---
Procedures for Halden's own employee Okta tenant (not customer IdPs).

## Joiners

HR creates the worker in BambooHR; Okta imports it nightly and assigns birthright apps
(Google Workspace, Slack, Zoom, Halden's internal tools) by department.

## Leavers

Deactivating a worker in BambooHR deactivates Okta within 15 minutes and signs the user
out of every app. IT checks the leaver report every morning.

## Privileged access

- Okta super-admins: Lena Brandt and one backup in Platform engineering.
- Admin roles are reviewed quarterly with the access review.

## MFA

Okta Verify with number matching for everyone; FIDO2 keys for admins.
