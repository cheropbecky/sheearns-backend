from importlib import import_module
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()


def _split_origins(value: str | None) -> list[str]:
	if not value:
		return ["http://localhost:5173", "http://127.0.0.1:5173"]
	return [origin.strip() for origin in value.split(",") if origin.strip()]


app = FastAPI(title="SheEarns API", version="1.0.0")

app.add_middleware(
	CORSMiddleware,
	allow_origins=_split_origins(os.getenv("ALLOWED_ORIGINS")),
	allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://.*\.vercel\.app$",
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


def _register_router(module_path: str, prefix: str, tags: list[str]) -> None:
	module = import_module(module_path)
	router = getattr(module, "router", None)
	if router is not None:
		app.include_router(router, prefix=prefix, tags=tags)


_register_router("routers.ai", "/ai", ["AI"])
_register_router("routers.admin", "/admin", ["Admin"])
_register_router("routers.users", "/auth", ["Auth"])
_register_router("routers.users", "/users", ["Users"])
_register_router("routers.marketplace", "/services", ["Marketplace"])
_register_router("routers.dashboard", "/dashboard", ["Dashboard"])
_register_router("routers.pricing", "/pricing", ["Pricing"])


@app.get("/")
def root() -> dict[str, str]:
	return {"message": "SheEarns API is live", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
	return {"status": "healthy"}
