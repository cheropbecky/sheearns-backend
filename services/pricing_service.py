from __future__ import annotations

from typing import Any, Literal


ExperienceLevel = Literal["beginner", "intermediate", "advanced"]
UrgencyLevel = Literal["normal", "rush"]


def experience_multiplier(level: ExperienceLevel) -> float:
	return {
		"beginner": 1.0,
		"intermediate": 1.25,
		"advanced": 1.55,
	}[level]


def location_multiplier(location: str) -> float:
	major_markets = {"nairobi", "mombasa", "nakuru", "kisumu"}
	return 1.15 if location.strip().lower() in major_markets else 1.0


def urgency_multiplier(urgency: UrgencyLevel) -> float:
	return 1.25 if urgency == "rush" else 1.0


def calculate_pricing(
	*,
	service_name: str,
	location: str,
	hours: float,
	experience_level: ExperienceLevel,
	urgency: UrgencyLevel,
	materials_cost: int = 0,
	base_hourly: int = 650,
) -> dict[str, Any]:
	base = base_hourly * hours
	exp_mult = experience_multiplier(experience_level)
	loc_mult = location_multiplier(location)
	urgency_mult = urgency_multiplier(urgency)

	recommended = int(base * exp_mult * loc_mult * urgency_mult) + materials_cost
	starting = int(recommended * 0.8)
	premium = int(recommended * 1.35)

	notes = [
		f"Based on {hours} hour(s) of work.",
		f"Experience level applied: {experience_level}.",
	]
	if urgency == "rush":
		notes.append("Rush multiplier applied for urgent delivery.")
	if materials_cost > 0:
		notes.append(f"Includes materials cost of Ksh {materials_cost:,}.")

	return {
		"service_name": service_name,
		"location": location,
		"starting_price": max(starting, 1),
		"recommended_price": max(recommended, 1),
		"premium_price": max(premium, 1),
		"notes": notes,
	}


def package_templates() -> dict[str, list[dict[str, Any]]]:
	return {
		"packages": [
			{
				"name": "Starter",
				"description": "Entry-level offer to attract first clients.",
				"pricing_rule": "Set between 80% and 95% of recommended price.",
			},
			{
				"name": "Standard",
				"description": "Core package for repeatable income.",
				"pricing_rule": "Use recommended price with clear deliverables.",
			},
			{
				"name": "Premium",
				"description": "High-touch package with faster delivery and extras.",
				"pricing_rule": "Set 125% to 150% of recommended price.",
			},
		]
	}


def negotiation_tips(*, client_budget: int, your_minimum: int) -> dict[str, Any]:
	if client_budget < your_minimum:
		gap = your_minimum - client_budget
		return {
			"status": "below_minimum",
			"gap": gap,
			"tips": [
				"Offer a smaller scope that fits the client's budget.",
				"Keep quality standards clear and avoid underpricing yourself.",
				"Propose installment payments if scope cannot be reduced.",
			],
		}

	return {
		"status": "workable",
		"gap": client_budget - your_minimum,
		"tips": [
			"Restate your value before confirming price.",
			"Define exactly what is included to avoid scope creep.",
			"Confirm timeline and payment terms in writing.",
		],
	}
