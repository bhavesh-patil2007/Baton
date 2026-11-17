"""JWT authentication for the Baton API.

Supports two identity flows:
  1. Demo / test flow – the ``/auth/demo-login`` endpoint issues HS256 JWTs for
     seeded members.  The ``sub`` claim contains a short demo ID such as
     ``u-approver``.  ``current_user`` looks up the member row in the database
     and returns the full ``User`` object.  No code changes are needed to the
     existing test suite.

  2. Supabase / production flow – the Supabase Auth service issues HS256 JWTs
     whose ``sub`` claim is a UUID (e.g. ``f47ac10b-58cc-4372-a567-0e02b2c3d479``).
     ``current_user`` attempts the same DB lookup; if the member row does not exist
     yet it calls ``_provision_workspace`` to atomically create an isolated project
     and seed the requesting user as the ``approver`` of that workspace, then
     returns the freshly minted ``User``.

The token proves identity only.  Role and active status are re-read from the
database on every request, so a revoked member loses access immediately.
"""
import re
import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session

DEMO_PROJECT_ID = "proj-campus-event-portal"
SEED_USERS = [
    {"id": "u-approver", "name": "Approver", "role": "approver"},
    {"id": "u-aarav", "name": "Aarav", "role": "leaver"},
    {"id": "u-meera", "name": "Meera", "role": "incoming_maintainer"},
    {"id": "u-contrib", "name": "Incoming Contributor", "role": "incoming_contributor"},
]

# Matches any RFC-4122 UUID (Supabase always uses this format for sub).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    project_id: str


def make_token(user: User) -> str:
    settings = get_settings()
    payload = {"sub": user.id, "project_id": user.project_id, "exp": int(time.time()) + settings.jwt_ttl_minutes * 60}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _provision_workspace(user_id: str, claims: dict, session: Session) -> User:
    """Create an isolated project and member record for a first-time Supabase user.

    This is idempotent: if two concurrent requests race, the second INSERT is a
    no-op (``INSERT OR IGNORE`` on SQLite / ``ON CONFLICT DO NOTHING`` on
    PostgreSQL via the idempotent pattern below).  We re-read the row after the
    attempt to handle both the create and the race case.
    """
    project_id = f"proj-{user_id}"
    # Derive a human-readable display name from the JWT claims (Supabase
    # populates ``email``, ``name``, or ``user_metadata.full_name``).
    email = claims.get("email") or claims.get("user_metadata", {}).get("email", "")
    full_name = (
        claims.get("name")
        or claims.get("user_metadata", {}).get("full_name")
        or (email.split("@")[0] if email else "")
        or "User"
    )

    # --- project (workspace) ---
    session.execute(
        text(
            "insert into projects (id, name, created_at) "
            "values (:id, :name, :created_at) "
            "on conflict (id) do nothing"
        ),
        {"id": project_id, "name": f"{full_name}'s Workspace", "created_at": _now_iso()},
    )

    # --- member with approver role (workspace owner) ---
    session.execute(
        text(
            "insert into members (id, project_id, name, role, active) "
            "values (:id, :project_id, :name, :role, 1) "
            "on conflict (id) do nothing"
        ),
        {"id": user_id, "project_id": project_id, "name": full_name, "role": "approver"},
    )
    session.commit()

    # Re-read to ensure we return the canonical persisted row (handles race).
    row = session.execute(
        text("select id, name, role, project_id from members where id = :id"),
        {"id": user_id},
    ).mappings().first()
    if row is None:
        raise _unauthorised("Workspace provisioning failed. Please retry.")
    return User(id=row["id"], name=row["name"], role=row["role"], project_id=row["project_id"])


def current_user(authorization: str | None = Header(default=None), session: Session = Depends(get_session)) -> User:
    """FastAPI dependency that validates a Bearer JWT and returns the authenticated User.

    Supports both demo HS256 tokens (issued by ``/auth/demo-login``) and
    Supabase HS256 tokens.  For Supabase users whose UUID ``sub`` is not yet in
    the ``members`` table, a new isolated workspace is provisioned automatically.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorised("Sign in first. Use the role switcher to log in as a demo user.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise _unauthorised("Your session expired or is invalid. Switch role to sign in again.") from None

    sub = claims.get("sub") or ""

    # Fast-path: member already exists in the database (covers all demo/test users
    # and returning Supabase users whose workspace has already been provisioned).
    row = session.execute(
        text("select id, name, role, project_id, active from members where id = :id"), {"id": sub}
    ).mappings().first()

    if row is not None:
        if not row["active"]:
            raise _unauthorised("This account no longer has access to the project.")
        return User(id=row["id"], name=row["name"], role=row["role"], project_id=row["project_id"])

    # Slow-path: unknown sub.  Only auto-provision for UUID-shaped subs (Supabase
    # production tokens).  Reject anything else (e.g. a tampered demo token).
    if not _UUID_RE.match(sub):
        raise _unauthorised("This account no longer has access to the project.")

    return _provision_workspace(sub, claims, session)
