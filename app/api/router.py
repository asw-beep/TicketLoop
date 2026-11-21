"""Assembles the versioned API. Every module's router is mounted under /api/v1 here."""

from fastapi import APIRouter

from app.api import health


def build_api_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    router.include_router(health.router)
    # Each business module adds its router here as it is built:
    # M3 identity, M4 events and tickets, M5 listings, and so on.
    return router
