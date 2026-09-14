"""Tests for the SQLite storage layer."""

from __future__ import annotations

import threading

from shareboard.storage import Storage


def test_create_and_get(storage: Storage):
    b = storage.create_board("My Board")
    assert b.name == "My Board"
    assert b.has_snapshot is False

    fetched = storage.get_board(b.id)
    assert fetched is not None
    assert fetched.id == b.id
    assert fetched.name == "My Board"


def test_list_empty(storage: Storage):
    assert storage.list_boards() == []


def test_list_ordering(storage: Storage):
    a = storage.create_board("A")
    b = storage.create_board("B")
    c = storage.create_board("C")
    listed = storage.list_boards()
    # Most-recently-updated first.
    assert [x.id for x in listed] == [c.id, b.id, a.id]


def test_rename(storage: Storage):
    b = storage.create_board("Old")
    assert storage.rename_board(b.id, "New") is True
    assert storage.get_board(b.id).name == "New"
    # Rename missing board returns False.
    assert storage.rename_board("nonexistent", "X") is False


def test_delete(storage: Storage):
    b = storage.create_board("X")
    assert storage.delete_board(b.id) is True
    assert storage.get_board(b.id) is None
    assert storage.delete_board(b.id) is False


def test_snapshot_roundtrip(storage: Storage):
    b = storage.create_board("snap")
    assert storage.load_snapshot(b.id) is None
    state = b"hello-yjs-state"
    storage.save_snapshot(b.id, state)
    assert storage.load_snapshot(b.id) == state
    assert storage.get_board(b.id).has_snapshot is True


def test_snapshot_overwrite(storage: Storage):
    b = storage.create_board("snap")
    storage.save_snapshot(b.id, b"v1")
    storage.save_snapshot(b.id, b"v2")
    assert storage.load_snapshot(b.id) == b"v2"


def test_touch(storage: Storage):
    b = storage.create_board("t")
    storage.touch(b.id)
    assert storage.get_board(b.id).updated_at >= b.updated_at


def test_id_uniqueness(storage: Storage):
    ids = {storage.create_board(f"b{i}").id for i in range(50)}
    assert len(ids) == 50


def test_concurrent_create(storage: Storage):
    """Sanity: SQLite under concurrent inserts survives without deadlocking."""
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(20):
                storage.create_board("x")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(storage.list_boards()) == 80
