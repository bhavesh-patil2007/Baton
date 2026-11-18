"""Core endpoints: GET /health, POST /auth/demo-login, GET /me, POST /demo/reset,
POST /auth/register, POST /auth/session, invitations management."""
import secrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import acl, audit
from app.core.auth import (
    DEMO_PROJECT_ID,
    User,
    current_user,
    make_token,
    record_identity_mapping,
    verify_supabase_token,
)
from app.core.db import get_session
from app.core.demo import seed_demo
from app.core.registry import reset_hooks

router = APIRouter()


class DemoLogin(BaseModel):
    role: str


class RegisterRequest(BaseModel):
    user_id: str | None = None
    email: str
    name: str
    role: str
    invitation_code: str | None = None
    project_id: str = DEMO_PROJECT_ID
    supabase_token: str | None = None


class SessionRequest(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    project_id: str = DEMO_PROJECT_ID
    supabase_token: str | None = None


class CreateInvitationRequest(BaseModel):
    role: str = "approver"
    email: str | None = None


@router.get("/health", tags=["core"])
def health():
    return {"status": "ok"}


@router.post("/auth/demo-login", tags=["core"])
def demo_login(body: DemoLogin, session: Session = Depends(get_session)):
    row = session.execute(
        text("select id, name, role, project_id from members where role = :role and project_id = :pid and active = 1"),
        {"role": body.role, "pid": DEMO_PROJECT_ID},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No demo user has that role.")
    user = User(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        project_id=row["project_id"],
        email=f"{body.role}@baton.internal",
        is_demo=True,
    )
    audit.log(session, user, "auth.demo_login", obj=user.id)
    return {"token": make_token(user), "user": user.__dict__}


@router.post("/auth/register", tags=["core"])
def register(
    body: RegisterRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    if body.role not in acl.ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role '{body.role}'. Must be one of: {', '.join(acl.ROLES)}")

    raw_token = body.supabase_token or authorization
    token_claims = verify_supabase_token(raw_token, expected_user_id=body.user_id, expected_email=body.email)
    uid = token_claims.get("sub") or (body.user_id.strip() if body.user_id else None)
    if not uid:
        raise HTTPException(status_code=400, detail="Missing user_id from token or payload.")

    email_clean = body.email.strip().lower()
    name_clean = body.name.strip()

    # 1. Duplicate email check: ensure email is not already claimed by a different registered user
    existing_by_email = session.execute(
        text("select id, name, role, email, is_demo from members where lower(email) = :email and is_demo = 0"),
        {"email": email_clean},
    ).mappings().first()
    if existing_by_email and existing_by_email["id"] != uid:
        raise HTTPException(
            status_code=400,
            detail="An account with this email is already registered. Please sign in instead.",
        )

    # 2. Existing user check: prevent role switching on registered accounts
    existing_by_id = session.execute(
        text("select id, name, role, project_id, active, email, is_demo from members where id = :id and is_demo = 0"),
        {"id": uid},
    ).mappings().first()

    if existing_by_id:
        assigned_role = existing_by_id["role"]
        if assigned_role != body.role:
            raise HTTPException(
                status_code=400,
                detail=f"Account already registered with role '{assigned_role}'. Unrestricted role switching is disabled.",
            )
        member_id = uid
        session.execute(
            text("update members set name = :name, active = 1 where id = :id"),
            {"name": name_clean, "id": member_id},
        )
    else:
        # 3. Approver registration requires valid single-use invitation or admin approval
        if body.role == "approver":
            approved = False
            if body.invitation_code and body.invitation_code.strip():
                code_clean = body.invitation_code.strip()
                inv_row = session.execute(
                    text("select id, code, role, email, status from invitations where code = :code and status = 'active'"),
                    {"code": code_clean},
                ).mappings().first()
                if inv_row:
                    if inv_row["role"] != "approver":
                        raise HTTPException(status_code=403, detail="Invitation code is not valid for Approver access.")
                    if inv_row["email"] and inv_row["email"].lower() != email_clean:
                        raise HTTPException(status_code=403, detail="Invitation code was issued to a different email address.")
                    approved = True
                    session.execute(
                        text("update invitations set status = 'used', used_by = :uid, used_at = :now where id = :id"),
                        {"uid": uid, "now": audit.now_iso(), "id": inv_row["id"]},
                    )
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid or expired invitation code for Approver access.",
                    )
            if not approved:
                # Check for admin pre-approval for this email
                admin_appr = session.execute(
                    text("select id from invitations where lower(email) = :email and role = 'approver' and status in ('approved', 'active')"),
                    {"email": email_clean},
                ).mappings().first()
                if admin_appr:
                    approved = True
                    session.execute(
                        text("update invitations set status = 'used', used_by = :uid, used_at = :now where id = :id"),
                        {"uid": uid, "now": audit.now_iso(), "id": admin_appr["id"]},
                    )

            if not approved:
                raise HTTPException(
                    status_code=403,
                    detail="Approver registration requires a valid invitation code or admin approval.",
                )

        member_id = uid
        # Ensure workspace project row exists
        session.execute(
            text(
                "insert into projects (id, name, created_at) "
                "values (:id, :name, :created_at) "
                "on conflict (id) do nothing"
            ),
            {"id": body.project_id, "name": f"{name_clean}'s Workspace", "created_at": audit.now_iso()},
        )
        try:
            session.execute(
                text("insert into members (id, project_id, name, role, active, email, is_demo) values (:id, :project_id, :name, :role, 1, :email, 0)"),
                {
                    "id": member_id,
                    "project_id": body.project_id,
                    "name": name_clean,
                    "role": body.role,
                    "email": email_clean,
                },
            )
        except Exception:
            session.execute(
                text("insert into members (id, project_id, name, role, active) values (:id, :project_id, :name, :role, 1)"),
                {
                    "id": member_id,
                    "project_id": body.project_id,
                    "name": name_clean,
                    "role": body.role,
                },
            )

    session.commit()
    user = User(
        id=member_id,
        name=name_clean,
        role=body.role if not existing_by_id else existing_by_id["role"],
        project_id=body.project_id,
        email=email_clean,
        is_demo=False,
    )
    # Additive mapping from Supabase sub to member, project, and role
    record_identity_mapping(
        session=session,
        supabase_sub=uid,
        member_id=user.id,
        project_id=user.project_id,
        role=user.role,
        email=user.email,
    )
    audit.log(session, user, "auth.register", obj=user.id, detail=f"Registered as {user.role}")
    return {"token": make_token(user), "user": user.__dict__}


@router.post("/auth/session", tags=["core"])
def sync_session(
    body: SessionRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    """Authenticate or sync an existing Supabase authenticated user with Baton."""
    raw_token = body.supabase_token or authorization
    token_claims = verify_supabase_token(raw_token, expected_user_id=body.user_id, expected_email=body.email)
    uid = token_claims.get("sub") or body.user_id.strip()
    email_clean = body.email.strip().lower()

    try:
        row = session.execute(
            text("select id, name, role, project_id, active, email, is_demo from members where (id = :id or lower(email) = :email) and is_demo = 0"),
            {"id": uid, "email": email_clean},
        ).mappings().first()
    except Exception:
        row = session.execute(
            text("select id, name, role, project_id, active from members where id = :id"),
            {"id": uid},
        ).mappings().first()

    if row is None or not row["active"]:
        return {
            "registered": False,
            "requires_role": True,
            "user_id": uid,
            "email": email_clean,
        }

    email_val = None
    if "email" in row and row["email"]:
        email_val = row["email"]
    elif email_clean:
        email_val = email_clean

    is_demo_val = bool(row["is_demo"]) if "is_demo" in row else False

    user = User(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        project_id=row["project_id"],
        email=email_val,
        is_demo=is_demo_val,
    )
    # Additive mapping from Supabase sub to member, project, and role
    record_identity_mapping(
        session=session,
        supabase_sub=uid,
        member_id=user.id,
        project_id=user.project_id,
        role=user.role,
        email=user.email,
    )
    try:
        audit.log(session, user, "auth.session_sync", obj=user.id)
    except Exception:
        pass
    return {"registered": True, "token": make_token(user), "user": user.__dict__}


@router.get("/auth/invitations/validate", tags=["core"])
def validate_invitation(code: str = Query(...), session: Session = Depends(get_session)):
    code_clean = code.strip()
    row = session.execute(
        text("select id, code, role, email, status from invitations where code = :code and status = 'active'"),
        {"code": code_clean},
    ).mappings().first()
    if row is None:
        return {"valid": False, "detail": "Invalid or expired invitation code."}
    return {"valid": True, "role": row["role"], "email": row.get("email")}


@router.post("/auth/invitations", tags=["core"])
def create_invitation(
    body: CreateInvitationRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    if user.role != "approver":
        raise HTTPException(status_code=403, detail="Only approvers can create invitations.")
    if body.role not in acl.ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")

    code = f"INV-{body.role.upper()}-{secrets.token_hex(8).upper()}"
    inv_id = f"inv-{uuid.uuid4().hex[:10]}"
    email_clean = body.email.strip().lower() if body.email else None

    session.execute(
        text("insert into invitations (id, code, role, email, status, created_at) values (:id, :code, :role, :email, 'active', :created_at)"),
        {
            "id": inv_id,
            "code": code,
            "role": body.role,
            "email": email_clean,
            "created_at": audit.now_iso(),
        },
    )
    session.commit()
    audit.log(session, user, "auth.create_invitation", obj=inv_id, detail=f"Code: {code} for role: {body.role}")
    return {"id": inv_id, "code": code, "role": body.role, "email": email_clean, "status": "active"}


@router.get("/me", tags=["core"])
def me(user: User = Depends(current_user)):
    return user.__dict__


@router.post("/demo/reset", tags=["core"])
def demo_reset(session: Session = Depends(get_session)):
    """One-click restore of the demo project. Open on purpose: demo data only, synthetic."""
    for hook in reset_hooks():
        hook(session)
    session.execute(text("delete from audit_log"))
    session.commit()
    seed_demo(session)
    return {"status": "reset", "project_id": DEMO_PROJECT_ID}
