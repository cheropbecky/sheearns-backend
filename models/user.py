from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
	full_name: str = Field(..., min_length=1, max_length=120)
	email: EmailStr
	phone: str | None = None
	location: str | None = None
	monthly_goal: int = Field(default=5000, ge=1000, le=1_000_000)
	bio: str | None = Field(default=None, max_length=500)
	avatar_url: str | None = None
	services: list[str] = Field(default_factory=list)
	notifications_enabled: bool = True
	marketing_emails_enabled: bool = False
	is_admin: bool = False
	is_suspended: bool = False
	is_deleted: bool = False


class UserCreate(BaseModel):
	full_name: str = Field(..., min_length=1, max_length=120)
	email: EmailStr
	password: str = Field(..., min_length=8, max_length=128)
	phone: str | None = None
	location: str | None = None


class UserLogin(BaseModel):
	email: EmailStr
	password: str = Field(..., min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
	current_password: str = Field(..., min_length=1, max_length=128)
	new_password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
	email: EmailStr | None = None
	full_name: str | None = Field(default=None, min_length=1, max_length=120)
	phone: str | None = None
	location: str | None = None
	monthly_goal: int | None = Field(default=None, ge=1000, le=1_000_000)
	bio: str | None = Field(default=None, max_length=500)
	avatar_url: str | None = None
	services: list[str] | None = None
	notifications_enabled: bool | None = None
	marketing_emails_enabled: bool | None = None


class UserPublic(UserBase):
	id: str
	is_premium: bool = False
	created_at: datetime | None = None
	updated_at: datetime | None = None


class UserInDB(UserPublic):
	password_hash: str
	created_at: datetime
	updated_at: datetime


class LoginResponse(BaseModel):
	access_token: str
	token_type: str = "bearer"
	user: UserPublic
