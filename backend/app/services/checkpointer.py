"""Durable LangGraph checkpointers for SQLite (local) and PostgreSQL (RDS-ready).

Checkpoints are keyed by hotel-{hotel_id}-run-{run_id}. The saver is never
MemorySaver when the database backend is available. Connection strings with
passwords are not logged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.database import (
    current_database_url,
    is_postgres_url,
    is_sqlite_url,
    normalize_database_url,
    psycopg_conninfo,
)
from sqlalchemy.engine.url import make_url

CHECKPOINT_SCHEMA = "langgraph"

_saver = None
_conn = None
_meta: dict[str, Any] = {"kind": "uninitialized", "durable": False}


def graph_thread_id(hotel_id: int, run_id: int) -> str:
    return f"hotel-{int(hotel_id)}-run-{int(run_id)}"


def parse_graph_thread_id(thread_id: str) -> tuple[int, int] | None:
    raw = (thread_id or "").strip()
    if not raw.startswith("hotel-") or "-run-" not in raw:
        return None
    try:
        hotel_part, run_part = raw[len("hotel-") :].split("-run-", 1)
        return int(hotel_part), int(run_part)
    except ValueError:
        return None


def assert_thread_belongs_to_hotel(thread_id: str, hotel_id: int) -> str:
    parsed = parse_graph_thread_id(thread_id)
    if parsed is None:
        raise PermissionError("无效的工作流线程标识")
    tid_hotel, _run_id = parsed
    if int(tid_hotel) != int(hotel_id):
        raise PermissionError("不能恢复或回滚其他酒店的工作流")
    return thread_id


def sqlite_checkpoint_path(database_url: str | None = None) -> Path:
    url = normalize_database_url(database_url or current_database_url())
    parsed = make_url(url)
    db_path = parsed.database or ""
    if not db_path or db_path == ":memory:":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / "oiro.memory.langgraph.db"
    path = Path(db_path)
    if not path.is_absolute():
        path = DATA_DIR / path
    return path.with_name(path.name + ".langgraph.db") if path.suffix else path.with_suffix(".langgraph.db")


def checkpointer_meta() -> dict[str, Any]:
    return dict(_meta)


def open_checkpointer(database_url: str | None = None):
    """Return a process-lived durable checkpointer for the current DATABASE_URL."""
    global _saver, _conn, _meta
    if _saver is not None:
        return _saver
    url = normalize_database_url(database_url or current_database_url())
    if is_postgres_url(url):
        _saver, _conn, _meta = _open_postgres(url)
    elif is_sqlite_url(url) or not url:
        _saver, _conn, _meta = _open_sqlite(url)
    else:
        raise RuntimeError("不支持的数据库后端，无法创建 LangGraph checkpointer")
    return _saver


def _open_sqlite(url: str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = sqlite_checkpoint_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    meta = {"kind": "sqlite", "durable": True, "path": str(path)}
    return saver, conn, meta


def _open_postgres(url: str):
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver

    conninfo = psycopg_conninfo(url)
    conn = psycopg.connect(conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
    conn.execute(f"SET search_path TO {CHECKPOINT_SCHEMA}, public")
    saver = PostgresSaver(conn)
    saver.setup()
    meta = {"kind": "postgres", "durable": True, "schema": CHECKPOINT_SCHEMA}
    return saver, conn, meta


def reset_checkpointer() -> None:
    global _saver, _conn, _meta
    conn = _conn
    _saver = None
    _conn = None
    _meta = {"kind": "uninitialized", "durable": False}
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
