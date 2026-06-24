"""Tests for compression-chain transcript reassembly (app/session_chain.py)."""

import json
import sqlite3

from app.session_chain import _CONTENT_JSON_PREFIX, chain_transcript


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, started_at REAL);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, role TEXT, content TEXT, active INTEGER DEFAULT 1
        );
        """
    )
    return conn


def _add_msg(conn, sid, role, content):
    conn.execute(
        "INSERT INTO messages (session_id, role, content, active) VALUES (?,?,?,1)",
        (sid, role, content),
    )


def test_stitches_chain_root_to_tip(tmp_path):
    # Layout: hermes_root/profiles/<profile>/state.db
    db_dir = tmp_path / "profiles" / "personal"
    db_dir.mkdir(parents=True)
    conn = _make_db(db_dir / "state.db")
    # root -> child (compression continuation)
    conn.execute("INSERT INTO sessions VALUES ('root', NULL, 1.0)")
    conn.execute("INSERT INTO sessions VALUES ('child', 'root', 2.0)")
    _add_msg(conn, "root", "user", "first question")
    _add_msg(conn, "root", "assistant", "first answer")
    _add_msg(conn, "child", "user", "second question")
    _add_msg(conn, "child", "assistant", "second answer")
    conn.commit()
    conn.close()

    # Resolving from EITHER end returns the whole chain in order.
    for start in ("root", "child"):
        out = chain_transcript(str(tmp_path), "personal", start)
        assert out == [
            ("user", "first question"),
            ("assistant", "first answer"),
            ("user", "second question"),
            ("assistant", "second answer"),
        ], start


def test_decodes_json_parts_and_skips_empty_and_tools(tmp_path):
    db_dir = tmp_path / "profiles" / "p"
    db_dir.mkdir(parents=True)
    conn = _make_db(db_dir / "state.db")
    conn.execute("INSERT INTO sessions VALUES ('s', NULL, 1.0)")
    parts = _CONTENT_JSON_PREFIX + json.dumps(
        [{"type": "text", "text": "hello"}, {"type": "tool_use", "name": "x"}]
    )
    _add_msg(conn, "s", "assistant", parts)        # JSON parts → "hello"
    _add_msg(conn, "s", "assistant", "")           # empty → skipped
    _add_msg(conn, "s", "tool", "tool output")     # non user/assistant → skipped
    conn.commit()
    conn.close()

    assert chain_transcript(str(tmp_path), "p", "s") == [("assistant", "hello")]


def test_unknown_session_returns_none_for_fallback(tmp_path):
    db_dir = tmp_path / "profiles" / "p"
    db_dir.mkdir(parents=True)
    _make_db(db_dir / "state.db").close()
    # Not in DB → None so the caller falls back to the API.
    assert chain_transcript(str(tmp_path), "p", "missing") is None


def test_missing_db_returns_none(tmp_path):
    assert chain_transcript(str(tmp_path), "nope", "x") is None
