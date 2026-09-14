"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

from shareboard.app import AppCore, Config
from shareboard.storage import Storage


@pytest.fixture
def tmp_data_dir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="shareboard-test-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def config(tmp_data_dir) -> Config:
    return Config(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_data_dir,
        db_path=tmp_data_dir / "test.db",
        secret_key="test-secret",
        snapshot_every_updates=100,
        snapshot_every_seconds=60,
        cors_origins="*",
        debug=False,
    )


@pytest.fixture
def storage(config) -> Storage:
    return Storage(config.db_path)


@pytest.fixture
def core(config) -> AppCore:
    return AppCore(config)
