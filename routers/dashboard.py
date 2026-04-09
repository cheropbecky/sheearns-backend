from __future__ import annotations

from typing import Annotated
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from models.income import IncomeSummary, Milestone
from services.auth_service import verify_token
from services.supabase_service import fetch_rows, get_supabase_client


router = APIRouter()

BOOKINGS_TABLE = "bookings"
SERVICES_TABLE = "services"


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


def _income_summary(total: int, jobs_count: int, monthly_goal: int = 10000) -> IncomeSummary:
	remaining = max(monthly_goal - total, 0)
	progress = round((total / monthly_goal) * 100, 2) if monthly_goal > 0 else 0

	return IncomeSummary(
		monthly_goal=monthly_goal,
		earned=total,
		remaining=remaining,
		progress_percent=min(progress, 100),
		log_count=jobs_count,
	)


def _milestones(earned: int, jobs_count: int) -> list[Milestone]:

	return [
		Milestone(key="first_client", label="First Client Landed", unlocked=jobs_count >= 1, target="1 job"),
		Milestone(key="earned_5k", label="Ksh 5,000 Earned", unlocked=earned >= 5000, target="Ksh 5,000"),
		Milestone(key="earned_10k", label="Ksh 10,000 Month", unlocked=earned >= 10000, target="Ksh 10,000"),
		Milestone(key="ten_clients", label="10 Clients Served", unlocked=jobs_count >= 10, target="10 jobs"),
	]


@router.get("")
def get_dashboard(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
	_require_db()
	user_id = _require_user_id(authorization)

	provider_bookings = fetch_rows(BOOKINGS_TABLE, filters={"provider_user_id": user_id}, limit=1000)
	provider_bookings_sorted = sorted(provider_bookings, key=lambda item: item.get("created_at") or "", reverse=True)

	completed_statuses = {"completed", "confirmed", "paid"}
	earnings_records = [booking for booking in provider_bookings if str(booking.get("status", "")).lower() in completed_statuses]
	total_earned = sum(int(item.get("amount") or 0) for item in earnings_records)
	jobs_count = len(provider_bookings)
	summary = _income_summary(total_earned, jobs_count)
	milestones = _milestones(total_earned, jobs_count)

	services = fetch_rows(SERVICES_TABLE, filters={"user_id": user_id, "is_active": True}, limit=1000)
	service_map = {item["id"]: item for item in services}

	activity = [
		{
			"type": "booking",
			"title": f"Booking request: {service_map.get(entry['service_id'], {}).get('title', 'Service')}",
			"subtitle": f"Status: {entry.get('status', 'pending').title()} | Amount: Ksh {int(entry.get('amount') or 0):,}",
			"timestamp": entry.get("created_at"),
		}
		for entry in provider_bookings_sorted[:5]
	]

	return {
		"summary": summary.model_dump(),
		"milestones": [item.model_dump() for item in milestones],
		"recent_activity": activity,
		"bookings": provider_bookings_sorted,
		"earnings_records": sorted(earnings_records, key=lambda item: item.get("created_at") or "", reverse=True),
	}
