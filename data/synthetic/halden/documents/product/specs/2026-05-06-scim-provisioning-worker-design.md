---
id: scim-provisioning-worker-design
title: Design — SCIM provisioning on the sync engine (v1)
author: Kofi Mensah
created: 2026-05-06T14:00:00Z
updated: 2026-05-20T10:30:00Z
departments: [engineering, product]
authority: working
---
Status: approved 20 May 2026. Reviewers: Sofia Marchetti, Wei Chen, Tom Lindqvist.

## Goal

Ship Directory Sync (SCIM 2.0 `/Users` and `/Groups`) in Q3 by reusing the sync engine
that already runs customer data flows.

## Design

- SCIM requests are validated at the edge and turned into **sync jobs**: one job per
  request, scheduled on the existing job queue.
- The worker applies each job as a full reconciliation of the affected users against
  the workspace directory.
- Group pushes become one job per group; membership is recomputed from scratch.
- Retries and rate limiting come from the sync engine unchanged.

## Why this approach

It needs no new infrastructure, and the engine is battle-tested. Estimated effort: six
weeks for two engineers, leaving July and August for hardening.

## Risks

- Large group pushes may be slow because each push recomputes membership.
- Ordering between user and group jobs is not guaranteed by the queue.
- We have not load-tested with Okta's PATCH-heavy traffic pattern.
