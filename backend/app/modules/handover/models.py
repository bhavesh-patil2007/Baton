"""Request models for the handover endpoints."""
from typing import Literal

from pydantic import BaseModel, Field


class AckRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    section: str = Field(min_length=1, max_length=100)


class OwnershipRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    claim_id: str = Field(min_length=1, max_length=100)
    response: Literal["accept", "decline"]
    note: str | None = Field(default=None, max_length=500)


class ActionStatusRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    claim_id: str = Field(min_length=1, max_length=100)
    status: Literal["not_started", "in_progress", "blocked", "completed"]
    owner: str | None = Field(default=None, max_length=100)
    due_date: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=500)
