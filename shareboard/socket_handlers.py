"""Socket.IO event handlers bridging y-protocol sync/awareness messages.

Wire model
----------
The client first emits a JSON ``join`` event with ``{"board_id": "..."}`` to
register itself for a board. After that, binary ``sync`` and ``awareness``
events flow bidirectionally using the standard y-websocket / y-protocols
format (which pycrdt's helpers produce natively).

Disconnect handling
-------------------
We track ``sid -> {awareness_client_id, ...}``. Each time a client sends an
awareness update we extract the client_ids from it and remember them. On
disconnect we call ``Room.handle_client_disconnect(client_id)`` for each one
so the server's awareness state for that client is cleared and peers are
notified.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import request
from flask_socketio import join_room, leave_room

from pycrdt import create_update_message, read_message as _read_message

if TYPE_CHECKING:
    from .app import AppCore
    from flask_socketio import SocketIO

logger = logging.getLogger(__name__)


def _parse_awareness_client_ids(payload: bytes) -> list[int]:
    """Read the y-protocols awareness update format and return the client_ids.

    Format (see pycrdt ``Awareness.encode_awareness_update``):
        varuint length
        for each entry: varuint client_id, varuint clock, varstring state
    """
    from pycrdt import Decoder

    decoder = Decoder(payload)
    try:
        length = decoder.read_var_uint()
    except Exception:
        return []
    ids: list[int] = []
    for _ in range(length):
        try:
            cid = decoder.read_var_uint()
            decoder.read_var_uint()  # clock
            decoder.read_var_string()  # state (discarded)
            ids.append(cid)
        except Exception:
            break
    return ids


def register(core: "AppCore", socketio: "SocketIO") -> None:
    rooms = core.rooms

    @socketio.on("join", namespace="/")
    def on_join(data):
        board_id = (data or {}).get("board_id")
        if not isinstance(board_id, str):
            logger.warning("join rejected: missing board_id from sid=%s", request.sid)
            return False
        room = rooms.ensure(board_id)
        # If this sid was previously bound to a different board, evict their
        # awareness state from that old room first.
        prev_board, prev_clients = rooms.unbind_sid(request.sid) or (None, [])
        if prev_board and prev_board != board_id:
            prev_room = rooms.get(prev_board)
            if prev_room and prev_clients:
                for cid in prev_clients:
                    prev_room.handle_client_disconnect(cid)
                socketio.emit(
                    "awareness",
                    _build_disconnect_message(prev_room, prev_clients),
                    to=prev_room.room_name,
                    namespace="/",
                )
            leave_room(f"board:{prev_board}")

        rooms.bind_sid(request.sid, board_id)
        join_room(room.room_name)

        # Kick the new client with current state.
        socketio.emit("sync", room.initial_sync_message(), to=request.sid, namespace="/")
        snap = room.all_awareness()
        if snap:
            socketio.emit("awareness", snap, to=request.sid, namespace="/")

    @socketio.on("sync", namespace="/")
    def on_sync(data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            logger.warning("sync rejected: non-binary payload from sid=%s", request.sid)
            return
        buf = bytes(data)

        board_id = rooms.board_for_sid(request.sid)
        if board_id is None:
            return
        room = rooms.get(board_id)
        if room is None:
            return

        # Capture update bytes produced by this single apply.
        captured: list[bytes] = []

        def _capture(event):
            captured.append(event.update)

        sub = room.doc.observe(_capture)

        try:
            with room._lock:  # type: ignore[attr-defined]
                reply = room.handle_sync(buf)
        finally:
            try:
                room.doc.unobserve(sub)
            except Exception:
                pass

        if reply is not None:
            socketio.emit("sync", reply, to=request.sid, namespace="/")

        for update in captured:
            socketio.emit(
                "sync",
                create_update_message(update),
                to=room.room_name,
                include_self=False,
                namespace="/",
            )

    @socketio.on("awareness", namespace="/")
    def on_awareness(data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return
        buf = bytes(data)
        board_id = rooms.board_for_sid(request.sid)
        if board_id is None:
            return
        room = rooms.get(board_id)
        if room is None:
            return

        # Parse before applying so we know which client_ids this payload
        # declared. The client typically declares its own client_id; peers
        # piggyback theirs in the same payload. ``buf`` is the framed
        # ``[AWARENESS][len][count+entries]`` wire message; we strip both
        # the type byte and the length prefix before reading the entries.
        try:
            payload = _read_message(buf[1:])
            client_ids = _parse_awareness_client_ids(payload)
        except Exception:
            client_ids = []
        room.handle_awareness(buf)
        if client_ids:
            rooms.remember_client(request.sid, client_ids)
        # Forward to peers. y-prosemirror rebroadcasts periodically so a
        # brief stale state is self-healing.
        socketio.emit("awareness", buf, to=room.room_name, include_self=False, namespace="/")

    @socketio.on("disconnect", namespace="/")
    def on_disconnect():
        result = rooms.unbind_sid(request.sid)
        if result is None:
            return
        board_id, clients = result
        room = rooms.get(board_id)
        if room is None or not clients:
            return
        for cid in clients:
            room.handle_client_disconnect(cid)
        msg = _build_disconnect_message(room, clients)
        if msg:
            socketio.emit("awareness", msg, to=room.room_name, namespace="/")


def _build_disconnect_message(room, client_ids: list[int]) -> bytes:
    """Encode a wire-format awareness message that nullifies each given client_id.

    Temporarily sets the states to ``None`` so ``encode_awareness_update``
    emits ``state=null`` for each, then restores the originals.
    """
    from pycrdt import create_awareness_message

    original_states = {cid: room.awareness.states.get(cid) for cid in client_ids}
    try:
        for cid in client_ids:
            room.awareness._states[cid] = None  # type: ignore[attr-defined]
        encoded = room.awareness.encode_awareness_update(client_ids)
    finally:
        for cid, state in original_states.items():
            if state is None:
                room.awareness._states.pop(cid, None)
            else:
                room.awareness._states[cid] = state
    return create_awareness_message(encoded)
