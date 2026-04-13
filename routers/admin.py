from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from models.service import ServiceApprovalUpdate
from services.admin_service import get_ai_misuse_events, get_common_ai_questions, list_ai_conversations, list_announcements, record_announcement
from services.auth_service import verify_token
from services.notification_service import send_announcement_notifications
from services.supabase_service import delete_rows, fetch_row, fetch_rows, get_supabase_client, update_rows

try:
	from routers.users import _get_user_by_token
except Exception:  # noqa: BLE001
	_get_user_by_token = None


router = APIRouter()

USERS_TABLE = "users"
SERVICES_TABLE = "services"
BOOKINGS_TABLE = "bookings"
DEFAULT_MONTHLY_GOAL = 5000


class UserModerationRequest(BaseModel):
	action: Literal["suspend", "unsuspend", "delete"]
	reason: str | None = Field(default=None, max_length=500)


class AnnouncementRequest(BaseModel):
	title: str = Field(..., min_length=1, max_length=120)
	body: str = Field(..., min_length=1, max_length=2000)
	channel: str = Field(default="dashboard", max_length=40)


class ServiceModerationRequest(BaseModel):
	action: Literal["approve", "reject", "pending"]


def _now() -> datetime:
	return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
	if not value:
		return None
	if isinstance(value, datetime):
		return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
	try:
		text = str(value).replace("Z", "+00:00")
		parsed = datetime.fromisoformat(text)
		return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
	except Exception:  # noqa: BLE001
		return None


def _format_date(value: datetime) -> str:
	return value.date().isoformat()


def _admin_emails() -> set[str]:
	raw = os.getenv("ADMIN_EMAILS", "")
	return {email.strip().lower() for email in raw.split(",") if email.strip()}


def _is_admin_user(user: dict[str, Any]) -> bool:
	return bool(user.get("is_admin", False) or str(user.get("email", "")).lower() in _admin_emails())


def _require_db() -> None:
	if get_supabase_client() is None:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not configured")


def _require_admin_user(authorization: str | None) -> dict[str, Any]:
	if _get_user_by_token is None:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="User auth is not available")
	user = _get_user_by_token(authorization)
	if not _is_admin_user(user):
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
	return user


def _mask_name(full_name: str | None) -> str:
	parts = [part for part in str(full_name or "").split() if part]
	if not parts:
		return "User"
	if len(parts) == 1:
		return f"{parts[0][0].upper()}."
	return f"{parts[0][0].upper()}. {parts[-1][0].upper()}."


def _mask_email(email: str | None) -> str:
	address = str(email or "")
	if "@" not in address:
		return "hidden"
	local, domain = address.split("@", 1)
	if not local:
		return f"***@{domain}"
	if len(local) <= 2:
		masked_local = f"{local[0]}*"
	else:
		masked_local = f"{local[0]}***{local[-1]}"
	return f"{masked_local}@{domain}"


def _service_visible(service: dict[str, Any]) -> bool:
	approval_status = str(service.get("approval_status") or "pending").lower()
	return bool(service.get("is_active", True)) and approval_status in {"approved", "pending"}


def _service_label(service: dict[str, Any]) -> str:
	return str(service.get("title") or "Service")


def _is_missing_approval_status_column_error(exc: Exception) -> bool:
	return "approval_status" in str(exc).lower()


def _soft_delete_user(user_id: str) -> dict[str, Any] | None:
	updated = update_rows(USERS_TABLE, filters={"id": user_id}, payload={"is_deleted": True, "is_suspended": True})
	if not updated:
		return None
	return updated[0]


def _hard_delete_user(user_id: str) -> bool:
	try:
		deleted = delete_rows(USERS_TABLE, filters={"id": user_id})
	except Exception:  # noqa: BLE001
		return False
	return bool(deleted)


def _soft_delete_service(service_id: str) -> bool:
	try:
		updated = update_rows(
			SERVICES_TABLE,
			filters={"id": service_id},
			payload={"is_active": False, "approval_status": "rejected"},
		)
	except Exception as exc:  # noqa: BLE001
		if not _is_missing_approval_status_column_error(exc):
			return False
		updated = update_rows(SERVICES_TABLE, filters={"id": service_id}, payload={"is_active": False})
	return bool(updated)


def _hard_delete_service(service_id: str) -> bool:
	try:
		deleted = delete_rows(SERVICES_TABLE, filters={"id": service_id})
	except Exception:  # noqa: BLE001
		return False
	return bool(deleted)


def _completed_booking_status(booking: dict[str, Any]) -> bool:
	return str(booking.get("status") or "").lower() in {"completed", "confirmed", "paid"}


def _build_users(users: list[dict[str, Any]], services: list[dict[str, Any]], bookings: list[dict[str, Any]], ai_events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, datetime]]:
	service_counts = Counter(service.get("user_id") for service in services if service.get("user_id"))
	booking_counts = Counter()
	last_activity: dict[str, datetime] = {}

	for booking in bookings:
		for key in ("provider_user_id", "customer_user_id"):
			user_id = booking.get(key)
			if user_id:
				booking_counts[user_id] += 1
				created_at = _parse_datetime(booking.get("created_at"))
				if created_at:
					last_activity[user_id] = max(last_activity.get(user_id, created_at), created_at)

	for event in ai_events:
		user_id = event.get("user_id")
		if user_id:
			created_at = _parse_datetime(event.get("created_at"))
			if created_at:
				last_activity[user_id] = max(last_activity.get(user_id, created_at), created_at)

	for service in services:
		user_id = service.get("user_id")
		created_at = _parse_datetime(service.get("created_at"))
		if user_id and created_at:
			last_activity[user_id] = max(last_activity.get(user_id, created_at), created_at)

	user_cards: list[dict[str, Any]] = []
	for user in users:
		created_at = _parse_datetime(user.get("created_at"))
		updated_at = _parse_datetime(user.get("updated_at")) or created_at
		latest_activity = max([value for value in [created_at, updated_at, last_activity.get(user["id"])] if value], default=None)
		is_blocked = bool(user.get("is_deleted", False) or user.get("is_suspended", False))
		is_active = bool(not is_blocked and latest_activity and latest_activity >= _now() - timedelta(days=30))
		status_label = "deleted" if user.get("is_deleted") else "suspended" if user.get("is_suspended") else "active" if is_active else "inactive"
		user_cards.append(
			{
				"id": user["id"],
				"name": user.get("full_name"),
				"masked_name": _mask_name(user.get("full_name")),
				"masked_email": _mask_email(user.get("email")),
				"email": user.get("email"),
				"location": user.get("location"),
				"skills": user.get("services", []),
				"service_count": service_counts.get(user["id"], 0),
				"booking_count": booking_counts.get(user["id"], 0),
				"monthly_goal": int(user.get("monthly_goal") or DEFAULT_MONTHLY_GOAL),
				"status": status_label,
				"is_active": is_active,
				"is_suspended": bool(user.get("is_suspended", False)),
				"is_deleted": bool(user.get("is_deleted", False)),
				"is_admin": bool(user.get("is_admin", False)),
				"joined_at": user.get("created_at"),
				"last_activity_at": latest_activity.isoformat() if latest_activity else None,
			}
		)

	return user_cards, last_activity


def _build_service_cards(services: list[dict[str, Any]], users_by_id: dict[str, dict[str, Any]], bookings: list[dict[str, Any]]) -> list[dict[str, Any]]:
	review_counts = Counter(service.get("id") for service in services)
	booking_counts = Counter(booking.get("service_id") for booking in bookings)
	category_counts = Counter(service.get("category") for service in services if service.get("category"))
	service_cards: list[dict[str, Any]] = []

	for service in services:
		provider = users_by_id.get(service.get("user_id"), {})
		service_cards.append(
			{
				"id": service["id"],
				"title": service.get("title"),
				"category": service.get("category"),
				"location": service.get("location"),
				"price_min": int(service.get("price_min") or 0),
				"price_max": int(service.get("price_max") or 0),
				"provider_name": provider.get("full_name"),
				"provider_masked": _mask_name(provider.get("full_name")),
				"approval_status": str(service.get("approval_status") or "pending"),
				"is_active": bool(service.get("is_active", True)),
				"bookings": booking_counts.get(service.get("id"), 0),
				"reviews": review_counts.get(service.get("id"), 0),
				"created_at": service.get("created_at"),
			}
		)

	return service_cards


def _build_overview(users: list[dict[str, Any]], services: list[dict[str, Any]], bookings: list[dict[str, Any]], ai_events: list[dict[str, Any]]) -> dict[str, Any]:
	users_cards, last_activity = _build_users(users, services, bookings, ai_events)
	users_by_id = {user["id"]: user for user in users}
	service_cards = _build_service_cards(services, users_by_id, bookings)

	now = _now()
	window_7 = now - timedelta(days=7)
	window_14 = now - timedelta(days=14)
	window_30 = now - timedelta(days=30)
	last_week_start = now - timedelta(days=14)
	last_week_end = now - timedelta(days=7)

	user_join_dates = [(_parse_datetime(user.get("created_at")), user) for user in users]
	new_signups_this_week = sum(1 for created_at, _ in user_join_dates if created_at and created_at >= window_7)
	new_signups_last_week = sum(1 for created_at, _ in user_join_dates if created_at and last_week_start <= created_at < last_week_end)
	new_signups_this_month = sum(1 for created_at, _ in user_join_dates if created_at and created_at >= window_30)

	active_users = sum(1 for user in users_cards if user["status"] == "active")
	inactive_users = sum(1 for user in users_cards if user["status"] == "inactive")
	blocked_users = sum(1 for user in users_cards if user["status"] in {"suspended", "deleted"})

	completed_bookings = [booking for booking in bookings if _completed_booking_status(booking)]
	total_income = sum(int(booking.get("amount") or 0) for booking in completed_bookings)
	income_recent = sum(int(booking.get("amount") or 0) for booking in completed_bookings if (_parse_datetime(booking.get("created_at")) or now) >= window_7)

	users_by_id = {user["id"]: user for user in users}
	income_by_user: dict[str, int] = defaultdict(int)
	for booking in completed_bookings:
		provider_id = booking.get("provider_user_id")
		if provider_id:
			income_by_user[provider_id] += int(booking.get("amount") or 0)

	top_earning_users = sorted(
		[
			{
				"user_id": user_id,
				"masked_name": _mask_name(users_by_id.get(user_id, {}).get("full_name")),
				"masked_email": _mask_email(users_by_id.get(user_id, {}).get("email")),
				"total": total,
			}
			for user_id, total in income_by_user.items()
		],
		key=lambda item: item["total"],
		reverse=True,
	)[:5]

	category_counts = Counter(service.get("category") for service in services if service.get("category"))
	location_counts = Counter(user.get("location") for user in users if user.get("location"))
	skill_counts = Counter(skill for user in users for skill in user.get("services", []) if skill)

	popular_questions = get_common_ai_questions()
	misuse_events = get_ai_misuse_events()

	daily_signups: dict[str, int] = defaultdict(int)
	daily_income: dict[str, int] = defaultdict(int)
	for user in users:
		created_at = _parse_datetime(user.get("created_at"))
		if created_at and created_at >= window_14:
			daily_signups[_format_date(created_at)] += 1
	for booking in completed_bookings:
		created_at = _parse_datetime(booking.get("created_at"))
		if created_at and created_at >= window_14:
			daily_income[_format_date(created_at)] += int(booking.get("amount") or 0)

	chart_days = [(_now() - timedelta(days=offset)).date().isoformat() for offset in range(13, -1, -1)]
	growth_chart = [
		{
			"date": day,
			"signups": daily_signups.get(day, 0),
			"income": daily_income.get(day, 0),
		}
		for day in chart_days
	]

	recent_ai = sorted(ai_events, key=lambda item: item.get("created_at") or "", reverse=True)[:10]
	recent_announcements = sorted(list_announcements(), key=lambda item: item.get("created_at") or "", reverse=True)[:5]

	users_by_status = Counter(user["status"] for user in users_cards)
	return {
		"summary": {
			"total_users": len(users),
			"total_services": len(services),
			"total_income": total_income,
			"new_signups_this_week": new_signups_this_week,
			"new_signups_last_week": new_signups_last_week,
			"new_signups_this_month": new_signups_this_month,
			"active_users": active_users,
			"inactive_users": inactive_users,
			"blocked_users": blocked_users,
			"ai_conversations": len(ai_events),
			"misuse_flags": len(misuse_events),
			"income_last_7_days": income_recent,
		},
		"users": users_cards,
		"services": service_cards,
		"popular_categories": [{"label": label, "count": count} for label, count in category_counts.most_common(5)],
		"popular_locations": [{"label": label, "count": count} for label, count in location_counts.most_common(5)],
		"popular_skills": [{"label": label, "count": count} for label, count in skill_counts.most_common(5)],
		"top_earning_users": top_earning_users,
		"growth_chart": growth_chart,
		"user_status_breakdown": [{"label": label, "count": count} for label, count in users_by_status.items()],
		"ai_monitoring": {
			"total_conversations": len(ai_events),
			"popular_questions": popular_questions,
			"misuse_events": misuse_events[:10],
			"recent_conversations": recent_ai,
		},
		"announcements": recent_announcements,
	}


@router.get("/dashboard")
def admin_dashboard(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
	_require_db()
	_require_admin_user(authorization)
	users = fetch_rows(USERS_TABLE, limit=5000)
	services = fetch_rows(SERVICES_TABLE, limit=5000)
	bookings = fetch_rows(BOOKINGS_TABLE, limit=5000)
	ai_events = list_ai_conversations()
	return _build_overview(users, services, bookings, ai_events)


@router.patch("/users/{user_id}/status")
def update_user_status(
	user_id: str,
	payload: UserModerationRequest,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	_require_admin_user(authorization)
	user = fetch_row(USERS_TABLE, filters={"id": user_id})
	if user is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

	updates: dict[str, Any] = {}
	if payload.action == "suspend":
		updates = {"is_suspended": True, "is_deleted": False}
	elif payload.action == "unsuspend":
		updates = {"is_suspended": False}
	elif payload.action == "delete":
		if _hard_delete_user(user_id):
			return {"id": user_id, "deleted": True, "mode": "hard"}
		soft_deleted = _soft_delete_user(user_id)
		if soft_deleted is None:
			raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to delete user")
		return soft_deleted

	updated = update_rows(USERS_TABLE, filters={"id": user_id}, payload=updates)
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update user status")
	return updated[0]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, authorization: Annotated[str | None, Header()] = None) -> None:
	_require_db()
	_require_admin_user(authorization)
	user = fetch_row(USERS_TABLE, filters={"id": user_id})
	if user is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
	if _hard_delete_user(user_id):
		return
	soft_deleted = _soft_delete_user(user_id)
	if soft_deleted is None:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to delete user")


@router.patch("/services/{service_id}/approval")
def update_service_approval(
	service_id: str,
	payload: ServiceApprovalUpdate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	_require_admin_user(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

	updates = {
		"approval_status": payload.approval_status,
		"is_active": payload.approval_status != "rejected",
	}
	try:
		updated = update_rows(SERVICES_TABLE, filters={"id": service_id}, payload=updates)
	except Exception as exc:  # noqa: BLE001
		if not _is_missing_approval_status_column_error(exc):
			raise
		updated = update_rows(
			SERVICES_TABLE,
			filters={"id": service_id},
			payload={"is_active": payload.approval_status != "rejected"},
		)
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update service approval")
	return updated[0]


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: str, authorization: Annotated[str | None, Header()] = None) -> None:
	_require_db()
	_require_admin_user(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	if _hard_delete_service(service_id):
		return
	if not _soft_delete_service(service_id):
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to delete service")


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
def create_announcement(
	payload: AnnouncementRequest,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	_require_admin_user(authorization)
	announcement = record_announcement(title=payload.title, body=payload.body, channel=payload.channel)
	users = fetch_rows(USERS_TABLE, limit=5000)
	try:
		send_announcement_notifications(users=users, title=payload.title, body=payload.body)
	except Exception:  # noqa: BLE001
		pass
	return announcement
