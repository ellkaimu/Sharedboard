"""Tests for the per-board Room (pycrdt sync/awareness)."""

from __future__ import annotations

from pycrdt import (
    Doc,
    Encoder,
    XmlElement,
    XmlFragment,
    XmlText,
    create_awareness_message,
    create_sync_message,
    create_update_message,
    read_message,
)

from shareboard.sync import Room


# ----------------------------------------------------------- helpers


def _awareness_payload(client_id: int, name: str) -> bytes:
    """Raw awareness payload (no type byte, no length prefix)."""
    enc = Encoder()
    enc.write_var_uint(1)
    enc.write_var_uint(client_id)
    enc.write_var_uint(1)
    enc.write_var_string(f'{{"user":{{"name":"{name}"}}}}')
    return enc.to_bytes()


def _add_paragraph(doc: Doc, text: str) -> bytes:
    """Append a paragraph to ``doc``'s ``content`` fragment; return update bytes."""
    captured: list[bytes] = []

    def cap(event):
        captured.append(event.update)

    doc.observe(cap)
    with doc.transaction():
        frag = doc.get("content", type=XmlFragment)
        p = XmlElement(tag="paragraph", contents=[XmlText(text)])
        frag.children.append(p)
    assert captured
    return captured[-1]


def _fragment_text(frag: XmlFragment) -> str:
    """Walk a fragment and concatenate all XmlText descendants."""
    parts: list[str] = []

    def walk(node):
        if isinstance(node, XmlText):
            parts.append(str(node))
        elif isinstance(node, XmlElement):
            for child in node.children:
                walk(child)
        elif isinstance(node, XmlFragment):
            for child in node.children:
                walk(child)

    for child in frag.children:
        walk(child)
    return "".join(parts)


# ----------------------------------------------------------------- tests


def test_empty_room():
    room = Room("b1")
    assert room.board_id == "b1"
    assert isinstance(room.fragment, XmlFragment)


def test_snapshot_roundtrip_nonempty():
    src = Doc(allow_multithreading=True)
    _add_paragraph(src, "hello")

    room = Room("b1", snapshot=src.get_update(b"\x00"))
    assert _fragment_text(room.fragment) == "hello"


def test_empty_snapshot_is_skipped():
    """An empty doc's state is just ``b'\\x00'``; Room should accept it gracefully."""
    src = Doc(allow_multithreading=True)
    src.get("content", type=XmlFragment)
    empty_state = src.get_update(b"\x00")
    room = Room("b1", snapshot=empty_state)
    assert list(room.fragment.children) == []


def test_handle_sync_step1_returns_step2():
    client = Doc(allow_multithreading=True)
    client.get("content", type=XmlFragment)
    server = Room("b1")
    _add_paragraph(server.doc, "Hello from server")

    client_step1 = create_sync_message(client)
    reply = server.handle_sync(client_step1)
    assert reply is not None
    assert reply[0] == 0  # SYNC
    assert reply[1] == 1  # SYNC_STEP2


def test_handle_sync_update_propagates_content():
    server = Room("b1")
    client = Doc(allow_multithreading=True)
    client.get("content", type=XmlFragment)
    msg = create_update_message(_add_paragraph(client, "from client"))

    result = server.handle_sync(msg)
    assert result is None  # SYNC_UPDATE produces no reply
    assert _fragment_text(server.fragment) == "from client"


def test_handle_awareness():
    room = Room("b1")
    msg = create_awareness_message(_awareness_payload(12345, "alice"))
    room.handle_awareness(msg)
    assert 12345 in room.awareness.states
    assert room.awareness.states[12345]["user"]["name"] == "alice"


def test_all_awareness_wire_format():
    """``Room.all_awareness()`` returns a properly framed wire message."""
    room = Room("b1")
    msg = create_awareness_message(_awareness_payload(7, "bob"))
    room.handle_awareness(msg)
    out = room.all_awareness()
    assert out
    assert out[0] == 1  # AWARENESS type byte
    inner = read_message(out[1:])
    assert inner == _awareness_payload(7, "bob")


def test_client_disconnect_removes_state():
    room = Room("b1")
    msg = create_awareness_message(_awareness_payload(99, "bob"))
    room.handle_awareness(msg)
    assert 99 in room.awareness.states
    room.handle_client_disconnect(99)
    assert 99 not in room.awareness.states


def test_dirty_subscriber_fires_only_for_remote_updates():
    room = Room("b1")
    fired: list[int] = []
    room.subscribe_dirty(lambda: fired.append(1))

    # Local mutation: shouldn't fire.
    _add_paragraph(room.doc, "local")
    assert fired == []

    # Remote update: should fire.
    client = Doc(allow_multithreading=True)
    client.get("content", type=XmlFragment)
    msg = create_update_message(_add_paragraph(client, "remote"))
    room.handle_sync(msg)
    assert fired, "dirty subscriber should fire on remote-origin updates"


def test_persistence_round_trip():
    a = Room("b1")
    _add_paragraph(a.doc, "survives")
    snapshot = a.snapshot()

    b = Room("b1", snapshot=snapshot)
    assert _fragment_text(b.fragment) == "survives"


def test_concurrent_edits_merge():
    """Two clients' updates apply without one clobbering the other."""
    server = Room("b1")
    c1 = Doc(allow_multithreading=True)
    c1.get("content", type=XmlFragment)
    c2 = Doc(allow_multithreading=True)
    c2.get("content", type=XmlFragment)

    u1 = _add_paragraph(c1, "first")
    u2 = _add_paragraph(c2, "second")

    server.handle_sync(create_update_message(u1))
    server.handle_sync(create_update_message(u2))

    text = _fragment_text(server.fragment)
    assert "first" in text
    assert "second" in text
