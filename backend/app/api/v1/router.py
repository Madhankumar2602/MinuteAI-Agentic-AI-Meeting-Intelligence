"""Aggregates the v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    action_items,
    agent,
    ask,
    auth,
    dashboard,
    intelligence,
    jobs,
    media,
    meetings,
    mom,
    search,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(meetings.router)
api_router.include_router(intelligence.router)
api_router.include_router(action_items.router)
api_router.include_router(jobs.router)
api_router.include_router(media.router)
api_router.include_router(dashboard.router)
api_router.include_router(search.router)
api_router.include_router(mom.router)
api_router.include_router(ask.router)
api_router.include_router(agent.router)
