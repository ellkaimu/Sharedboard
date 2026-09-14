# ShareBoard v2.0.0

First public release of the rewritten collaborative editor on top of
[pycrdt](https://github.com/y-crt/pycrdt) (Yjs in Python) — a Google-Docs
style real-time editor.

## What's in this release

### Highlights

- **Multi-board.** Each board has its own shareable URL. The URL is the
  only secret — no auth, no accounts. Create as many boards as you like
  from the home page.
- **Real-time CRDT sync.** Concurrent edits from any number of clients
  merge correctly even if peers go offline and come back later. Backed
  by the same Yjs document model used by Evernote, Notion, and
  Affine.
- **Rich text.** Paragraphs, headings (H1/H2), bold, italic, inline
  code, plus undo/redo per user.
- **Presence.** Every connected user shows up as a colored dot in the
  topbar with a remote cursor of the same color in the document.
- **Persistence.** SQLite, atomic snapshots, survives server restarts
  without data loss.

### Tech

- Backend: **Flask + Flask-SocketIO + pycrdt**
- Frontend: **ProseMirror + y-prosemirror + Yjs** (loaded via importmap
  from `esm.sh`, no build step)
- Wire protocol: standard **y-websocket / y-protocols** format
- Storage: SQLite (stdlib `sqlite3`) with WAL mode
- Tests: 36 pytest cases covering storage, sync handshake, REST, and
  socket end-to-end

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m shareboard            # http://localhost:8888
```

Or build the Debian package:

```bash
./build_deb.sh
sudo apt install ../shareboard_2.0.0-1_all.deb
sudo pip install --break-system-packages pycrdt   # pycrdt has no Debian pkg yet
sudo systemctl enable --now shareboard
```

### Configuration

All settings are env vars — see the README table. The defaults work out
of the box; the only one worth setting for production is
`SHAREBOARD_SECRET` (a stable Flask session secret).

### Breaking changes vs. v1

The original v1 (`app.py` + `index.html` + flat-file backups) is
**not** compatible with v2. Schema, storage backend, and wire protocol
all changed. Boards from v1 cannot be migrated — start fresh.

### Known limitations

- Single-process in-memory room map. Horizontal scaling needs a
  pub/sub bridge between nodes.
- ~50 concurrent editors per board per process is the comfortable
  ceiling under the threading async mode.
- No rich-text schema beyond basic (no images, no tables, no code
  blocks in the toolbar — text only for now).
- No authentication. Anyone with a board's URL can edit.

See `README.md` for the full architecture notes.
