"""Assembles the versioned API. Every module's router is mounted under /api/v1 here."""

from fastapi import APIRouter

from app.api import health
from app.modules.identity.router import auth_router, users_router


def build_api_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    router.include_router(health.router)
    # Each business module adds its router here as it is built: M4 events and tickets,
    # M5 listings, and so on.
    router.include_router(auth_router)
    router.include_router(users_router)
    return router
