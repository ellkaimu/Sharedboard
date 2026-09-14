"""End-to-end socket tests using the Flask-SocketIO test client."""

from __future__ import annotations

import time

import pytest
from pycrdt import (
    Doc,
    Encoder,
    XmlElement,
    XmlFragment,
    XmlText,
    create_awareness_message,
    create_update_message,
)

from shareboard.app import create_app


@pytest.fixture
def server(config):
    app, core, socketio = create_app(config)
    return app, core, socketio


def _doc_with_paragraph(text: str) -> tuple[Doc, bytes]:
    """Build a tiny doc with one paragraph; return (doc, captured update)."""
    doc = Doc(allow_multithreading=True)
    captured: list[bytes] = []

    def on_upd(event):
        captured.append(event.update)

    doc.observe(on_upd)
    with doc.transaction():
        p = XmlElement(tag="paragraph", contents=[XmlText(text)])
        doc.get("content", type=XmlFragment).children.append(p)
    return doc, (captured[-1] if captured else b"")


def _awareness_message(client_id: int, name: str) -> bytes:
    enc = Encoder()
    enc.write_var_uint(1)
    enc.write_var_uint(client_id)
    enc.write_var_uint(1)
    enc.write_var_string(f'{{"user":{{"name":"{name}"}}}}')
    return create_awareness_message(enc.to_bytes())


def test_two_clients_sync(server):
    """Client B receives the update Client A sent."""
    app, core, socketio = server
    board = core.storage.create_board("sync-test")

    c1 = socketio.test_client(app, namespace="/")
    c2 = socketio.test_client(app, namespace="/")
    assert c1.is_connected()
    assert c2.is_connected()

    c1.emit("join", {"board_id": board.id})
    c2.emit("join", {"board_id": board.id})
    c1.get_received("/")
    c2.get_received("/")

    _doc_a, upd = _doc_with_paragraph("hi from A")
    assert upd
    c1.emit("sync", create_update_message(upd))

    received = c2.get_received("/")
    sync_events = [e for e in received if e["name"] == "sync"]
    assert sync_events, "expected at least one sync event on B"
    payload = sync_events[-1]["args"][0]
    assert payload[0] == 0  # SYNC
    assert payload[1] == 2  # SYNC_UPDATE


def test_awareness_fanout(server):
    app, core, socketio = server
    board = core.storage.create_board("aware-test")
    c1 = socketio.test_client(app, namespace="/")
    c2 = socketio.test_client(app, namespace="/")
    c1.emit("join", {"board_id": board.id})
    c2.emit("join", {"board_id": board.id})
    c1.get_received("/")
    c2.get_received("/")

    msg = _awareness_message(4242, "Alice")
    c1.emit("awareness", msg)
    received = c2.get_received("/")
    awareness_events = [e for e in received if e["name"] == "awareness"]
    assert awareness_events
    payload = awareness_events[-1]["args"][0]
    assert payload[0] == 1  # AWARENESS


def test_initial_sync_message_on_join(server):
    """Joining a board with existing content delivers a SYNC_STEP1 to the new client."""
    app, core, socketio = server
    board = core.storage.create_board("init-test")
    room = core.ensure_room(board.id)
    _doc_with_paragraph_into(room.doc, "preloaded")
    core.storage.save_snapshot(board.id, room.snapshot())

    c = socketio.test_client(app, namespace="/")
    c.emit("join", {"board_id": board.id})
    received = c.get_received("/")
    sync_events = [e for e in received if e["name"] == "sync"]
    assert sync_events
    payload = sync_events[0]["args"][0]
    assert payload[0] == 0  # SYNC
    assert payload[1] == 0  # SYNC_STEP1


def _doc_with_paragraph_into(doc, text: str) -> None:
    with doc.transaction():
        p = XmlElement(tag="paragraph", contents=[XmlText(text)])
        doc.get("content", type=XmlFragment).children.append(p)


def test_join_with_unknown_board_rejected(server):
    """Joining without board_id is silently rejected."""
    app, _, socketio = server
    c = socketio.test_client(app, namespace="/")
    c.emit("join", {})
    c.emit("join", {"board_id": 12345})  # wrong type
    received = c.get_received("/")
    assert all(e["name"] != "sync" for e in received)


def test_disconnect_clears_awareness(server):
    app, core, socketio = server
    board = core.storage.create_board("disco-test")
    c1 = socketio.test_client(app, namespace="/")
    c2 = socketio.test_client(app, namespace="/")
    c1.emit("join", {"board_id": board.id})
    c2.emit("join", {"board_id": board.id})
    c1.get_received("/")
    c2.get_received("/")

    msg = _awareness_message(7777, "DisconnectMe")
    c1.emit("awareness", msg)
    c2.get_received("/")

    room = core.ensure_room(board.id)
    assert 7777 in room.awareness.states

    c1.disconnect()
    time.sleep(0.2)
    assert 7777 not in room.awareness.states


def test_initial_awareness_delivered_on_join(server):
    """A new client receives a snapshot of existing awareness states."""
    app, core, socketio = server
    board = core.storage.create_board("aw-init")
    c1 = socketio.test_client(app, namespace="/")
    c1.emit("join", {"board_id": board.id})
    c1.emit("awareness", _awareness_message(111, "ExistingUser"))
    c1.get_received("/")

    c2 = socketio.test_client(app, namespace="/")
    c2.emit("join", {"board_id": board.id})
    received = c2.get_received("/")
    aw_events = [e for e in received if e["name"] == "awareness"]
    assert aw_events, "expected awareness to be delivered to the new joiner"
    payload = aw_events[0]["args"][0]
    assert payload[0] == 1  # AWARENESS
