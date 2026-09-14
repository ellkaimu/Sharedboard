# Changelog

All notable changes to ShareBoard are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2024-09-14

### Changed — full rewrite

The original v1 (`app.py` + `index.html` + flat-file backups) has been
replaced. Schema, storage backend, and wire protocol all changed.
Boards from v1 cannot be migrated.

### Added

- Multi-board: each board has its own shareable URL.
- CRDT-based real-time sync via `pycrdt` (Yjs in Python).
- Rich-text editor: paragraphs, headings, bold, italic, inline code,
  lists (ProseMirror + y-prosemirror, loaded via importmap from
  `esm.sh`, no build step).
- Presence: colored remote cursors + user names.
- Undo/redo per user.
- SQLite persistence with atomic snapshot writes (WAL mode).
- Standard y-websocket / y-protocols wire format on socket.io.
- REST API for board CRUD (`/api/boards`).
- Periodic snapshot worker thread with debounced writes.
- systemd unit with hardening directives.
- `.deb` packaging via `dpkg-buildpackage` / pybuild.
- 36 pytest cases covering storage, sync, REST, and socket e2e.

### Removed

- Single-shared-textarea model.
- Hardcoded `/var/lib/shareboard/backups` path.
- Per-keystroke broadcast of the full document text.
- Insecure `StandardOutput=append:` systemd directive.
- Bundled vendored PyPI libraries (now installed via pip).

### Fixed

- Race on connect (server overwriting client mid-edit).
- Missing awareness tracking — peer cursors now visible.
- Path/port mismatches between README, code, systemd unit, and .deb.
- `eventlet` declared but unused and missing from `requirements.txt`.

[2.0.0]: https://github.com/plae-lkm/Sharedboard/releases/tag/v2.0.0
