"""Router package for SheEarns API."""

from .ai import router as ai_router
from .dashboard import router as dashboard_router
from .marketplace import router as marketplace_router
from .pricing import router as pricing_router
from .users import router as users_router

__all__ = [
	"ai_router",
	"dashboard_router",
	"marketplace_router",
	"pricing_router",
	"users_router",
]
