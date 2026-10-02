"""Access management (Phase 9): administrators review and change who may read what.

* ``directory`` -- read models: the member directory, a member's access, the
  assignable directory, documents the administrator can read, group display rules.
* ``preview`` -- what a proposed change would do: effects as rules, and document
  counts only over documents the administrator can read (security-model §10).
* ``changes`` -- the writes, each in one transaction with its ``audit_events`` row.
* ``audit`` -- the audit view, re-authorized for the viewing administrator.

Authorization decisions stay in ``lattice.authz``; this package only reads and writes
access *state* (an explicit allowance in ``tests/test_authz_boundary.py``) and asks
``lattice.authz`` who may read what. Being an administrator grants no data access: every
document shown here is one the viewing administrator can read.
"""
