"""Per-board collaboration room built on pycrdt.

Each room owns one ``pycrdt.Doc`` plus a server-side ``Awareness`` object that
tracks the awareness states of all currently-connected clients. The wire format
we emit on socket.io is exactly the y-websocket / y-protocols format, so a JS
client using ``yjs`` + ``y-prosemirror`` can speak to us through a tiny
socket.io transport shim.

We never call ``Awareness.set_local_state`` for the server itself: the
server's awareness object exists only so we can use its binary
``apply_awareness_update`` / ``encode_awareness_update`` helpers against the
states that arrive from clients.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from pycrdt import Awareness, Doc, XmlFragment, create_sync_message
from pycrdt import create_awareness_message, handle_sync_message, read_message
from pycrdt import YMessageType

logger = logging.getLogger(__name__)

REMOTE_ORIGIN = object()


def _parse_message(buf: bytes) -> tuple[int, bytes]:
    """Strip the y-protocol ``[msg_type, ...]`` prefix and return (type, payload).

    For SYNC messages the second byte is the sub-type and the payload is the
    remaining varuint-prefixed message (handled by ``handle_sync_message``).
    For AWARENESS messages the rest is the full encoded update.
    """
    if not buf:
        raise ValueError("empty y-protocol message")
    msg_type = buf[0]
    if msg_type not in (YMessageType.SYNC, YMessageType.AWARENESS):
        raise ValueError(f"unknown y-protocol message type: {msg_type}")
    return msg_type, buf[1:]


class Room:
    """One collaborative document and its associated clients."""

    def __init__(self, board_id: str, snapshot: Optional[bytes] = None):
        self.board_id = board_id
        self.room_name = self._room_name(board_id)
        # ``allow_multithreading`` so socket.io's threading worker can call
        # into the doc without deadlocking on its transaction guard.
        self.doc = Doc(allow_multithreading=True)
        # Use a Y.XmlFragment so the client (y-prosemirror) can bind a full
        # rich-text schema (paragraphs, headings, lists, marks).
        self.fragment = self.doc.get("content", type=XmlFragment)
        self.awareness = Awareness(self.doc)
        self._lock = threading.RLock()
        self._update_counter = 0
        self._dirty_subscribers: list[Callable[[], None]] = []

        if snapshot and len(snapshot) > 1:
            with self.doc.transaction():
                self.doc.apply_update(snapshot)

        self.doc.observe(self._on_doc_update)

    @staticmethod
    def _room_name(board_id: str) -> str:
        return f"board:{board_id}"

    # ------------------------------------------------------------------ subscribe

    def subscribe_dirty(self, cb: Callable[[], None]) -> None:
        """``cb`` fires after a remote-origin doc update is applied.

        Used by the snapshot worker to debounce persistence writes.
        """
        self._dirty_subscribers.append(cb)

    def _notify_dirty(self) -> None:
        for cb in self._dirty_subscribers:
            try:
                cb()
            except Exception:
                logger.exception("dirty subscriber failed for board %s", self.board_id)

    # --------------------------------------------------------------- sync protocol

    def handle_sync(self, raw: bytes) -> Optional[bytes]:
        """Apply a y-protocol sync message and return a reply if one is due."""
        msg_type, payload = _parse_message(raw)
        if msg_type != YMessageType.SYNC:
            return None
        # second byte is the sub-type; handle_sync_message expects the full
        # message starting at that byte, which is what `payload` is here.
        with self._lock, self.doc.transaction(origin=REMOTE_ORIGIN):
            return handle_sync_message(payload, self.doc)

    def initial_sync_message(self) -> bytes:
        """The sync-step-1 message a freshly-connected client should receive."""
        return create_sync_message(self.doc)

    def snapshot(self) -> bytes:
        """Return bytes suitable for SQLite persistence.

        ``get_state()`` returns only the state vector, not the full state.
        ``get_update(b"\\x00")`` returns the full diff from empty — that's
        what we need to reconstruct the document on load.
        """
        return self.doc.get_update(b"\x00")

    def all_awareness(self) -> bytes:
        """Wire-format awareness message containing every known client state.

        Returns the full ``[AWARENESS][len][payload]`` envelope ready to
        emit on socket.io; empty bytes if there's nothing to share.
        """
        client_ids = [cid for cid in self.awareness.states.keys() if cid != self.awareness.client_id]
        if not client_ids:
            return b""
        payload = self.awareness.encode_awareness_update(client_ids)
        return create_awareness_message(payload)

    # ------------------------------------------------------------ awareness handling

    def handle_awareness(self, raw: bytes) -> Optional[bytes]:
        """Apply a remote awareness update. Returns None (server is passive)."""
        msg_type, framed = _parse_message(raw)
        if msg_type != YMessageType.AWARENESS:
            return None
        # Strip the length-prefix that create_awareness_message wraps the
        # payload in; apply_awareness_update expects the raw count+entries.
        payload = read_message(framed)
        with self._lock:
            self.awareness.apply_awareness_update(payload, origin=REMOTE_ORIGIN)
        return None

    def handle_client_disconnect(self, client_id: int) -> None:
        """Broadcast a disconnect (state=null) for the given Y.Doc client_id."""
        with self._lock:
            self.awareness.remove_awareness_states([client_id], origin="disconnect")

    # --------------------------------------------------------- outbound observers

    def _on_doc_update(self, event) -> None:
        # Only fire dirty-notify when the update came from a remote client;
        # local-origin updates (initial snapshot load, tests) are not the
        # persistence trigger. We mark the apply in ``handle_sync`` with
        # ``origin=REMOTE_ORIGIN``; pycrdt hashes that into the transaction,
        # and the observer retrieves it via ``transaction.origin()``.
        if event.transaction.origin() is not None:
            self._notify_dirty()
