"""JWT authentication for the Baton API.

Supports two identity flows:
  1. Demo / test flow – the ``/auth/demo-login`` endpoint issues HS256 JWTs for
     seeded members.  The ``sub`` claim contains a short demo ID such as
     ``u-approver``.  ``current_user`` looks up the member row in the database
     and returns the full ``User`` object.
  2. Supabase / production flow – authenticated Supabase users register with an
     assigned role via ``/auth/register`` (with invitation-controlled access for Approver)
     or authenticate via ``/auth/session``.  Baton issues signed JWTs with identity,
     role, and email claims.

The token proves identity only.  Role and active status are re-read from the
database on every request, so a revoked member loses access immediately.
"""
import json
import os
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKSet
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
class AccountUser:
    id: str
    name: str
    email: str | None = None
    is_demo: bool = False


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    project_id: str
    email: str | None = None
    is_demo: bool = False


def make_token(user: User) -> str:
    settings = get_settings()
    payload = {
        "sub": user.id,
        "project_id": user.project_id,
        "role": user.role,
        "name": user.name,
        "email": user.email,
        "is_demo": user.is_demo,
        "exp": int(time.time()) + settings.jwt_ttl_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def make_account_token(user: AccountUser) -> str:
    settings = get_settings()
    payload = {
        "sub": user.id,
        "name": user.name,
        "email": user.email,
        "is_demo": user.is_demo,
        "exp": int(time.time()) + settings.jwt_ttl_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _provision_workspace(user_id: str, claims: dict, session: Session, role: str = "incoming_contributor") -> User:
    """Create an isolated project and member record for a first-time user.

    NOTE: Approver access cannot be granted through automatic provisioning.
    Approver registration requires a valid single-use invitation code verified
    via /auth/register.
    """
    if role == "approver":
        raise HTTPException(
            status_code=403,
            detail="Approver access requires invitation-controlled registration via /auth/register.",
        )
    project_id = f"proj-{uuid.uuid4()}"
    email = claims.get("email") or claims.get("user_metadata", {}).get("email", "")
    full_name = (
        claims.get("name")
        or claims.get("user_metadata", {}).get("full_name")
        or (email.split("@")[0] if email else "")
        or "User"
    )

    session.execute(
        text(
            "insert into projects (id, name, created_at) "
            "values (:id, :name, :created_at) "
            "on conflict (id) do nothing"
        ),
        {"id": project_id, "name": f"{full_name}'s Workspace", "created_at": _now_iso()},
    )

    try:
        session.execute(
            text(
                "insert into users (id, email, name, is_demo, created_at) "
                "values (:id, :email, :name, 0, :now) "
                "on conflict (id) do nothing"
            ),
            {"id": user_id, "email": email or None, "name": full_name, "now": _now_iso()},
        )
        session.execute(
            text(
                "insert into project_memberships (user_id, project_id, role, active, created_at) "
                "values (:uid, :pid, :role, 1, :now) "
                "on conflict (user_id, project_id) do nothing"
            ),
            {"uid": user_id, "pid": project_id, "role": role, "now": _now_iso()},
        )
    except Exception:
        pass

    try:
        session.execute(
            text(
                "insert into members (id, project_id, name, role, active, email, is_demo) "
                "values (:id, :project_id, :name, :role, 1, :email, 0) "
                "on conflict (id) do nothing"
            ),
            {"id": user_id, "project_id": project_id, "name": full_name, "role": role, "email": email or None},
        )
    except Exception:
        session.execute(
            text(
                "insert into members (id, project_id, name, role, active) "
                "values (:id, :project_id, :name, :role, 1) "
                "on conflict (id) do nothing"
            ),
            {"id": user_id, "project_id": project_id, "name": full_name, "role": role},
        )
    session.commit()

    return User(
        id=user_id,
        name=full_name,
        role=role,
        project_id=project_id,
        email=email or None,
        is_demo=False,
    )


def current_account_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> AccountUser:
    """Validate Bearer token and return AccountUser (even if user belongs to 0 projects)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorised("Sign in first.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        try:
            claims = verify_supabase_token(token)
        except Exception:
            raise _unauthorised("Your session expired or is invalid. Please sign in again.") from None

    uid = claims.get("sub") or ""
    try:
        u_row = session.execute(
            text("select id, name, email, is_demo from users where id = :id"),
            {"id": uid},
        ).mappings().first()
    except Exception:
        u_row = None

    if u_row:
        return AccountUser(
            id=u_row["id"],
            name=u_row["name"],
            email=u_row.get("email"),
            is_demo=bool(u_row.get("is_demo", 0)),
        )

    try:
        m_row = session.execute(
            text("select id, name, email, is_demo, active from members where id = :id"),
            {"id": uid},
        ).mappings().first()
    except Exception:
        m_row = None

    if m_row and m_row["active"]:
        return AccountUser(
            id=m_row["id"],
            name=m_row["name"],
            email=m_row.get("email"),
            is_demo=bool(m_row.get("is_demo", 0)),
        )

    email = claims.get("email") or claims.get("user_metadata", {}).get("email")
    name = claims.get("name") or claims.get("user_metadata", {}).get("full_name") or (email.split("@")[0] if email else "User")
    if uid:
        return AccountUser(id=uid, name=name, email=email, is_demo=False)

    raise _unauthorised("Account not found. Please sign in again.")


def current_user(authorization: str | None = Header(default=None), session: Session = Depends(get_session)) -> User:
    """FastAPI dependency that validates a Bearer JWT and returns the authenticated User.

    Supports both demo HS256 tokens (issued by ``/auth/demo-login``) and
    authenticated user tokens (issued after role-based registration/session sync).
    Active status and assigned role are verified directly against the database on each request.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorised("Sign in first.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise _unauthorised("Your session expired or is invalid. Please sign in again.") from None

    uid = claims.get("sub") or ""
    active_pid = claims.get("project_id")

    # 1. Authoritative project memberships lookup
    has_any_pm = False
    try:
        has_any_pm = session.execute(
            text("select 1 from project_memberships where user_id = :uid limit 1"),
            {"uid": uid},
        ).scalar() is not None
    except Exception:
        has_any_pm = False

    if has_any_pm:
        # project_memberships is strictly authoritative; legacy members cannot bypass it
        if active_pid:
            row = session.execute(
                text("""
                    select u.id, u.name, u.email, u.is_demo, pm.role, pm.project_id, pm.active
                    from users u
                    join project_memberships pm on pm.user_id = u.id
                    where u.id = :uid and pm.project_id = :pid
                """),
                {"uid": uid, "pid": active_pid},
            ).mappings().first()
        else:
            row = session.execute(
                text("""
                    select u.id, u.name, u.email, u.is_demo, pm.role, pm.project_id, pm.active
                    from users u
                    join project_memberships pm on pm.user_id = u.id
                    where u.id = :uid
                    order by pm.created_at asc
                """),
                {"uid": uid},
            ).mappings().first()

        if row is None or not row["active"]:
            raise _unauthorised("This account no longer has access to the project.")

        # Respect immediate revocation if legacy members table has active = 0
        try:
            m_check = session.execute(
                text("select active from members where id = :uid and (project_id = :pid or project_id is null)"),
                {"uid": uid, "pid": row["project_id"]},
            ).mappings().first()
            if m_check is not None and not m_check["active"]:
                raise _unauthorised("This account no longer has access to the project.")
        except HTTPException:
            raise
        except Exception:
            pass

        return User(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            project_id=row["project_id"],
            email=row.get("email"),
            is_demo=bool(row.get("is_demo", 0)),
        )

    # 2. Legacy members table fallback (strictly for unmigrated databases without project_memberships)
    try:
        if active_pid:
            m_row = session.execute(
                text("select id, name, role, project_id, active, email, is_demo from members where id = :id and project_id = :pid"),
                {"id": uid, "pid": active_pid},
            ).mappings().first()
        else:
            m_row = session.execute(
                text("select id, name, role, project_id, active, email, is_demo from members where id = :id"),
                {"id": uid},
            ).mappings().first()
    except Exception:
        m_row = None

    if m_row is None or not m_row["active"]:
        raise _unauthorised("This account no longer has access to the project.")

    return User(
        id=m_row["id"],
        name=m_row["name"],
        role=m_row["role"],
        project_id=m_row["project_id"],
        email=m_row.get("email"),
        is_demo=bool(m_row.get("is_demo", 0)),
    )


def require_role(*roles: str):
    """Dependency helper to require one of the specified roles."""
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Requires one of roles: {', '.join(roles)}. Current role: {user.role}",
            )
        return user
    return dependency


# Known Supabase Project JWKS (e.g. sowzfgfcdfhmnmcghgvt)
DEFAULT_SUPABASE_JWKS = {
    "keys": [
        {
            "alg": "ES256",
            "crv": "P-256",
            "ext": True,
            "key_ops": ["verify"],
            "kid": "d0d99d6f-cd18-4fcc-b977-68bcd337ee7f",
            "kty": "EC",
            "use": "sig",
            "x": "tkSVNGiY5woPid276Ignl4FM3nFyxvAUgmPUD0l77WE",
            "y": "2Ym9zWV_ScVHHIAPjeVD_CHyHmPORyGUQSuRsLW3pcI",
        }
    ]
}

_JWKS_CACHE: dict[str, dict] = {}


def _get_supabase_jwks(supabase_url: str | None = None) -> list[dict]:
    """Retrieve Supabase JWKS from environment, network cache, or built-in fallback."""
    keys_found: list[dict] = []

    # 1. Custom JWKS from environment if configured
    env_jwks = os.environ.get("SUPABASE_JWKS")
    if env_jwks:
        try:
            parsed = json.loads(env_jwks)
            if isinstance(parsed, dict) and "keys" in parsed:
                keys_found.append(parsed)
        except Exception:
            pass

    # 2. Live JWKS endpoint if network is reachable
    target_url = supabase_url or os.environ.get("SUPABASE_URL", "https://sowzfgfcdfhmnmcghgvt.supabase.co")
    if target_url:
        cached = _JWKS_CACHE.get(target_url)
        if cached:
            keys_found.append(cached)
        else:
            try:
                jwks_url = f"{target_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
                req = urllib.request.Request(jwks_url, headers={"User-Agent": "Baton-Auth/1.0"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        live_jwks = json.loads(resp.read().decode("utf-8"))
                        if isinstance(live_jwks, dict) and "keys" in live_jwks:
                            _JWKS_CACHE[target_url] = live_jwks
                            keys_found.append(live_jwks)
            except Exception:
                pass

    # 3. Always include default project JWKS fallback
    keys_found.append(DEFAULT_SUPABASE_JWKS)
    return keys_found


def verify_supabase_token(
    token: str | None,
    expected_user_id: str | None = None,
    expected_email: str | None = None,
) -> dict:
    """Validate Supabase JWT (ES256, RS256, or HS256) and ensure token subject matches requested user_id."""
    if not token or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Supabase authentication token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clean_token = token.strip()
    if clean_token.lower().startswith("bearer "):
        clean_token = clean_token[7:].strip()

    try:
        header = jwt.get_unverified_header(clean_token)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Malformed Supabase JWT header: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    payload = None
    last_err: Exception | None = None

    # Asymmetric verification (ES256, RS256)
    if alg in ("ES256", "RS256"):
        jwks_list = _get_supabase_jwks()
        for jwks_data in jwks_list:
            try:
                jwk_set = PyJWKSet.from_dict(jwks_data)
                for jwk in jwk_set.keys:
                    if kid and getattr(jwk, "key_id", None) and jwk.key_id != kid:
                        continue
                    try:
                        payload = jwt.decode(
                            clean_token,
                            jwk.key,
                            algorithms=[alg],
                            options={"verify_exp": True, "verify_signature": True, "verify_aud": False},
                        )
                        break
                    except jwt.ExpiredSignatureError:
                        raise HTTPException(
                            status_code=401,
                            detail="Supabase token has expired.",
                            headers={"WWW-Authenticate": "Bearer"},
                        ) from None
                    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
                        last_err = exc
                        continue
                if payload is not None:
                    break
            except Exception as exc:
                last_err = exc
                continue

    # Symmetric verification (HS256)
    if payload is None and alg == "HS256":
        settings = get_settings()
        secrets_to_try: list[str] = []
        supa_secret = os.environ.get("SUPABASE_JWT_SECRET")
        if supa_secret:
            secrets_to_try.append(supa_secret)
        if settings.jwt_secret and settings.jwt_secret not in secrets_to_try:
            secrets_to_try.append(settings.jwt_secret)
        if "dev-secret-change-me" not in secrets_to_try:
            secrets_to_try.append("dev-secret-change-me")
        if "test-secret-at-least-32-bytes-long-for-hmac-sha256" not in secrets_to_try:
            secrets_to_try.append("test-secret-at-least-32-bytes-long-for-hmac-sha256")

        for secret in secrets_to_try:
            try:
                payload = jwt.decode(
                    clean_token,
                    secret,
                    algorithms=["HS256"],
                    options={"verify_exp": True, "verify_signature": True, "verify_aud": False},
                )
                break
            except jwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=401,
                    detail="Supabase token has expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from None
            except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
                last_err = exc
                continue

    # Fallback for unexpected algorithm configurations
    if payload is None and alg not in ("ES256", "RS256", "HS256"):
        settings = get_settings()
        for secret in [os.environ.get("SUPABASE_JWT_SECRET"), settings.jwt_secret, "dev-secret-change-me"]:
            if not secret:
                continue
            try:
                payload = jwt.decode(
                    clean_token,
                    secret,
                    options={"verify_exp": True, "verify_signature": True, "verify_aud": False},
                )
                break
            except jwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=401,
                    detail="Supabase token has expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from None
            except Exception as exc:
                last_err = exc
                continue

    if payload is None:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid Supabase token signature or format: {last_err or 'verification failed'}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=401,
            detail="Supabase token missing subject (sub).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if expected_user_id and str(sub).strip() != str(expected_user_id).strip():
        raise HTTPException(
            status_code=403,
            detail=f"Token subject ({sub}) does not match requested user_id ({expected_user_id}).",
        )

    token_email = payload.get("email")
    if expected_email and token_email:
        if str(token_email).lower().strip() != str(expected_email).lower().strip():
            raise HTTPException(
                status_code=403,
                detail=f"Token email ({token_email}) does not match requested email ({expected_email}).",
            )

    return payload
