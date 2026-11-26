"""Assembles the versioned API. Every module's router is mounted under /api/v1 here."""

from fastapi import APIRouter

from app.api import health
from app.modules.discovery.router import discovery_router
from app.modules.events.router import admin_events_router, events_router
from app.modules.identity.router import auth_router, users_router
from app.modules.listings.router import admin_listings_router, listings_router
from app.modules.orders.router import orders_router
from app.modules.payments.router import payments_router
from app.modules.reservations.router import reservations_router
from app.modules.tickets.router import admin_tickets_router, tickets_router


def build_api_router(prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix)
    router.include_router(health.router)
    # Each business module adds its router here as it is built: M4 events and tickets,
    # M5 listings, and so on.
    router.include_router(auth_router)
    router.include_router(users_router)
    router.include_router(events_router)
    router.include_router(admin_events_router)
    router.include_router(tickets_router)
    router.include_router(admin_tickets_router)
    router.include_router(discovery_router)
    router.include_router(listings_router)
    router.include_router(admin_listings_router)
    router.include_router(reservations_router)
    router.include_router(orders_router)
    router.include_router(payments_router)
    return router
