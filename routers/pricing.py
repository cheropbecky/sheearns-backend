from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

import services.pricing_service as pricing_service


router = APIRouter()


ExperienceLevel = Literal["beginner", "intermediate", "advanced"]
UrgencyLevel = Literal["normal", "rush"]


class PricingCalculateRequest(BaseModel):
	service_name: str = Field(..., min_length=1, max_length=120)
	location: str = Field(..., min_length=1, max_length=120)
	hours: float = Field(default=1, gt=0, le=24)
	experience_level: ExperienceLevel = "beginner"
	urgency: UrgencyLevel = "normal"
	materials_cost: int = Field(default=0, ge=0)


class PricingCalculateResponse(BaseModel):
	service_name: str
	location: str
	starting_price: int
	recommended_price: int
	premium_price: int
	notes: list[str]


class NegotiationTipsRequest(BaseModel):
	service_name: str = Field(..., min_length=1, max_length=120)
	client_budget: int = Field(..., gt=0)
	your_minimum: int = Field(..., gt=0)


@router.post("/calculate", response_model=PricingCalculateResponse)
def calculate_pricing(payload: PricingCalculateRequest) -> PricingCalculateResponse:
	result = pricing_service.calculate_pricing(
		service_name=payload.service_name,
		location=payload.location,
		hours=payload.hours,
		experience_level=payload.experience_level,
		urgency=payload.urgency,
		materials_cost=payload.materials_cost,
	)
	return PricingCalculateResponse(**result)


@router.get("/packages")
def package_templates() -> dict[str, list[dict[str, Any]]]:
	return pricing_service.package_templates()


@router.post("/negotiation-tips")
def negotiation_tips(payload: NegotiationTipsRequest) -> dict[str, Any]:
	return pricing_service.negotiation_tips(
		client_budget=payload.client_budget,
		your_minimum=payload.your_minimum,
	)
