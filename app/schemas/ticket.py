import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.ticket import TicketPriority, TicketStatus, Terminal


# ── Request schemas ───────────────────────────────────────

class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=10)
    priority: TicketPriority
    location: Optional[str] = None
    terminal: Optional[Terminal] = None
    equipment_model: Optional[str] = Field(default=None, max_length=255)
    device_type: Optional[str] = Field(default=None, max_length=100)


class TicketStatusUpdate(BaseModel):
    status: TicketStatus
    notes: Optional[str] = None


class TicketAssign(BaseModel):
    assignee_id: uuid.UUID


# ── Response schema ───────────────────────────────────────

class TicketResponse(BaseModel):
    id: uuid.UUID
    ticket_number: Optional[str] = None   # 8-digit display number, e.g. "10000042"
    title: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    location: Optional[str] = None
    terminal: Optional[Terminal] = None
    equipment_model: Optional[str] = None
    device_type: Optional[str] = None

    # ML fields (Phase 4)
    category: Optional[str] = None
    ml_confidence: Optional[float] = None

    # Image field (Phase 5)
    image_path: Optional[str] = None

    reporter_id: Optional[uuid.UUID] = None
    assignee_id: Optional[uuid.UUID] = None

    # Timestamps
    assigned_at:  Optional[datetime] = None
    resolved_at:  Optional[datetime] = None
    closed_at:    Optional[datetime] = None
    created_at:   datetime
    updated_at:   Optional[datetime] = None

    # Computed — not stored in DB, calculated from assigned_at + priority
    sla_deadline: Optional[datetime] = None

    # Priority queue fields
    is_paused:               Optional[bool]     = None
    paused_at:               Optional[datetime] = None
    remaining_sla_seconds:   Optional[int]      = None
    additional_assignee_ids: Optional[str]      = None  # JSON array of UUID strings
    escalated_at:            Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Dashboard stats ───────────────────────────────────────

class DashboardStats(BaseModel):
    open: int
    critical: int
    sla_breaches: int
    resolved_today: int
