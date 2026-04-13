from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

_AI_CONVERSATIONS: list[dict[str, Any]] = []
_ANNOUNCEMENTS: list[dict[str, Any]] = []
_HARMFUL_PATTERNS = [
	"hack",
	"exploit",
	"bypass",
	"steal",
	"scam",
	"spam",
	"fake receipt",
	"malware",
	"phish",
	"password",
	"fraud",
]
_STOPWORDS = {
	"a",
	"an",
	"and",
	"ask",
	"for",
	"how",
	"i",
	"in",
	"is",
	"it",
	"my",
	"of",
	"on",
	"the",
	"to",
	"what",
	"with",
	"you",
}


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str) -> str:
	cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower())
	return cleaned


def _extract_question_keywords(question: str) -> str:
	cleaned = re.sub(r"[^a-z0-9\s]", " ", _normalize_text(question))
	words = [word for word in cleaned.split() if word and word not in _STOPWORDS]
	if not words:
		return _normalize_text(question)
	return " ".join(words[:8])


def _detect_misuse(question: str) -> dict[str, str | bool]:
	normalized = _normalize_text(question)
	for pattern in _HARMFUL_PATTERNS:
		if pattern in normalized:
			return {
				"flagged": True,
				"reason": f"Contains potentially unsafe term: {pattern}",
				"risk_level": "high",
			}

	if any(marker in normalized for marker in ("cheat", "illegal", "fake id", "bypass payment")):
		return {
			"flagged": True,
			"reason": "Looks like a misuse or fraud request",
			"risk_level": "high",
		}

	return {"flagged": False, "reason": "", "risk_level": "low"}


def record_ai_conversation(*, user_id: str | None, question: str, response_source: str | None = None) -> dict[str, Any]:
	monitoring = _detect_misuse(question)
	record = {
		"id": f"ai-{len(_AI_CONVERSATIONS) + 1}",
		"user_id": user_id,
		"question": str(question or "").strip(),
		"question_key": _extract_question_keywords(question),
		"response_source": response_source or "fallback",
		"created_at": _now_iso(),
		"flagged": bool(monitoring["flagged"]),
		"risk_level": monitoring["risk_level"],
		"reason": monitoring["reason"],
	}
	_AI_CONVERSATIONS.append(record)
	return record


def list_ai_conversations() -> list[dict[str, Any]]:
	return list(_AI_CONVERSATIONS)


def get_common_ai_questions(limit: int = 5) -> list[dict[str, Any]]:
	counts = Counter(record["question_key"] for record in _AI_CONVERSATIONS if record.get("question_key"))
	return [{"question": question, "count": count} for question, count in counts.most_common(limit)]


def get_ai_misuse_events() -> list[dict[str, Any]]:
	return [record for record in _AI_CONVERSATIONS if record.get("flagged")]


def record_announcement(*, title: str, body: str, channel: str = "dashboard") -> dict[str, Any]:
	record = {
		"id": f"announcement-{len(_ANNOUNCEMENTS) + 1}",
		"title": title,
		"body": body,
		"channel": channel,
		"created_at": _now_iso(),
	}
	_ANNOUNCEMENTS.append(record)
	return record


def list_announcements() -> list[dict[str, Any]]:
	return list(_ANNOUNCEMENTS)
