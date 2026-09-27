---
id: directory-sync-rewrite-rfc
title: "RFC: Rewrite the directory sync engine for SCIM provisioning"
author: Kofi Mensah
created: 2026-09-08T10:00:00Z
updated: 2026-09-11T15:20:00Z
departments: [engineering, product]
supersedes: scim-provisioning-worker-design
authority: working
---
Status: accepted 11 September 2026. Reviewers: Sofia Marchetti, Wei Chen, Tom Lindqvist,
Sam Patel. Supersedes "Design — SCIM provisioning on the sync engine (v1)" (May 2026).

## Problem

Load tests in late August and early September showed the v1 design cannot meet Okta's
traffic pattern:

- Okta sends group changes as PATCH operations of up to 1,000 members. v1 turns each
  into a full reconciliation job, so a 20,000-user tenant reached a **p95 provisioning
  lag of 38 minutes** (target: under 2 minutes).
- The shared job queue does not order a user's create and group-add, so 0.4% of test
  users ended up in the wrong groups.
- Deprovisioning waits behind data-flow jobs during business hours.

Deprovisioning latency is exactly what customers such as Acme Corp buy SCIM for, so v1
cannot ship.

## Proposal

A dedicated provisioning service:

- An append-only event log per workspace; SCIM requests are applied as events in order.
- Incremental membership updates instead of recomputing groups.
- Its own worker pool, isolated from data-flow jobs.
- Idempotency keys on every SCIM request.

## Plan

Eight weeks for Kofi, Wei and one engineer from Platform: service ready for beta around
6 November, hardening through November, GA in the December release if beta exit criteria
are met.

## Alternatives considered

Tuning v1 (bigger batches, priority queue) — halves the lag but does not fix ordering.
Buying a provisioning component — out of scope for this RFC.

Document control: LATTICE-CANARY-C08-2f7c95
