from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
	reviewer_name: str = Field(..., min_length=1, max_length=120)
	rating: int = Field(..., ge=1, le=5)
	comment: str = Field(..., min_length=1, max_length=1000)


class ReviewRecord(ReviewCreate):
	id: str
	service_id: str
	created_at: datetime


class ReviewUpdate(BaseModel):
	rating: int | None = Field(default=None, ge=1, le=5)
	comment: str | None = Field(default=None, min_length=1, max_length=1000)
