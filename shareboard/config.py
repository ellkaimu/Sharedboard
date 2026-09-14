"""Environment-driven configuration. All paths and ports are env-overridable."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "shareboard"


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path
    db_path: Path
    secret_key: str
    snapshot_every_updates: int
    snapshot_every_seconds: float
    cors_origins: str
    debug: bool

    @classmethod
    def from_env(cls) -> "Config":
        host = os.getenv("SHAREBOARD_HOST", "0.0.0.0")
        port = int(os.getenv("SHAREBOARD_PORT", "8888"))
        data_dir = Path(os.getenv("SHAREBOARD_DATA_DIR", str(_DEFAULT_DATA_DIR))).expanduser()
        db_path = Path(os.getenv("SHAREBOARD_DB", str(data_dir / "shareboard.db"))).expanduser()
        secret_key = os.getenv("SHAREBOARD_SECRET") or secrets.token_urlsafe(32)
        snapshot_every_updates = int(os.getenv("SHAREBOARD_SNAPSHOT_UPDATES", "20"))
        snapshot_every_seconds = float(os.getenv("SHAREBOARD_SNAPSHOT_SECONDS", "30"))
        cors_origins = os.getenv("SHAREBOARD_CORS_ORIGINS", "*")
        debug = os.getenv("SHAREBOARD_DEBUG", "0") in ("1", "true", "yes")

        data_dir.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if not os.getenv("SHAREBOARD_SECRET"):
            logger.warning(
                "SHAREBOARD_SECRET not set; generated an ephemeral key. "
                "Set it in production to persist sessions across restarts."
            )

        return cls(
            host=host,
            port=port,
            data_dir=data_dir,
            db_path=db_path,
            secret_key=secret_key,
            snapshot_every_updates=snapshot_every_updates,
            snapshot_every_seconds=snapshot_every_seconds,
            cors_origins=cors_origins,
            debug=debug,
        )
