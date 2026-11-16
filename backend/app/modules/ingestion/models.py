"""Pydantic contracts for consented source ingestion and immutable evidence chunks."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

ACLLabel = Literal["L1", "L2", "L3", "L4"]
SourceType = Literal["document", "git", "task_csv", "chat"]


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    project_id: str
    title: str
    type: SourceType
    acl_label: ACLLabel
    consent: bool
    status: Literal["parsed", "skipped", "failed"]
    chunks: int
    note: str | None = None
    created_at: datetime | None = None


class ChunkOut(BaseModel):
    chunk_id: str
    project_id: str
    source_id: str
    source_type: SourceType
    source_title: str
    author: str
    event_time: datetime
    acl_label: ACLLabel
    content_hash: str
    char_span: tuple[int, int]
    text: str


class SourceCreateMetadata(BaseModel):
    """Metadata used by multipart endpoints and tests; consent is explicit."""
    acl_label: ACLLabel
    consent: bool

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("You must confirm that you are authorised to connect this source.")
        return value


class SourceConnectionCreate(BaseModel):
    provider: Literal["github", "jira"]
    target_name: str
    acl_label: ACLLabel = "L1"

    @field_validator("acl_label")
    @classmethod
    def validate_acl(cls, value: str) -> str:
        if value == "L4":
            raise ValueError("L4 secret sources are never accepted or stored.")
        return value


class SourceConnectionOut(BaseModel):
    id: str
    project_id: str
    provider: Literal["github", "jira"]
    target_name: str
    acl_label: ACLLabel
    status: Literal["connected", "syncing", "succeeded", "partial", "failed"]
    last_synced_at: str | None = None
    records_added: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    error_message: str | None = None
    created_at: str
    updated_at: str
