from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IncomeBase(BaseModel):
	amount: int = Field(..., gt=0)
	source: str = Field(..., min_length=1, max_length=120)
	note: str | None = Field(default=None, max_length=500)


class IncomeCreate(IncomeBase):
	earned_at: datetime | None = None


class IncomeRecord(IncomeBase):
	id: str
	user_id: str
	earned_at: datetime


class IncomeSummary(BaseModel):
	monthly_goal: int = Field(..., gt=0)
	earned: int = Field(default=0, ge=0)
	remaining: int = Field(default=0, ge=0)
	progress_percent: float = Field(default=0, ge=0, le=100)
	log_count: int = Field(default=0, ge=0)


class Milestone(BaseModel):
	key: str
	label: str
	unlocked: bool
	target: str
