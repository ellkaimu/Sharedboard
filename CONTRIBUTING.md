# Contributing

Contributions are welcome. Please open an issue first to discuss anything
beyond a small fix — the wire protocol and storage schema have
inter-dependencies that are easier to coordinate before code lands.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest -q
```

`pycrdt` needs to compile a Rust extension on first install; this takes
~2 minutes. Subsequent installs hit the wheel cache.

## Code layout

```
shareboard/
  app.py              Flask factory + RoomRegistry + snapshot worker
  config.py           env-driven config
  storage.py          SQLite + atomic snapshots
  sync.py             per-board pycrdt Room (sync + awareness)
  rooms.py            REST blueprint
  socket_handlers.py  socket.io event handlers
  templates/          Jinja2 pages
  static/             CSS + JS editor
tests/                pytest
```

The pycrdt wire format we emit matches `y-websocket` byte-for-byte, so
any standard Yjs client (Yjs, Ypy, y-prosemirror, y-codemirror.next,
y-tldraw) can connect via the socket.io transport shim in
`static/js/editor.js`.

## Pull request checklist

- [ ] `pytest -q` passes locally on Python 3.10+.
- [ ] No new pyflakes warnings (`python -m pyflakes shareboard/ tests/`).
- [ ] CHANGELOG.md updated under an "Unreleased" section if the change
      is user-visible.

## Commit message style

Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`...). One
logical change per commit.

## Code of conduct

Be kind. The project is a Google-Docs clone; we already have one of
those in the world, we don't need another workplace.
