# ShareBoard

A real-time collaborative text editor in the spirit of Google Docs. Multiple
people can open the same board URL and type simultaneously; each user's cursor
and selection is visible to the others, and edits merge correctly even when
peers are offline.

Boards are CRDT documents ([Yjs][yjs] on the wire, [pycrdt][pycrdt] on the
server). The server keeps an authoritative copy in memory and periodically
snapshots it to SQLite. Snapshots are atomic and tolerate crashes.

[yjs]: https://github.com/yjs/yjs
[pycrdt]: https://github.com/y-crt/pycrdt

## Features

- Multiple boards with shareable URLs (no auth — the URL is the secret).
- Rich-text editor (paragraphs, headings, lists, bold/italic/code).
- Presence: colored remote cursors with user names.
- Undo/redo per user.
- SQLite persistence with atomic snapshot writes.
- Single-binary Python server (Flask + Flask-SocketIO + pycrdt).

## Install (from source)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m shareboard
# open http://localhost:8888
```

## Install (.deb)

The .deb vendors all Python dependencies (flask, pycrdt, ...) from PyPI at
build time, so it installs and runs on a stock **Ubuntu 22.04** with no
extra apt packages beyond `python3` — no pip, no network needed at install
time:

```bash
sudo apt install python3-pip   # build-time only, for vendoring the deps
./build_deb.sh                 # produces ../shareboard_2.0.0-1_amd64.deb
sudo apt install ../shareboard_2.0.0-1_amd64.deb
```

Installing enables and starts the systemd service on port 8888
(`http://localhost:8888`), creates the dedicated `shareboard` system user,
and installs a desktop entry plus a `/usr/bin/shareboard` launcher for
running it manually.

## Configuration (env vars)

| variable                       | default                                        |
| ------------------------------ | ---------------------------------------------- |
| `SHAREBOARD_HOST`              | `0.0.0.0`                                      |
| `SHAREBOARD_PORT`              | `8888`                                         |
| `SHAREBOARD_DATA_DIR`          | `~/.local/share/shareboard`                    |
| `SHAREBOARD_DB`                | `$SHAREBOARD_DATA_DIR/shareboard.db`           |
| `SHAREBOARD_SECRET`            | randomly generated (sessions reset on restart) |
| `SHAREBOARD_SNAPSHOT_UPDATES`  | `20`                                           |
| `SHAREBOARD_SNAPSHOT_SECONDS`  | `30`                                           |
| `SHAREBOARD_CORS_ORIGINS`      | `*`                                            |
| `SHAREBOARD_DEBUG`             | `0`                                            |

## REST API

| method | path                       | body             | returns       |
| ------ | -------------------------- | ---------------- | ------------- |
| GET    | `/api/boards`              | —                | list of boards |
| POST   | `/api/boards`              | `{"name": "..."}` | new board     |
| GET    | `/api/boards/<id>`         | —                | single board   |
| PATCH  | `/api/boards/<id>`         | `{"name": "..."}` | updated board  |
| DELETE | `/api/boards/<id>`         | —                | 204           |
| GET    | `/`                        | —                | board switcher  |
| GET    | `/b/<id>`                  | —                | editor page     |

## Wire protocol

For browsers, just open `/b/<id>`. For other clients, the socket.io
endpoint is at the default namespace; the protocol follows [y-websocket][yws]:

1. emit `join` with `{board_id: "<id>"}` (JSON).
2. exchange binary `sync` messages (varuint type, varuint subtype, length-prefixed payload).
3. exchange binary `awareness` messages for presence.

[yws]: https://github.com/yjs/y-websocket

## Tests

```bash
pip install -e ".[test]"
pytest -q
```

## Architecture notes

- **Threading model.** The server runs Flask + Flask-SocketIO in the default
  `threading` async mode (no eventlet). Each `pycrdt.Doc` is created with
  `allow_multithreading=True` and guarded by an `RLock` in its `Room`.
  Concurrency ceiling: ~50 simultaneous editors per board per process on a
  commodity box. Beyond that, switch to a gevent/eventlet worker.
- **Single process.** Multi-process fan-out is not implemented; the in-memory
  room map is per-process. For horizontal scaling you'd front this with a
  pub/sub bridge (e.g. Redis) and have each node sync its rooms through it.
- **Persistence.** Each dirty update bumps a counter. The snapshot worker
  flushes to SQLite when either (a) >= N updates since the last flush, or
  (b) >= S seconds since the last flush and at least one update arrived.

## License

MIT.
