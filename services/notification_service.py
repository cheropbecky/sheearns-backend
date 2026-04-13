from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx


logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def is_email_notifications_configured() -> bool:
	return _is_smtp_configured() or _is_resend_configured()


def _is_resend_configured() -> bool:
	return bool(os.getenv("RESEND_API_KEY") and os.getenv("EMAIL_FROM"))


def _is_smtp_configured() -> bool:
	return bool(
		os.getenv("SMTP_HOST")
		and os.getenv("SMTP_PORT")
		and os.getenv("SMTP_USERNAME")
		and os.getenv("SMTP_PASSWORD")
		and os.getenv("EMAIL_FROM")
	)


def _send_email_via_smtp(*, to_email: str, subject: str, html: str) -> None:
	host = os.getenv("SMTP_HOST")
	port = int(os.getenv("SMTP_PORT", "587"))
	username = os.getenv("SMTP_USERNAME")
	password = os.getenv("SMTP_PASSWORD")
	email_from = os.getenv("EMAIL_FROM")

	if not host or not username or not password or not email_from:
		raise RuntimeError("SMTP email notifications are not configured")

	msg = EmailMessage()
	msg["Subject"] = subject
	msg["From"] = email_from
	msg["To"] = to_email
	msg.set_content("This email contains HTML content. Please view it in an HTML-capable client.")
	msg.add_alternative(html, subtype="html")

	with smtplib.SMTP(host, port, timeout=10) as server:
		server.starttls()
		server.login(username, password)
		server.send_message(msg)


def _send_email_via_resend(*, to_email: str, subject: str, html: str) -> None:
	api_key = os.getenv("RESEND_API_KEY")
	email_from = os.getenv("EMAIL_FROM")
	if not api_key or not email_from:
		raise RuntimeError("Email notifications are not configured")

	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
	}
	payload = {
		"from": email_from,
		"to": [to_email],
		"subject": subject,
		"html": html,
	}

	with httpx.Client(timeout=8.0) as client:
		response = client.post(RESEND_API_URL, headers=headers, json=payload)
		if response.is_error:
			raise RuntimeError(f"Resend API error {response.status_code}: {response.text}")


def _send_email(*, to_email: str, subject: str, html: str) -> None:
	if _is_smtp_configured():
		_send_email_via_smtp(to_email=to_email, subject=subject, html=html)
		return

	if _is_resend_configured():
		_send_email_via_resend(to_email=to_email, subject=subject, html=html)
		return

	raise RuntimeError("No email provider is configured")


def _notifications_enabled(user: dict[str, Any] | None) -> bool:
	if not user:
		return False
	return bool(user.get("notifications_enabled", True))


def send_booking_notifications(
	*,
	booking: dict[str, Any],
	service: dict[str, Any],
	customer: dict[str, Any] | None,
	provider: dict[str, Any] | None,
) -> None:
	if not is_email_notifications_configured():
		logger.info("Skipping booking emails: SMTP and Resend are both not configured")
		return

	service_title = str(service.get("title") or "Service")
	service_location = str(service.get("location") or "Online")
	amount = int(booking.get("amount") or 0)
	status = str(booking.get("status") or "pending").capitalize()
	message = str(booking.get("message") or "")

	customer_email = str((customer or {}).get("email") or "").strip()
	provider_email = str((provider or {}).get("email") or "").strip()
	customer_name = str((customer or {}).get("full_name") or "there")
	provider_name = str((provider or {}).get("full_name") or "there")

	if customer_email and _notifications_enabled(customer):
		customer_html = (
			f"<h2>Booking confirmed</h2>"
			f"<p>Hi {customer_name}, your booking request has been created.</p>"
			f"<p><strong>Service:</strong> {service_title}</p>"
			f"<p><strong>Location:</strong> {service_location}</p>"
			f"<p><strong>Amount:</strong> {amount}</p>"
			f"<p><strong>Status:</strong> {status}</p>"
			f"<p><strong>Your message:</strong> {message or 'No message added.'}</p>"
		)
		try:
			_send_email(
				to_email=customer_email,
				subject=f"Booking request received: {service_title}",
				html=customer_html,
			)
		except Exception:  # noqa: BLE001
			logger.exception("Failed to send booking confirmation email to customer")

	if provider_email and _notifications_enabled(provider):
		provider_html = (
			f"<h2>You have a new booking request</h2>"
			f"<p>Hi {provider_name}, someone booked your service.</p>"
			f"<p><strong>Service:</strong> {service_title}</p>"
			f"<p><strong>Location:</strong> {service_location}</p>"
			f"<p><strong>Amount:</strong> {amount}</p>"
			f"<p><strong>Status:</strong> {status}</p>"
			f"<p><strong>Client message:</strong> {message or 'No message added.'}</p>"
		)
		try:
			_send_email(
				to_email=provider_email,
				subject=f"New booking request: {service_title}",
				html=provider_html,
			)
		except Exception:  # noqa: BLE001
			logger.exception("Failed to send booking alert email to provider")


def send_announcement_notifications(*, users: list[dict[str, Any]], title: str, body: str) -> None:
	if not is_email_notifications_configured():
		logger.info("Skipping announcement emails: SMTP and Resend are both not configured")
		return

	for user in users:
		if not _notifications_enabled(user):
			continue
		email = str(user.get("email") or "").strip()
		if not email:
			continue
		name = str(user.get("full_name") or "there")
		html = (
			f"<h2>{title}</h2>"
			f"<p>Hi {name},</p>"
			f"<p>{body}</p>"
		)
		try:
			_send_email(
				to_email=email,
				subject=title,
				html=html,
			)
		except Exception:  # noqa: BLE001
			logger.exception("Failed to send announcement email to %s", email)
