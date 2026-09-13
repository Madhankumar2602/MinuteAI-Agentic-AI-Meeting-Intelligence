"""Aggregates the v1 routers."""

from fastapi import APIRouter

from app.api.v1 import action_items, auth, intelligence, meetings

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(meetings.router)
api_router.include_router(intelligence.router)
api_router.include_router(action_items.router)
