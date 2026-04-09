from __future__ import annotations

import os
from typing import Any


def is_groq_configured() -> bool:
	return bool(os.getenv("GROQ_API_KEY"))


def is_openai_configured() -> bool:
	return bool(os.getenv("OPENAI_API_KEY"))


def is_azure_openai_configured() -> bool:
	return bool(
		os.getenv("AZURE_OPENAI_API_KEY")
		and os.getenv("AZURE_OPENAI_ENDPOINT")
		and os.getenv("AZURE_OPENAI_DEPLOYMENT")
	)


def _azure_openai_api_version() -> str:
	return os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")


def _fallback_reply(user_prompt: str) -> str:
	return (
		"I can help with that. Based on your goal, start with one clear offer, "
		"post it where your target clients are, and follow up with 10 direct messages. "
		f"Your prompt was: {user_prompt}"
	)


async def _generate_groq_reply(
	*,
	user_prompt: str,
	history: list[dict[str, str]] | None = None,
	system_prompt: str | None = None,
	model: str | None = None,
) -> dict[str, Any]:
	if not is_groq_configured():
		raise RuntimeError("Groq not configured")

	try:
		from openai import AsyncOpenAI
	except Exception as exc:
		raise RuntimeError("OpenAI SDK missing for Groq client") from exc

	selected_model = model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
	client = AsyncOpenAI(
		api_key=os.getenv("GROQ_API_KEY"),
		base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
	)

	messages: list[dict[str, str]] = []
	if system_prompt:
		messages.append({"role": "system", "content": system_prompt})
	if history:
		messages.extend(history)
	messages.append({"role": "user", "content": user_prompt})

	response = await client.chat.completions.create(
		model=selected_model,
		messages=messages,
		temperature=0.6,
		max_tokens=700,
	)

	text = (response.choices[0].message.content or "").strip()
	if not text:
		raise RuntimeError("Empty Groq response")

	return {"reply": text, "source": "groq", "model": selected_model}


async def _generate_azure_openai_reply(
	*,
	user_prompt: str,
	history: list[dict[str, str]] | None = None,
	system_prompt: str | None = None,
	model: str | None = None,
) -> dict[str, Any]:
	if not is_azure_openai_configured():
		raise RuntimeError("Azure OpenAI not configured")

	try:
		from openai import AsyncAzureOpenAI
	except Exception as exc:
		raise RuntimeError("OpenAI SDK missing Azure client") from exc

	deployment = model or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
	endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
	api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
	api_version = _azure_openai_api_version()

	client = AsyncAzureOpenAI(
		api_key=api_key,
		azure_endpoint=endpoint,
		api_version=api_version,
	)

	messages: list[dict[str, str]] = []
	if system_prompt:
		messages.append({"role": "system", "content": system_prompt})
	if history:
		messages.extend(history)
	messages.append({"role": "user", "content": user_prompt})

	response = await client.chat.completions.create(
		model=deployment,
		messages=messages,
		temperature=0.6,
		max_tokens=700,
	)

	text = (response.choices[0].message.content or "").strip()
	if not text:
		raise RuntimeError("Empty Azure OpenAI response")

	return {"reply": text, "source": "azure-openai", "model": deployment}


async def generate_chat_reply(
	*,
	user_prompt: str,
	history: list[dict[str, str]] | None = None,
	system_prompt: str | None = None,
	model: str | None = None,
) -> dict[str, Any]:
	# Primary: Groq (free tier friendly)
	try:
		return await _generate_groq_reply(
			user_prompt=user_prompt,
			history=history,
			system_prompt=system_prompt,
			model=model,
		)
	except Exception:
		pass

	# Secondary: Azure OpenAI (Student Pack)
	try:
		return await _generate_azure_openai_reply(
			user_prompt=user_prompt,
			history=history,
			system_prompt=system_prompt,
			model=model,
		)
	except Exception:
		pass

	# Tertiary: Regular OpenAI (if billing available)
	if is_openai_configured():
		try:
			from openai import AsyncOpenAI
		except Exception:
			pass
		else:
			client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
			selected_model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

			messages: list[dict[str, str]] = []
			if system_prompt:
				messages.append({"role": "system", "content": system_prompt})
			if history:
				messages.extend(history)
			messages.append({"role": "user", "content": user_prompt})

			try:
				response = await client.chat.completions.create(
					model=selected_model,
					messages=messages,
					temperature=0.6,
					max_tokens=700,
				)
				text = (response.choices[0].message.content or "").strip()
				if text:
					return {"reply": text, "source": "openai", "model": selected_model}
			except Exception:
				pass

	# Final fallback: Generic response
	return {"reply": _fallback_reply(user_prompt), "source": "fallback"}
