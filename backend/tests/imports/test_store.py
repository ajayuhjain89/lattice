"""The filesystem import store (P12.6; phase-12-plan C8.1): locations come from UUIDs
only, an organization's imports never leave its directory, a tree appears only by one
rename of a fully validated staging directory, and a failed or interrupted upload leaves
nothing usable."""

import os
import stat
import uuid
from pathlib import Path

import pytest

from lattice.imports.limits import MIB, ImportLimits, ImportRejected
from lattice.imports.store import FilesystemImportStore, ImportUnavailable

from .archives import files

DOC = (
    b"---\nid: d1\ntitle: T\nauthor: A\n"
    b"created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\nBody\n"
)
ORG = uuid.uuid4()
OTHER = uuid.uuid4()


@pytest.fixture
def store(tmp_path: Path) -> FilesystemImportStore:
    root = tmp_path / "imports"
    root.mkdir()
    return FilesystemImportStore(root=root, max_upload_bytes=2 * MIB)


def _upload(store: FilesystemImportStore, data: bytes, org: uuid.UUID = ORG) -> uuid.UUID:
    import_id = uuid.uuid4()
    writer = store.begin(org, import_id)
    for start in range(0, len(data), 1000):
        writer.append(data[start : start + 1000])
    size, digest = writer.seal()
    assert size == len(data)
    assert len(digest) == 32
    return import_id


def test_locations_are_derived_from_uuids_only(store: FilesystemImportStore) -> None:
    with pytest.raises(TypeError):
        store.begin("../../etc", uuid.uuid4())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        store.tree(ORG, "../x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="absolute"):
        FilesystemImportStore(root=Path("relative/imports"))


def test_an_upload_is_published_as_a_tree_and_its_archive_removed(
    store: FilesystemImportStore,
) -> None:
    import_id = _upload(store, files({"a.md": DOC, "x.png": b"p"}))
    with pytest.raises(ImportUnavailable):
        store.tree(ORG, import_id)  # sealed, not yet validated: not readable
    result = store.extract(
        ORG, import_id, connector_type="document_repository", fmt="zip", limits=ImportLimits()
    )
    assert (result.entries, result.ignored) == (1, 1)
    tree = store.tree(ORG, import_id)
    assert tree == store.root / str(ORG) / str(import_id) / "tree"
    assert sorted(p.name for p in tree.parent.iterdir()) == ["tree"]
    assert (tree / "a.md").read_bytes() == DOC
    for path in (store.root / str(ORG), tree.parent, tree):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o750


def test_a_refused_archive_leaves_no_tree_and_no_staging(store: FilesystemImportStore) -> None:
    import_id = _upload(store, files({"../escape.md": DOC}))
    with pytest.raises(ImportRejected) as raised:
        store.extract(
            ORG, import_id, connector_type="document_repository", fmt="zip", limits=ImportLimits()
        )
    assert raised.value.code == "unsafe_path"
    directory = store.root / str(ORG) / str(import_id)
    assert list(directory.iterdir()) == []
    with pytest.raises(ImportUnavailable):
        store.tree(ORG, import_id)
    assert not (store.root / "escape.md").exists()
    assert not (store.root.parent / "escape.md").exists()


def test_the_body_is_capped_while_it_streams(store: FilesystemImportStore) -> None:
    writer = store.begin(ORG, uuid.uuid4())
    writer.append(b"x" * (2 * MIB))
    with pytest.raises(ImportRejected) as raised:
        writer.append(b"x")
    assert raised.value.code == "too_large"
    writer.abort()


def test_an_aborted_upload_leaves_nothing(store: FilesystemImportStore) -> None:
    import_id = uuid.uuid4()
    writer = store.begin(ORG, import_id)
    writer.append(b"PK\x03\x04partial")
    writer.abort()
    assert list((store.root / str(ORG) / str(import_id)).iterdir()) == []
    store.delete(ORG, import_id)
    assert not (store.root / str(ORG) / str(import_id)).exists()


def test_an_import_id_cannot_be_registered_twice(store: FilesystemImportStore) -> None:
    import_id = uuid.uuid4()
    store.begin(ORG, import_id).abort()
    with pytest.raises(FileExistsError):
        store.begin(ORG, import_id)


def test_another_organization_cannot_reach_an_import(store: FilesystemImportStore) -> None:
    import_id = _upload(store, files({"a.md": DOC}))
    store.extract(
        ORG, import_id, connector_type="document_repository", fmt="zip", limits=ImportLimits()
    )
    with pytest.raises(ImportUnavailable):
        store.tree(OTHER, import_id)
    store.delete(OTHER, import_id)  # a no-op for another organization
    assert store.tree(ORG, import_id).is_dir()


def test_a_planted_symlink_is_never_followed(store: FilesystemImportStore, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    import_id = uuid.uuid4()
    org_dir = store.root / str(ORG)
    org_dir.mkdir()
    os.symlink(outside, org_dir / str(import_id))
    with pytest.raises(ImportUnavailable):
        store.tree(ORG, import_id)
    store.delete(ORG, import_id)
    assert (outside / "secret.md").exists()


def test_a_single_jsonl_page_is_stored_under_a_fixed_name(store: FilesystemImportStore) -> None:
    import_id = _upload(store, b'{"id": "T-1"}\n')
    store.extract(ORG, import_id, connector_type="ticketing", fmt="jsonl", limits=ImportLimits())
    assert [p.name for p in store.tree(ORG, import_id).iterdir()] == ["export.jsonl"]


def test_listing_for_the_orphan_sweep(store: FilesystemImportStore) -> None:
    first = _upload(store, b"x")
    (store.root / "not-a-uuid").mkdir()
    assert store.organizations() == [ORG]
    assert [i for i, _ in store.stored(ORG)] == [first]
    assert store.stored(OTHER) == []
