"""PUBLIC API of ingestion. Other modules must use this module, not its internals."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core import acl
from app.modules.ingestion import service


def can_manage_sources(role: str) -> bool:
    return service.can_manage_sources(role)


def list_sources(project_id: str, role: str, session: Session) -> list[dict]:
    # Do not reveal restricted source titles or metadata to lower-clearance roles.
    return [source for source in service.list_sources(session, project_id) if acl.can_read(role, source.get("acl_label"))]


def get_chunk(project_id: str, chunk_id: str, role: str, session: Session) -> dict | None:
    chunk = service.get_chunk(session, project_id, chunk_id)
    if chunk is None or not acl.can_read(role, chunk.get("acl_label")):
        return None
    return chunk


def list_chunks(project_id: str, role: str, session: Session) -> list[dict]:
    return [chunk for chunk in service.list_chunks(session, project_id) if acl.can_read(role, chunk.get("acl_label"))]


def create_captured_note(
    project_id: str,
    title: str,
    body: str,
    author: str,
    acl_label: str,
    session: Session,
) -> dict:
    """Persist an explicitly consented note through ingestion's redaction and chunking path."""
    safe_title = service.redact_text(title.strip())
    record = service.ParsedRecord(
        text=f"# {safe_title}\n\n{body.strip()}",
        author=author.strip() or "Unknown",
        event_time=datetime.now(UTC),
    )
    return service.persist_source(
        session,
        project_id=project_id,
        filename=f"{safe_title}.md",
        source_type="document",
        acl_label=acl_label,
        records=[record],
    )
