from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
	message: str | None = Field(default=None, max_length=1000)


class BookingRecord(BaseModel):
	id: str
	service_id: str
	customer_user_id: str
	provider_user_id: str
	status: str
	amount: int = Field(..., ge=0)
	message: str | None = None
	created_at: datetime
