from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-this")

_password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
	return _password_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
	return _password_context.verify(plain_password, password_hash)


def create_token(user_id: str, expires_minutes: int | None = None) -> str:
	expiry_minutes = expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES
	expire = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
	payload = {
		"sub": user_id,
		"exp": expire,
	}
	return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
	try:
		payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
	except JWTError as exc:
		raise ValueError("Invalid token") from exc

	user_id = payload.get("sub")
	if not user_id:
		raise ValueError("Token missing subject")

	return str(user_id)
