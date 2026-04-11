from __future__ import annotations

import re
from typing import Annotated
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, status

from models.user import (
	LoginResponse,
	PasswordChangeRequest,
	UserCreate,
	UserLogin,
	UserPublic,
	UserUpdate,
)
from services.auth_service import create_token, hash_password, verify_password, verify_token
from services.supabase_service import fetch_row, fetch_rows, get_supabase_client, insert_row, update_rows

try:
	from postgrest.exceptions import APIError
except Exception:  # noqa: BLE001
	APIError = Exception


router = APIRouter()

USER_TABLE = "users"
DEFAULT_MONTHLY_GOAL = 5000
_REQUIRED_USER_FIELDS = {"id", "full_name", "email", "password_hash"}


_users_by_email: dict[str, dict[str, Any]] = {}
_users_by_id: dict[str, dict[str, Any]] = {}


def _public_user(user: dict[str, Any]) -> UserPublic:
	return UserPublic(
		id=user["id"],
		full_name=user["full_name"],
		email=user["email"],
		phone=user.get("phone"),
		location=user.get("location"),
		monthly_goal=int(user.get("monthly_goal") or DEFAULT_MONTHLY_GOAL),
		bio=user.get("bio"),
		avatar_url=user.get("avatar_url"),
		services=user.get("services", []),
		notifications_enabled=user.get("notifications_enabled", True),
		marketing_emails_enabled=user.get("marketing_emails_enabled", False),
		is_premium=user.get("is_premium", False),
	)


def _normalize_services(value: Any) -> list[str]:
	if not value:
		return []
	if isinstance(value, list):
		return [str(service).strip() for service in value if str(service).strip()]
	return []


def _normalize_user_record(user: dict[str, Any]) -> dict[str, Any]:
	return {
		"id": user["id"],
		"full_name": user["full_name"],
		"email": str(user["email"]).lower(),
		"password_hash": user["password_hash"],
		"phone": user.get("phone"),
		"location": user.get("location"),
		"monthly_goal": int(user.get("monthly_goal") or DEFAULT_MONTHLY_GOAL),
		"bio": user.get("bio"),
		"avatar_url": user.get("avatar_url"),
		"services": _normalize_services(user.get("services")),
		"notifications_enabled": user.get("notifications_enabled", True),
		"marketing_emails_enabled": user.get("marketing_emails_enabled", False),
		"is_premium": user.get("is_premium", False),
		"token": user.get("token"),
	}


def _db_user_payload(user: dict[str, Any]) -> dict[str, Any]:
	payload = dict(user)
	payload.pop("token", None)
	return payload


def _using_database() -> bool:
	return get_supabase_client() is not None


def _is_missing_monthly_goal_column_error(exc: Exception) -> bool:
	if not isinstance(exc, APIError):
		return False
	code = str(getattr(exc, "code", "") or "")
	message = str(getattr(exc, "message", "") or exc)
	return code == "PGRST204" and "monthly_goal" in message


def _missing_column_name(exc: Exception) -> str | None:
	if not isinstance(exc, APIError):
		return None
	code = str(getattr(exc, "code", "") or "")
	message = str(getattr(exc, "message", "") or exc)
	if code != "PGRST204":
		return None
	match = re.search(r"column '([^']+)'", message)
	if match:
		return match.group(1)
	match = re.search(r"'([^']+)' column", message)
	if match:
		return match.group(1)
	return None


def _without_monthly_goal(payload: dict[str, Any]) -> dict[str, Any]:
	trimmed = dict(payload)
	trimmed.pop("monthly_goal", None)
	return trimmed


def _without_optional_user_column(payload: dict[str, Any], column_name: str | None) -> dict[str, Any]:
	if not column_name:
		return payload
	trimmed = dict(payload)
	trimmed.pop(column_name, None)
	return trimmed


def _get_user_by_email(email: str) -> dict[str, Any] | None:
	if _using_database():
		return fetch_row(USER_TABLE, filters={"email": email.lower()})
	return _users_by_email.get(email.lower())


def _get_user_by_id(user_id: str) -> dict[str, Any] | None:
	if _using_database():
		return fetch_row(USER_TABLE, filters={"id": user_id})
	return _users_by_id.get(user_id)


def _get_user_by_token(token: str | None) -> dict[str, Any]:
	if not token:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization token")

	token = token.replace("Bearer ", "", 1)

	try:
		user_id = verify_token(token)
	except Exception as exc:  # noqa: BLE001
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

	user = _get_user_by_id(user_id)
	if user is not None:
		return user

	raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found for token")


def _save_user_record(user: dict[str, Any]) -> dict[str, Any]:
	if _using_database():
		payload = _db_user_payload(user)
		try:
			stored = insert_row(USER_TABLE, payload)
		except Exception as exc:  # noqa: BLE001
			column_name = _missing_column_name(exc)
			if column_name in _REQUIRED_USER_FIELDS or column_name is None:
				raise
			stored = insert_row(USER_TABLE, _without_optional_user_column(payload, column_name))
		if stored is None:
			raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to save user")
		return stored

	_users_by_email[user["email"]] = user
	_users_by_id[user["id"]] = user
	return user


def _update_user_record(user: dict[str, Any]) -> dict[str, Any]:
	if _using_database():
		payload = _db_user_payload(user)
		try:
			updated = update_rows(USER_TABLE, filters={"id": user["id"]}, payload=payload)
		except Exception as exc:  # noqa: BLE001
			column_name = _missing_column_name(exc)
			if column_name in _REQUIRED_USER_FIELDS or column_name is None:
				raise
			updated = update_rows(USER_TABLE, filters={"id": user["id"]}, payload=_without_optional_user_column(payload, column_name))
		if not updated:
			raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update user")
		return updated[0]

	_users_by_email[user["email"]] = user
	_users_by_id[user["id"]] = user
	return user


@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate) -> LoginResponse:
	email = str(payload.email).lower()
	if _get_user_by_email(email) is not None:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A user with this email already exists")

	user_id = str(uuid4())
	password_hash = hash_password(payload.password)
	token = create_token(user_id)

	user_record: dict[str, Any] = {
		"id": user_id,
		"full_name": payload.full_name,
		"email": email,
		"password_hash": password_hash,
		"phone": payload.phone,
		"location": payload.location,
		"monthly_goal": DEFAULT_MONTHLY_GOAL,
		"bio": None,
		"avatar_url": None,
		"services": [],
		"notifications_enabled": True,
		"marketing_emails_enabled": False,
		"is_premium": False,
		"token": token,
	}

	stored_user = _save_user_record(user_record)

	return LoginResponse(access_token=token, user=_public_user(stored_user))


@router.post("/login", response_model=LoginResponse)
@router.post("/signin", response_model=LoginResponse)
def login_user(payload: UserLogin) -> LoginResponse:
	user = _get_user_by_email(str(payload.email).lower())
	if not user or not verify_password(payload.password, user["password_hash"]):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

	token = create_token(user["id"])
	return LoginResponse(access_token=token, user=_public_user(user))


@router.get("/me", response_model=UserPublic)
@router.get("/current", response_model=UserPublic)
def get_current_user(authorization: Annotated[str | None, Header()] = None) -> UserPublic:
	user = _get_user_by_token(authorization)
	return _public_user(user)


@router.put("/me", response_model=UserPublic)
def update_current_user(payload: UserUpdate, authorization: Annotated[str | None, Header()] = None) -> UserPublic:
	user = _get_user_by_token(authorization)

	if payload.email is not None:
		next_email = str(payload.email).lower()
		existing = _get_user_by_email(next_email)
		if existing is not None and existing["id"] != user["id"]:
			raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

		old_email = user["email"]
		user["email"] = next_email
		if old_email != next_email:
			_users_by_email.pop(old_email, None)
			_users_by_email[next_email] = user

	if payload.full_name is not None:
		user["full_name"] = payload.full_name
	if payload.phone is not None:
		user["phone"] = payload.phone
	if payload.location is not None:
		user["location"] = payload.location
	if payload.monthly_goal is not None:
		user["monthly_goal"] = payload.monthly_goal
	if payload.bio is not None:
		user["bio"] = payload.bio
	if payload.avatar_url is not None:
		user["avatar_url"] = payload.avatar_url
	if payload.services is not None:
		user["services"] = [service.strip() for service in payload.services if service and service.strip()]
	if payload.notifications_enabled is not None:
		user["notifications_enabled"] = payload.notifications_enabled
	if payload.marketing_emails_enabled is not None:
		user["marketing_emails_enabled"] = payload.marketing_emails_enabled

	updated_user = _update_user_record(user)
	return _public_user(updated_user)


@router.post("/me/change-password", status_code=status.HTTP_200_OK)
def change_current_user_password(
	payload: PasswordChangeRequest,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
	user = _get_user_by_token(authorization)

	if not verify_password(payload.current_password, user["password_hash"]):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Current password is incorrect",
		)

	if verify_password(payload.new_password, user["password_hash"]):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="New password must be different from current password",
		)

	user["password_hash"] = hash_password(payload.new_password)
	_update_user_record(user)

	return {"message": "Password updated successfully"}


@router.get("/{user_id}", response_model=UserPublic)
def get_public_profile(user_id: str) -> UserPublic:
	user = _get_user_by_id(user_id)
	if not user:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

	return _public_user(user)
