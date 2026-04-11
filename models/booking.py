from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


BookingStatus = Literal["pending", "accepted", "rejected", "completed", "cancelled"]


class BookingCreate(BaseModel):
	message: str | None = Field(default=None, max_length=1000)


class BookingRecord(BaseModel):
	id: str
	service_id: str
	customer_user_id: str
	provider_user_id: str
	status: BookingStatus
	amount: int = Field(..., ge=0)
	message: str | None = None
	created_at: datetime


class BookingStatusUpdate(BaseModel):
	status: BookingStatus


class BookingArchiveUpdate(BaseModel):
	archived: bool
