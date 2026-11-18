"""Database session and schema runner. FROZEN after Day 0."""
import re
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings


@lru_cache
def _engine_for(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    return _engine_for(get_settings().database_url)


def get_session():
    with Session(get_engine()) as session:
        yield session


def _statements(sql: str) -> list[str]:
    no_comments = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def apply_schema(engine: Engine | None = None) -> list[str]:
    """Apply db/schema/*.sql in filename order. Every file is idempotent, so this is safe on every start."""
    engine = engine or get_engine()
    applied = []
    for path in sorted(get_settings().schema_dir.glob("*.sql")):
        with engine.begin() as conn:
            for statement in _statements(path.read_text(encoding="utf-8")):
                conn.exec_driver_sql(statement)
        applied.append(path.name)
    # Ensure backward compatibility for existing databases where members already existed
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("alter table members add column email text")
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("alter table members add column is_demo integer not null default 0")
    except Exception:
        pass
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "create table if not exists identity_mappings ("
                "  supabase_sub text primary key,"
                "  member_id text not null,"
                "  project_id text not null,"
                "  role text not null check (role in ('approver', 'leaver', 'incoming_maintainer', 'incoming_contributor')),"
                "  email text,"
                "  created_at text not null,"
                "  updated_at text not null"
                ")"
            )
    except Exception:
        pass
    return applied


def is_safe_identifier(name: str) -> bool:
    return re.fullmatch(r"[a-z_][a-z0-9_]*", name) is not None
