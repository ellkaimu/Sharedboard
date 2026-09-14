"""Flask app factory, in-memory room registry, snapshot worker, main entry.

The ``AppCore`` is the dependency container passed to REST blueprints and
socket.io handlers. It owns:

* a ``Storage`` (SQLite board metadata + per-board snapshots)
* a ``RoomRegistry`` (in-memory cache of active ``Room`` objects, one per
  board that's currently loaded)
* a snapshot worker thread that periodically writes each dirty room's state
  to SQLite (atomic, debounced)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable, Optional

from flask import Flask
from flask_socketio import SocketIO

from .config import Config
from .storage import Storage
from .sync import Room
from .socket_handlers import register as register_socket_handlers
from .rooms import make_blueprint as make_rooms_blueprint

logger = logging.getLogger(__name__)


class RoomRegistry:
    """Manages ``Room`` instances keyed by board_id.

    Side responsibilities:
        * maps socket.io sid -> board_id (a sid is bound to at most one
          board at a time; switching boards mid-session rebinds it)
        * tracks awareness client_id -> sid so we can evict the right
          client on disconnect
    """

    def __init__(self, storage: Storage, config: Config):
        self.storage = storage
        self.config = config
        self._rooms: dict[str, Room] = {}
        self._subscribed: set[str] = set()
        self._sid_to_board: dict[str, str] = {}
        self._board_to_sids: dict[str, set[str]] = defaultdict(set)
        self._sid_to_clients: dict[str, set[int]] = defaultdict(set)
        self._dirty_subscribers: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    def add_dirty_subscriber(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            self._dirty_subscribers.append(cb)

    def _notify_dirty(self, board_id: str) -> None:
        for cb in list(self._dirty_subscribers):
            try:
                cb(board_id)
            except Exception:
                logger.exception("dirty subscriber raised for board %s", board_id)

    # --------------------------------------------------------- room lifecycle

    def ensure(self, board_id: str) -> Room:
        with self._lock:
            room = self._rooms.get(board_id)
            if room is not None:
                return room
            snapshot = self.storage.load_snapshot(board_id)
            room = Room(board_id, snapshot=snapshot)
            self._rooms[board_id] = room
            if board_id not in self._subscribed:
                room.subscribe_dirty(lambda b=board_id: self._notify_dirty(b))
                self._subscribed.add(board_id)
            logger.info(
                "loaded board %s (snapshot=%s bytes)",
                board_id,
                "no" if snapshot is None else len(snapshot),
            )
            return room

    def get(self, board_id: str) -> Optional[Room]:
        with self._lock:
            return self._rooms.get(board_id)

    def drop(self, board_id: str) -> None:
        with self._lock:
            self._rooms.pop(board_id, None)

    def all_rooms(self) -> list[Room]:
        with self._lock:
            return list(self._rooms.values())

    # ----------------------------------------------------------- sid bookkeeping

    def bind_sid(self, sid: str, board_id: str) -> None:
        with self._lock:
            prev = self._sid_to_board.get(sid)
            if prev == board_id:
                return
            if prev is not None:
                self._board_to_sids[prev].discard(sid)
            self._sid_to_board[sid] = board_id
            self._board_to_sids[board_id].add(sid)

    def unbind_sid(self, sid: str) -> Optional[str]:
        with self._lock:
            board_id = self._sid_to_board.pop(sid, None)
            if board_id is not None:
                self._board_to_sids[board_id].discard(sid)
            clients = list(self._sid_to_clients.pop(sid, ()))
        return board_id, clients

    def board_for_sid(self, sid: str) -> Optional[str]:
        with self._lock:
            return self._sid_to_board.get(sid)

    def rooms_for_sid(self, sid: str) -> list[str]:
        with self._lock:
            b = self._sid_to_board.get(sid)
            return [b] if b else []

    def remember_client(self, sid: str, client_ids: list[int]) -> None:
        if not client_ids:
            return
        with self._lock:
            self._sid_to_clients[sid].update(client_ids)


class AppCore:
    def __init__(self, config: Config):
        self.config = config
        self.storage = Storage(config.db_path)
        self.rooms = RoomRegistry(self.storage, config)
        self.socketio: Optional[SocketIO] = None  # set in create_app

    def ensure_room(self, board_id: str) -> Room:
        return self.rooms.ensure(board_id)

    def drop_room(self, board_id: str) -> None:
        self.rooms.drop(board_id)


# ---------------------------------------------------------------- snapshot worker


def _snapshot_worker(core: AppCore, stop: threading.Event) -> None:
    """Periodic persistence: every N seconds, flush every dirty room."""
    cfg = core.config
    last_flush: dict[str, float] = {}
    pending_updates: dict[str, int] = {}

    def on_dirty(board_id: str) -> None:
        pending_updates[board_id] = pending_updates.get(board_id, 0) + 1

    core.rooms.add_dirty_subscriber(on_dirty)

    while not stop.wait(cfg.snapshot_every_seconds):
        now = time.time()
        for room in core.rooms.all_rooms():
            bid = room.board_id
            updates = pending_updates.pop(bid, 0)
            if updates == 0 and last_flush.get(bid, 0) > now - cfg.snapshot_every_seconds:
                continue
            threshold_updates = updates >= cfg.snapshot_every_updates
            threshold_time = last_flush.get(bid, 0) <= now - cfg.snapshot_every_seconds
            if not (threshold_updates or (threshold_time and updates > 0)):
                continue
            try:
                state = room.snapshot()
                if state:
                    core.storage.save_snapshot(bid, state)
                else:
                    core.storage.touch(bid)
                last_flush[bid] = now
            except Exception:
                logger.exception("snapshot flush failed for board %s", bid)


# -------------------------------------------------------------------- factory


def create_app(config: Optional[Config] = None) -> tuple[Flask, AppCore, SocketIO]:
    cfg = config or Config.from_env()
    logging.basicConfig(
        level=logging.DEBUG if cfg.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Flask(__name__)
    app.config["SECRET_KEY"] = cfg.secret_key
    app.config["JSON_SORT_KEYS"] = False

    core = AppCore(cfg)

    socketio = SocketIO(
        app,
        cors_allowed_origins=cfg.cors_origins,
        async_mode="threading",
        logger=False,
        engineio_logger=False,
        max_http_buffer_size=8 * 1024 * 1024,  # 8 MiB ceiling per update
    )
    core.socketio = socketio

    app.register_blueprint(make_rooms_blueprint(core))
    register_socket_handlers(core, socketio)

    # Bootstrap any pre-existing boards so the in-memory cache matches
    # what's on disk (mostly relevant for boards a returning user visits
    # first).
    for b in core.storage.list_boards():
        core.ensure_room(b.id)

    stop_event = threading.Event()
    worker = threading.Thread(
        target=_snapshot_worker,
        args=(core, stop_event),
        name="shareboard-snapshot",
        daemon=True,
    )
    worker.start()

    # Attach stop handle to the app for tests (clean shutdown).
    app.config["_shareboard_stop_event"] = stop_event  # type: ignore[attr-defined]

    return app, core, socketio


def main() -> None:
    cfg = Config.from_env()
    app, _, socketio = create_app(cfg)
    logger.info(
        "ShareBoard v2 starting on http://%s:%d (db=%s)",
        cfg.host,
        cfg.port,
        cfg.db_path,
    )
    # ``allow_unsafe_werkzeug`` is needed only with the dev server, which is
    # what we use here. For production, front with gunicorn + an
    # eventlet/gevent worker (see README).
    socketio.run(
        app,
        host=cfg.host,
        port=cfg.port,
        debug=cfg.debug,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
