"""PUBLIC API of ingestion. Other modules must use this module, not its internals."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import acl
from app.modules.ingestion import service


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


def list_connections(project_id: str, session: Session) -> list[dict]:
    return service.list_connections(session, project_id)


def create_connection(project_id: str, provider: str, target_name: str, acl_label: str, session: Session) -> dict:
    return service.create_connection(session, project_id, provider, target_name, acl_label)


def delete_connection(project_id: str, connection_id: str, session: Session) -> bool:
    return service.delete_connection(session, project_id, connection_id)


def sync_connection(project_id: str, connection_id: str, session: Session) -> dict:
    return service.sync_connection(session, project_id, connection_id)


def reset_demo(session: Session) -> None:
    session.execute(text("delete from source_connections where project_id = 'proj-campus-event-portal'"))
    session.execute(text("delete from source_chunks where project_id = 'proj-campus-event-portal' and source_id not like 'S%'"))
    session.execute(text("delete from sources where project_id = 'proj-campus-event-portal' and id not like 'S%'"))
    session.commit()


