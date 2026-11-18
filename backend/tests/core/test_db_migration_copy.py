"""Test running migrations on a copy of existing baton_dev.db twice to verify safety, data preservation, and idempotence."""
import shutil
import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT_DIR / "db" / "schema"


def test_migration_on_copy_of_baton_dev_db(tmp_path):
    # Locate existing baton_dev.db
    src_db = ROOT_DIR / "baton_dev.db"
    if not src_db.exists():
        src_db = ROOT_DIR / "backend" / "baton_dev.db"

    assert src_db.exists(), f"Could not find existing baton_dev.db at {src_db}"

    # 1. Create a temporary copy (NEVER mutate the user's actual database)
    test_db = tmp_path / "baton_dev_test_copy.db"
    shutil.copy2(src_db, test_db)
    assert test_db.exists()

    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row

    # 2. Inspect initial pre-migration state
    initial_tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'").fetchall()]

    pre_members = conn.execute("select * from members").fetchall() if "members" in initial_tables else []
    pre_claims = conn.execute("select count(*) from claims").fetchone()[0] if "claims" in initial_tables else 0
    pre_tasks = conn.execute("select count(*) from handover_tasks").fetchone()[0] if "handover_tasks" in initial_tables else 0

    assert len(pre_members) > 0, "baton_dev.db should contain members"
    conn.close()

    # Read migration scripts
    script_10 = (SCHEMA_DIR / "10_core.sql").read_text(encoding="utf-8")
    script_15 = (SCHEMA_DIR / "15_multiproject.sql").read_text(encoding="utf-8")

    def run_migration_pass(db_path):
        from app.core.db import _ensure_columns, _preflight_check

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        _preflight_check(c)
        _ensure_columns(c)
        c.executescript(script_10)
        c.executescript(script_15)
        c.commit()
        c.close()

    # 3. FIRST MIGRATION PASS
    run_migration_pass(test_db)

    # Verify first pass
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row

    users_pass1 = conn.execute("select * from users").fetchall()
    memberships_pass1 = conn.execute("select * from project_memberships").fetchall()
    invitations_pass1 = conn.execute("select * from invitations").fetchall()
    claims_pass1 = conn.execute("select count(*) from claims").fetchone()[0] if "claims" in initial_tables else 0
    tasks_pass1 = conn.execute("select count(*) from handover_tasks").fetchone()[0] if "handover_tasks" in initial_tables else 0

    # Ensure all original members are migrated to users and project_memberships
    assert len(users_pass1) >= len(pre_members)
    assert len(memberships_pass1) >= len(pre_members)
    assert claims_pass1 == pre_claims
    assert tasks_pass1 == pre_tasks

    # Verify demo accounts are preserved
    demo_roles = {r["role"] for r in memberships_pass1 if r["project_id"] == "proj-campus-event-portal"}
    assert {"approver", "leaver", "incoming_maintainer", "incoming_contributor"}.issubset(demo_roles)

    # Verify invitations are preserved and have project_id assigned
    for inv in invitations_pass1:
        assert inv["project_id"] is not None

    conn.close()

    # 4. SECOND MIGRATION PASS (Verifies idempotence)
    run_migration_pass(test_db)

    # Verify second pass produces identical results without duplication
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row

    users_pass2 = conn.execute("select * from users").fetchall()
    memberships_pass2 = conn.execute("select * from project_memberships").fetchall()
    claims_pass2 = conn.execute("select count(*) from claims").fetchone()[0] if "claims" in initial_tables else 0
    tasks_pass2 = conn.execute("select count(*) from handover_tasks").fetchone()[0] if "handover_tasks" in initial_tables else 0

    assert len(users_pass2) == len(users_pass1)
    assert len(memberships_pass2) == len(memberships_pass1)
    assert claims_pass2 == pre_claims
    assert tasks_pass2 == pre_tasks

    conn.close()
