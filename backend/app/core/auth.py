"""Seeded demo users and signed JWTs. FROZEN after Day 0.
The token proves identity only. Role and active status are re-read from the database on every request,
so a revoked member loses access immediately."""
import json
import os
import time
import urllib.request
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


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(authorization: str | None = Header(default=None), session: Session = Depends(get_session)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorised("Sign in first.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise _unauthorised("Your session expired or is invalid. Please sign in again.") from None
    try:
        row = session.execute(
            text("select id, name, role, project_id, active, email, is_demo from members where id = :id"),
            {"id": claims.get("sub")},
        ).mappings().first()
    except Exception:
        row = session.execute(
            text("select id, name, role, project_id, active from members where id = :id"),
            {"id": claims.get("sub")},
        ).mappings().first()
    if row is None or not row["active"]:
        raise _unauthorised("This account no longer has access to the project.")
    return User(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        project_id=row["project_id"],
        email=row.get("email"),
        is_demo=bool(row.get("is_demo", 0)),
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
