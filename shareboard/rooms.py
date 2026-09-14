"""REST API for board management.

Endpoints:
    GET    /api/boards                  -> list boards
    POST   /api/boards                  -> create a board, returns the new board
    GET    /api/boards/<id>             -> single board (metadata only)
    PATCH  /api/boards/<id>             -> rename
    DELETE /api/boards/<id>             -> delete (in-memory room is dropped lazily)
    GET    /                            -> renders board switcher
    GET    /b/<id>                      -> renders editor
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, abort, jsonify, render_template, request

from .storage import Board

if TYPE_CHECKING:
    from .app import AppCore

logger = logging.getLogger(__name__)


def make_blueprint(core: "AppCore") -> Blueprint:
    bp = Blueprint("shareboard", __name__)

    def _serialize(b: Board) -> dict:
        return {
            "id": b.id,
            "name": b.name,
            "created_at": b.created_at,
            "updated_at": b.updated_at,
            "has_snapshot": b.has_snapshot,
        }

    @bp.get("/")
    def index():
        return render_template("index.html")

    @bp.get("/b/<board_id>")
    def board_page(board_id: str):
        b = core.storage.get_board(board_id)
        if b is None:
            abort(404)
        return render_template("board.html", board=b)

    @bp.get("/api/boards")
    def list_boards():
        return jsonify([_serialize(b) for b in core.storage.list_boards()])

    @bp.post("/api/boards")
    def create_board():
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            name = "Untitled"
        if len(name) > 200:
            name = name[:200]
        board = core.storage.create_board(name)
        core.ensure_room(board.id)  # eagerly create so the first visitor is fast
        return jsonify(_serialize(board)), 201

    @bp.get("/api/boards/<board_id>")
    def get_board(board_id: str):
        b = core.storage.get_board(board_id)
        if b is None:
            abort(404)
        return jsonify(_serialize(b))

    @bp.patch("/api/boards/<board_id>")
    def rename_board(board_id: str):
        b = core.storage.get_board(board_id)
        if b is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        core.storage.rename_board(board_id, name[:200])
        return jsonify(_serialize(core.storage.get_board(board_id)))

    @bp.delete("/api/boards/<board_id>")
    def delete_board(board_id: str):
        ok = core.storage.delete_board(board_id)
        if not ok:
            abort(404)
        core.drop_room(board_id)
        return "", 204

    return bp
