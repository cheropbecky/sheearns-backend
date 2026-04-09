from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceBase(BaseModel):
	title: str = Field(..., min_length=1, max_length=120)
	category: str = Field(..., min_length=1, max_length=80)
	description: str = Field(..., min_length=1, max_length=2000)
	price_min: int = Field(..., ge=0)
	price_max: int = Field(..., ge=0)
	location: str = Field(..., min_length=1, max_length=120)
	portfolio_urls: list[str] = Field(default_factory=list)


class ServiceCreate(ServiceBase):
	pass


class ServiceUpdate(BaseModel):
	title: str | None = Field(default=None, min_length=1, max_length=120)
	category: str | None = Field(default=None, min_length=1, max_length=80)
	description: str | None = Field(default=None, min_length=1, max_length=2000)
	price_min: int | None = Field(default=None, ge=0)
	price_max: int | None = Field(default=None, ge=0)
	location: str | None = Field(default=None, min_length=1, max_length=120)
	portfolio_urls: list[str] | None = None
	is_active: bool | None = None


class ServicePublic(ServiceBase):
	id: str
	user_id: str
	rating: float = 0.0
	review_count: int = 0
	is_active: bool = True
	created_at: datetime


class ServiceFilters(BaseModel):
	category: str | None = None
	location: str | None = None
	min_price: int | None = Field(default=None, ge=0)
	max_price: int | None = Field(default=None, ge=0)
