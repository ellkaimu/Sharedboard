"""Tests for the REST API (rooms blueprint)."""

from __future__ import annotations

import pytest
from shareboard.app import create_app


@pytest.fixture
def client(config):
    app, core, _ = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, core


def test_index_renders(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert b"ShareBoard" in r.data


def test_create_and_list(client):
    c, _ = client
    r = c.post("/api/boards", json={"name": "First"})
    assert r.status_code == 201
    board = r.get_json()
    assert board["name"] == "First"
    assert "id" in board

    listed = c.get("/api/boards").get_json()
    assert any(b["id"] == board["id"] for b in listed)


def test_get_board_page(client):
    c, _ = client
    board = c.post("/api/boards", json={"name": "PageTest"}).get_json()
    r = c.get(f"/b/{board['id']}")
    assert r.status_code == 200
    assert b"PageTest" in r.data


def test_get_missing_board_returns_404(client):
    c, _ = client
    r = c.get("/b/nonexistent")
    assert r.status_code == 404


def test_rename(client):
    c, _ = client
    board = c.post("/api/boards", json={"name": "Old"}).get_json()
    r = c.patch(f"/api/boards/{board['id']}", json={"name": "New"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "New"


def test_delete(client):
    c, _ = client
    board = c.post("/api/boards", json={"name": "Del"}).get_json()
    r = c.delete(f"/api/boards/{board['id']}")
    assert r.status_code == 204
    assert c.get(f"/api/boards/{board['id']}").status_code == 404


def test_create_with_blank_name_defaults(client):
    c, _ = client
    r = c.post("/api/boards", json={"name": ""})
    assert r.status_code == 201
    assert r.get_json()["name"] == "Untitled"


def test_create_strips_overlong_name(client):
    c, _ = client
    r = c.post("/api/boards", json={"name": "x" * 500})
    assert r.status_code == 201
    assert len(r.get_json()["name"]) == 200


def test_create_eagerly_loads_room(client):
    c, core = client
    board = c.post("/api/boards", json={"name": "Eager"}).get_json()
    # Room should already be in the registry.
    assert core.rooms.get(board["id"]) is not None
