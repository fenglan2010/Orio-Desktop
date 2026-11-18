"""SQLAlchemy engine and session helpers.

SQLite is the default local path. PostgreSQL is selected by DATABASE_URL
(postgresql+psycopg:// or postgresql+psycopg2://). The URL, including any
password, is never logged or returned by the API.
"""

from __future__ import annotations

from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def redact_url(url: str | None) -> str:
    """Replace a password in a URL (database, Redis, broker) with ***. Safe to print."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-database-url>"
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, _, host = netloc.rpartition("@")
        if ":" in userinfo:
            user, _, _password = userinfo.partition(":")
            netloc = f"{user}:***@{host}"
        else:
            netloc = f"{userinfo}@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


redact_database_url = redact_url


def normalize_database_url(url: str | None) -> str:
    """Accept postgres:// and postgresql:// and prefer the psycopg 3 driver."""
    value = (url or "").strip()
    if not value:
        return value
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


def is_sqlite_url(url: str | None) -> bool:
    value = (url or "").strip().lower()
    return value.startswith("sqlite:")


def is_postgres_url(url: str | None) -> bool:
    value = (url or "").strip().lower()
    return value.startswith("postgres:") or value.startswith("postgresql")


def current_database_url() -> str:
    return normalize_database_url(get_settings().database_url)


def psycopg_conninfo(url: str | None = None) -> str:
    """SQLAlchemy URL → libpq / psycopg connection string (no +driver)."""
    raw = normalize_database_url(url or current_database_url())
    u = make_url(raw)
    backend = u.get_backend_name()
    if backend not in {"postgresql", "postgres"}:
        raise ValueError("psycopg_conninfo is only for PostgreSQL URLs")
    # Recreate without the SQLAlchemy driver suffix.
    query = dict(u.query) if u.query else {}
    return u.set(drivername="postgresql", query=query).render_as_string(hide_password=False)


def get_engine(url: str | None = None):
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        db_url = normalize_database_url(url or settings.database_url)
        kwargs: dict = {"future": True}
        if is_sqlite_url(db_url):
            kwargs["connect_args"] = {"check_same_thread": False}
        elif is_postgres_url(db_url):
            kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
        _engine = create_engine(db_url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_session_factory():
    get_engine()
    return _SessionLocal


def SessionLocal() -> Session:
    return get_session_factory()()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _column_names(conn, table: str) -> set[str]:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return {row[1] for row in rows}
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    ).fetchall()
    return {row[0] for row in rows}


def migrate_schema() -> None:
    """Add columns that create_all will not attach to an existing table."""
    engine = get_engine()
    with engine.begin() as conn:
        names = _column_names(conn, "users")
        if names and "disabled" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT FALSE"))
        if names and "disabled_at" not in names:
            conn.execute(text("ALTER TABLE users ADD COLUMN disabled_at TIMESTAMP"))
        audit_names = _column_names(conn, "audit_logs")
        if audit_names and "hotel_id" not in audit_names:
            conn.execute(text("ALTER TABLE audit_logs ADD COLUMN hotel_id INTEGER"))
        mem_names = _column_names(conn, "memory_records")
        if mem_names:
            additions = [
                ("run_id", "INTEGER"),
                ("strategy_id", "INTEGER"),
                ("checkpoint_id", "VARCHAR(64) DEFAULT ''"),
                ("approval_status", "VARCHAR(32) DEFAULT ''"),
                ("action_kind", "VARCHAR(32) DEFAULT ''"),
                ("risk_level", "VARCHAR(16) DEFAULT ''"),
                ("episode_key", "VARCHAR(128)"),
            ]
            for col, typ in additions:
                if col not in mem_names:
                    conn.execute(text(f"ALTER TABLE memory_records ADD COLUMN {col} {typ}"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_episode_key "
                    "ON memory_records (episode_key) "
                    "WHERE episode_key IS NOT NULL AND episode_key <> ''"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_memory_records_hotel_id_id "
                    "ON memory_records (hotel_id, id)"
                )
            )
        if engine.dialect.name == "postgresql":
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS langgraph"))


def init_db() -> None:
    from app import models  # noqa: F401

    get_engine()
    Base.metadata.create_all(bind=_engine)
    migrate_schema()


def reset_engine() -> None:
    """Used by tests to swap database URLs. Also drops the LangGraph checkpointer."""
    global _engine, _SessionLocal
    try:
        from app.services.checkpointer import reset_checkpointer
        from app.services.workflow import reset_langgraph

        reset_langgraph()
        reset_checkpointer()
    except Exception:
        # Import cycles or a partial test teardown should still drop the engine.
        pass
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
