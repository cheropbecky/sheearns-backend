from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status

from models.booking import BookingCreate
from models.review import ReviewCreate
from models.service import ServiceCreate, ServiceUpdate
from services.auth_service import verify_token
from services.supabase_service import fetch_row, fetch_rows, get_supabase_client, insert_row, update_rows


router = APIRouter()

SERVICES_TABLE = "services"
REVIEWS_TABLE = "reviews"
BOOKINGS_TABLE = "bookings"
USERS_TABLE = "users"

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


def _service_summary(
	service: dict[str, Any],
	*,
	provider_name: str | None = None,
	provider_phone: str | None = None,
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
		"created_at": service.get("created_at"),
	}


def _get_provider_meta(user_id: str) -> dict[str, str | None]:
	provider = fetch_row(USERS_TABLE, filters={"id": user_id})
	if provider is None:
		return {"name": None, "phone": None}
	return {
		"name": provider.get("full_name"),
		"phone": provider.get("phone"),
	}


@router.get("")
def list_services(
	category: str | None = Query(default=None),
	location: str | None = Query(default=None),
	min_price: int | None = Query(default=None, ge=0),
	max_price: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
	_require_db()
	services = fetch_rows(SERVICES_TABLE, filters={"is_active": True}, limit=1000)

	if category and category != "All":
		services = [service for service in services if service["category"] == category]
	if location:
		services = [service for service in services if location.lower() in service["location"].lower()]
	if min_price is not None:
		services = [service for service in services if int(service.get("price_max") or 0) >= min_price]
	if max_price is not None:
		services = [service for service in services if int(service.get("price_min") or 0) <= max_price]

	result: list[dict[str, Any]] = []
	for service in sorted(services, key=lambda item: item.get("created_at") or "", reverse=True):
		provider_meta = _get_provider_meta(service["user_id"])
		result.append(
			_service_summary(
				service,
				provider_name=provider_meta["name"],
				provider_phone=provider_meta["phone"],
			)
		)

	return result


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
	)


@router.get("/{service_id}")
def get_service(service_id: str) -> dict[str, Any]:
	_require_db()
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if not service:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
	reviews = fetch_rows(REVIEWS_TABLE, filters={"service_id": service_id}, limit=500)
	reviews_sorted = sorted(reviews, key=lambda item: item.get("created_at") or "", reverse=True)
	provider_meta = _get_provider_meta(service["user_id"])

	return {
		**_service_summary(
			service,
			provider_name=provider_meta["name"],
			provider_phone=provider_meta["phone"],
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

	update_rows(SERVICES_TABLE, filters={"id": service_id}, payload={"is_active": False})


@router.post("/{service_id}/review", status_code=status.HTTP_201_CREATED)
def submit_review(service_id: str, payload: ReviewCreate) -> dict[str, Any]:
	_require_db()
	service = fetch_row(SERVICES_TABLE, filters={"id": service_id, "is_active": True})
	if service is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

	review = {
		"id": str(uuid4()),
		"service_id": service_id,
		"reviewer_name": payload.reviewer_name,
		"rating": payload.rating,
		"comment": payload.comment,
		"created_at": _now_iso(),
	}
	stored_review = insert_row(REVIEWS_TABLE, review)
	if stored_review is None:
		raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to save review")

	reviews = fetch_rows(REVIEWS_TABLE, filters={"service_id": service_id}, limit=1000)
	review_count = len(reviews)
	average_rating = round(sum(int(item.get("rating") or 0) for item in reviews) / review_count, 1) if review_count else 0.0
	update_rows(
		SERVICES_TABLE,
		filters={"id": service_id},
		payload={"rating": average_rating, "review_count": review_count},
	)

	return stored_review


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

	return stored_booking

