"""Imports: uploaded exports and where they live (phase-12-plan C8; P12.6).

``ImportStore`` is the storage contract (``lattice.imports.store``): the API streams an
upload into it, it validates and extracts the archive entry by entry
(``lattice.imports.archive``), and the worker reads the extracted tree by the import's
opaque ID. Locations are derived from the organization and import UUIDs only; no file
name, client path or tenant is ever read from an upload.
"""
