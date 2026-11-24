"""API 路由汇总。"""
from fastapi import APIRouter

from .routes import connections, conversations, metrics, query, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(connections.router)
api_router.include_router(query.router)
api_router.include_router(metrics.router)
api_router.include_router(conversations.router)
