from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status

from models.booking import BookingArchiveUpdate, BookingCreate, BookingStatusUpdate
from models.review import ReviewCreate, ReviewUpdate
from models.service import ServiceCreate, ServiceUpdate
from services.auth_service import verify_token
from services.notification_service import send_booking_notifications
from services.supabase_service import delete_rows, fetch_row, fetch_rows, get_supabase_client, insert_row, update_rows

try:
	from postgrest.exceptions import APIError
except Exception:  # noqa: BLE001
	APIError = Exception


router = APIRouter()

SERVICES_TABLE = "services"
REVIEWS_TABLE = "reviews"
BOOKINGS_TABLE = "bookings"
USERS_TABLE = "users"
ALLOWED_BOOKING_STATUSES = {"pending", "accepted", "rejected", "completed", "cancelled"}
ARCHIVE_MARKER = "[[__SHEEARNS_ARCHIVED_BY_PROVIDER__]]"
DEFAULT_APPROVAL_STATUS = "pending"

def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _require_db() -> None:
	if get_supabase_client() is None:
		raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not configured")


def _require_user_id(authorization: str | None) -> str:
	if not authorization:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization token")

	token = authorization.replace("Bearer ", "", 1)
	try:
		return verify_token(token)
	except Exception as exc:  # noqa: BLE001
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


def _optional_user_id(authorization: str | None) -> str | None:
	if not authorization:
		return None
	token = authorization.replace("Bearer ", "", 1)
	try:
		return verify_token(token)
	except Exception:  # noqa: BLE001
		return None


def _service_summary(
	service: dict[str, Any],
	*,
	provider_name: str | None = None,
	provider_phone: str | None = None,
	provider_avatar_url: str | None = None,
) -> dict[str, Any]:
	price_min = int(service.get("price_min") or 0)
	price_max = int(service.get("price_max") or 0)
	review_count = int(service.get("review_count") or 0)
	rating = float(service.get("rating") or 0)

	return {
		"id": service["id"],
		"user_id": service["user_id"],
		"provider_name": provider_name,
		"provider_phone": provider_phone,
		"avatar_url": provider_avatar_url,
		"title": service["title"],
		"category": service["category"],
		"description": service["description"],
		"price_min": price_min,
		"price_max": price_max,
		"location": service["location"],
		"portfolio_urls": service.get("portfolio_urls") or [],
		"rating": round(rating, 1),
		"review_count": review_count,
		"is_active": bool(service.get("is_active", True)),
		"approval_status": str(service.get("approval_status") or DEFAULT_APPROVAL_STATUS),
		"created_at": service.get("created_at"),
	}


def _is_visible_service(service: dict[str, Any]) -> bool:
	approval_status = str(service.get("approval_status") or "approved").lower()
	return bool(service.get("is_active", True)) and approval_status == "approved"


def _get_provider_meta(user_id: str) -> dict[str, str | None]:
	provider = fetch_row(USERS_TABLE, filters={"id": user_id})
	if provider is None:
		return {"name": None, "phone": None, "avatar_url": None}
	return {
		"name": provider.get("full_name"),
		"phone": provider.get("phone"),
		"avatar_url": provider.get("avatar_url"),
	}


def _booking_summary(booking: dict[str, Any]) -> dict[str, Any]:
	service = fetch_row(SERVICES_TABLE, filters={"id": booking["service_id"]}) or {}
	customer = fetch_row(USERS_TABLE, filters={"id": booking["customer_user_id"]}) or {}
	provider = fetch_row(USERS_TABLE, filters={"id": booking["provider_user_id"]}) or {}
	message, marker_archived = _split_message_archive_marker(booking.get("message"))
	archived_by_provider = _resolve_booking_archived_flag(booking, marker_archived)

	return {
		"id": booking["id"],
		"service_id": booking["service_id"],
		"service_title": service.get("title"),
		"customer_user_id": booking["customer_user_id"],
		"customer_name": customer.get("full_name"),
		"customer_email": customer.get("email"),
		"provider_user_id": booking["provider_user_id"],
		"provider_name": provider.get("full_name"),
		"provider_email": provider.get("email"),
		"status": str(booking.get("status") or "pending"),
		"amount": int(booking.get("amount") or 0),
		"message": message,
		"archived_by_provider": archived_by_provider,
		"created_at": booking.get("created_at"),
	}


def _split_message_archive_marker(message: Any) -> tuple[str | None, bool]:
	raw_message = "" if message is None else str(message)
	marker_archived = ARCHIVE_MARKER in raw_message
	if marker_archived:
		cleaned = raw_message.replace(ARCHIVE_MARKER, "").strip()
		return (cleaned if cleaned else None, True)
	return (message if message is None or isinstance(message, str) else str(message), False)


def _resolve_booking_archived_flag(booking: dict[str, Any], marker_archived: bool) -> bool:
	if "archived_by_provider" in booking and booking.get("archived_by_provider") is not None:
		return bool(booking.get("archived_by_provider"))
	return marker_archived


def _message_with_archive_marker(message: Any, archived: bool) -> str | None:
	cleaned_message, _ = _split_message_archive_marker(message)
	if archived:
		if cleaned_message:
			return f"{cleaned_message}\n{ARCHIVE_MARKER}"
		return ARCHIVE_MARKER
	return cleaned_message


def _is_missing_archive_column_error(exc: Exception) -> bool:
	if not isinstance(exc, APIError):
		return False
	code = str(getattr(exc, "code", "") or "")
	message = str(getattr(exc, "message", "") or exc)
	return code == "PGRST204" and "archived_by_provider" in message


def _is_missing_reviewer_user_id_column_error(exc: Exception) -> bool:
	if not isinstance(exc, APIError):
		return False
	code = str(getattr(exc, "code", "") or "")
	message = str(getattr(exc, "message", "") or exc)
	return code == "PGRST204" and "reviewer_user_id" in message


def _is_missing_approval_status_column_error(exc: Exception) -> bool:
	if not isinstance(exc, APIError):
		return False
	code = str(getattr(exc, "code", "") or "")
	message = str(getattr(exc, "message", "") or exc)
	return code == "PGRST204" and "approval_status" in message


def _deactivate_service(service_id: str) -> bool:
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


def _update_booking_archive_flag(booking: dict[str, Any], archived: bool) -> dict[str, Any] | None:
	try:
		updated = update_rows(
			BOOKINGS_TABLE,
			filters={"id": booking["id"]},
			payload={"archived_by_provider": archived},
		)
		return updated[0] if updated else None
	except Exception as exc:  # noqa: BLE001
		if not _is_missing_archive_column_error(exc):
			raise

	message_payload = _message_with_archive_marker(booking.get("message"), archived)
	fallback_updated = update_rows(
		BOOKINGS_TABLE,
		filters={"id": booking["id"]},
		payload={"message": message_payload},
	)
	return fallback_updated[0] if fallback_updated else None


def _refresh_service_rating(service_id: str) -> None:
	reviews = fetch_rows(REVIEWS_TABLE, filters={"service_id": service_id}, limit=1000)
	review_count = len(reviews)
	average_rating = round(sum(int(item.get("rating") or 0) for item in reviews) / review_count, 1) if review_count else 0.0
	update_rows(
		SERVICES_TABLE,
		filters={"id": service_id},
		payload={"rating": average_rating, "review_count": review_count},
	)


@router.get("")
def list_services(
	category: str | None = Query(default=None),
	location: str | None = Query(default=None),
	min_price: int | None = Query(default=None, ge=0),
	max_price: int | None = Query(default=None, ge=0),
	q: str | None = Query(default=None, max_length=120),
) -> list[dict[str, Any]]:
	_require_db()
	services = [service for service in fetch_rows(SERVICES_TABLE, limit=1000) if _is_visible_service(service)]

	if category and category != "All":
		services = [service for service in services if service["category"] == category]
	if location:
		services = [service for service in services if location.lower() in service["location"].lower()]
	if min_price is not None:
		services = [service for service in services if int(service.get("price_max") or 0) >= min_price]
	if max_price is not None:
		services = [service for service in services if int(service.get("price_min") or 0) <= max_price]

	result: list[dict[str, Any]] = []
	query = q.strip().lower() if q else ""
	for service in sorted(services, key=lambda item: item.get("created_at") or "", reverse=True):
		provider_meta = _get_provider_meta(service["user_id"])
		if query:
			search_blob = " ".join(
				str(value or "")
				for value in (
					service.get("title"),
					service.get("category"),
					service.get("description"),
					service.get("location"),
					provider_meta["name"],
				)
			).lower()
			if query not in search_blob:
				continue
		result.append(
			_service_summary(
				service,
				provider_name=provider_meta["name"],
				provider_phone=provider_meta["phone"],
				provider_avatar_url=provider_meta["avatar_url"],
			)
		)

	return result


@router.get("/me")
def list_my_services(authorization: Annotated[str | None, Header()] = None) -> list[dict[str, Any]]:
	_require_db()
	user_id = _require_user_id(authorization)
	services = fetch_rows(SERVICES_TABLE, filters={"user_id": user_id, "is_active": True}, limit=1000)
	provider_meta = _get_provider_meta(user_id)

	return [
		_service_summary(
			service,
			provider_name=provider_meta["name"],
			provider_phone=provider_meta["phone"],
			provider_avatar_url=provider_meta["avatar_url"],
		)
		for service in sorted(services, key=lambda item: item.get("created_at") or "", reverse=True)
	]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_service(
	payload: ServiceCreate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	if payload.price_min > payload.price_max:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_min cannot be greater than price_max")
	provider_user_id = _require_user_id(authorization)

	service_id = str(uuid4())
	service = {
		"id": service_id,
		"user_id": provider_user_id,
		"title": payload.title,
		"category": payload.category,
		"description": payload.description,
		"price_min": payload.price_min,
		"price_max": payload.price_max,
		"location": payload.location,
		"portfolio_urls": payload.portfolio_urls,
		"rating": 0.0,
		"review_count": 0,
		"is_active": True,
		"approval_status": DEFAULT_APPROVAL_STATUS,
		"created_at": _now_iso(),
	}
	created = insert_row(SERVICES_TABLE, service)
	if created is None:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to create service")
	provider_meta = _get_provider_meta(provider_user_id)
	return _service_summary(
		created,
		provider_name=provider_meta["name"],
		provider_phone=provider_meta["phone"],
		provider_avatar_url=provider_meta["avatar_url"],
	)


@router.get("/{service_id}")
def get_service(service_id: str) -> dict[str, Any]:
	_require_db()
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if not service or not _is_visible_service(service):
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	reviews = fetch_rows(REVIEWS_TABLE, filters={"service_id": service_id}, limit=500)
	reviews_sorted = sorted(reviews, key=lambda item: item.get("created_at") or "", reverse=True)
	provider_meta = _get_provider_meta(service["user_id"])

	return {
		**_service_summary(
			service,
			provider_name=provider_meta["name"],
			provider_phone=provider_meta["phone"],
			provider_avatar_url=provider_meta["avatar_url"],
		),
		"reviews": reviews_sorted,
	}


@router.put("/{service_id}")
def update_service(
	service_id: str,
	payload: ServiceUpdate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id})
	if not service:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	if service["user_id"] != actor_user_id:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own service")

	updates = payload.model_dump(exclude_unset=True)
	if "price_min" in updates and "price_max" in updates and updates["price_min"] > updates["price_max"]:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_min cannot be greater than price_max")
	if "price_min" in updates and "price_max" not in updates and int(updates["price_min"]) > int(service.get("price_max") or 0):
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_min cannot be greater than price_max")
	if "price_max" in updates and "price_min" not in updates and int(service.get("price_min") or 0) > int(updates["price_max"]):
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="price_min cannot be greater than price_max")

	updated = update_rows(SERVICES_TABLE, filters={"id": service_id}, payload=updates)
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update service")
	provider_meta = _get_provider_meta(service["user_id"])
	return _service_summary(
		updated[0],
		provider_name=provider_meta["name"],
		provider_phone=provider_meta["phone"],
		provider_avatar_url=provider_meta["avatar_url"],
	)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: str, authorization: Annotated[str | None, Header()] = None) -> None:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	if service["user_id"] != actor_user_id:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own service")

	try:
		deleted = delete_rows(SERVICES_TABLE, filters={"id": service_id})
	except Exception:  # noqa: BLE001
		deleted = []

	if deleted:
		return

	if not _deactivate_service(service_id):
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to delete service")


@router.post("/{service_id}/review", status_code=status.HTTP_201_CREATED)
def submit_review(
	service_id: str,
	payload: ReviewCreate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	reviewer_user_id = _optional_user_id(authorization)

	review = {
		"id": str(uuid4()),
		"service_id": service_id,
		"reviewer_user_id": reviewer_user_id,
		"reviewer_name": payload.reviewer_name,
		"rating": payload.rating,
		"comment": payload.comment,
		"created_at": _now_iso(),
	}
	try:
		stored_review = insert_row(REVIEWS_TABLE, review)
	except Exception as exc:  # noqa: BLE001
		if not _is_missing_reviewer_user_id_column_error(exc):
			raise
		stored_review = insert_row(
			REVIEWS_TABLE,
			{
				key: value
				for key, value in review.items()
				if key != "reviewer_user_id"
			},
		)
	if stored_review is None:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to save review")
	_refresh_service_rating(service_id)

	return stored_review


@router.put("/{service_id}/review/{review_id}")
def update_review(
	service_id: str,
	review_id: str,
	payload: ReviewUpdate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	review = fetch_row(REVIEWS_TABLE, filters={"id": review_id, "service_id": service_id})
	if review is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

	reviewer_user_id = review.get("reviewer_user_id")
	is_reviewer = bool(reviewer_user_id and reviewer_user_id == actor_user_id)
	is_provider = service["user_id"] == actor_user_id
	if not is_reviewer and not is_provider:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot edit this review")

	updates = payload.model_dump(exclude_unset=True)
	if not updates:
		return review

	updated = update_rows(REVIEWS_TABLE, filters={"id": review_id, "service_id": service_id}, payload=updates)
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update review")

	_refresh_service_rating(service_id)
	return updated[0]


@router.delete("/{service_id}/review/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(
	service_id: str,
	review_id: str,
	authorization: Annotated[str | None, Header()] = None,
) -> None:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	review = fetch_row(REVIEWS_TABLE, filters={"id": review_id, "service_id": service_id})
	if review is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

	reviewer_user_id = review.get("reviewer_user_id")
	is_reviewer = bool(reviewer_user_id and reviewer_user_id == actor_user_id)
	is_provider = service["user_id"] == actor_user_id
	if not is_reviewer and not is_provider:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot delete this review")

	deleted = delete_rows(REVIEWS_TABLE, filters={"id": review_id, "service_id": service_id})
	if not deleted:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to delete review")

	_refresh_service_rating(service_id)


@router.post("/{service_id}/book", status_code=status.HTTP_201_CREATED)
def create_booking(
	service_id: str,
	payload: BookingCreate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	customer_user_id = _require_user_id(authorization)
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

	provider_user_id = service["user_id"]
	if provider_user_id == customer_user_id:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot book your own service")

	booking = {
		"id": str(uuid4()),
		"service_id": service_id,
		"customer_user_id": customer_user_id,
		"provider_user_id": provider_user_id,
		"status": "pending",
		"amount": int(service.get("price_min") or 0),
		"message": payload.message,
		"created_at": _now_iso(),
	}

	stored_booking = insert_row(BOOKINGS_TABLE, booking)
	if stored_booking is None:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to create booking")

	customer = fetch_row(USERS_TABLE, filters={"id": customer_user_id})
	provider = fetch_row(USERS_TABLE, filters={"id": provider_user_id})
	send_booking_notifications(
		booking=stored_booking,
		service=service,
		customer=customer,
		provider=provider,
	)

	return stored_booking


@router.get("/bookings/provider")
def list_provider_bookings(
	include_archived: bool = Query(default=False),
	authorization: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
	_require_db()
	provider_user_id = _require_user_id(authorization)
	bookings = fetch_rows(BOOKINGS_TABLE, filters={"provider_user_id": provider_user_id}, limit=1000)
	if not include_archived:
		bookings = [
			booking
			for booking in bookings
			if not _resolve_booking_archived_flag(booking, _split_message_archive_marker(booking.get("message"))[1])
		]
	bookings_sorted = sorted(bookings, key=lambda item: item.get("created_at") or "", reverse=True)
	return [_booking_summary(item) for item in bookings_sorted]


@router.get("/bookings/me")
def list_customer_bookings(authorization: Annotated[str | None, Header()] = None) -> list[dict[str, Any]]:
	_require_db()
	customer_user_id = _require_user_id(authorization)
	bookings = fetch_rows(BOOKINGS_TABLE, filters={"customer_user_id": customer_user_id}, limit=1000)
	bookings_sorted = sorted(bookings, key=lambda item: item.get("created_at") or "", reverse=True)
	return [_booking_summary(item) for item in bookings_sorted]


@router.patch("/bookings/{booking_id}/status")
def update_booking_status(
	booking_id: str,
	payload: BookingStatusUpdate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	booking = fetch_row(BOOKINGS_TABLE, filters={"id": booking_id})
	if booking is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

	current_status = str(booking.get("status") or "pending")
	next_status = payload.status
	if next_status not in ALLOWED_BOOKING_STATUSES:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid booking status")

	provider_user_id = booking["provider_user_id"]
	customer_user_id = booking["customer_user_id"]

	is_provider = actor_user_id == provider_user_id
	is_customer = actor_user_id == customer_user_id
	if not is_provider and not is_customer:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not allowed to modify this booking")

	if is_provider:
		allowed_transitions = {
			"pending": {"accepted", "rejected"},
			"accepted": {"completed", "cancelled"},
			"rejected": set(),
			"completed": set(),
			"cancelled": set(),
		}
	else:
		allowed_transitions = {
			"pending": {"cancelled"},
			"accepted": {"cancelled"},
			"rejected": set(),
			"completed": set(),
			"cancelled": set(),
		}

	if next_status == current_status:
		return _booking_summary(booking)

	if next_status not in allowed_transitions.get(current_status, set()):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail=f"Cannot move booking from {current_status} to {next_status}",
		)

	updated = update_rows(BOOKINGS_TABLE, filters={"id": booking_id}, payload={"status": next_status})
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update booking status")

	return _booking_summary(updated[0])


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_booking(
	booking_id: str,
	authorization: Annotated[str | None, Header()] = None,
) -> None:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	booking = fetch_row(BOOKINGS_TABLE, filters={"id": booking_id})
	if booking is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
	if booking["provider_user_id"] != actor_user_id:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only hide your own booking requests")

	updated = _update_booking_archive_flag(booking, True)
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to hide booking")


@router.patch("/bookings/{booking_id}/archive")
def update_booking_archive_status(
	booking_id: str,
	payload: BookingArchiveUpdate,
	authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
	_require_db()
	actor_user_id = _require_user_id(authorization)
	booking = fetch_row(BOOKINGS_TABLE, filters={"id": booking_id})
	if booking is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
	if booking["provider_user_id"] != actor_user_id:
		raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only update your own booking requests")

	updated = _update_booking_archive_flag(booking, bool(payload.archived))
	if not updated:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to update archive status")

	return _booking_summary(updated[0])

