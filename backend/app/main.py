"""FastAPI 应用装配：路由、异常映射、生命周期。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.router import api_router
from .core import config, deps
from .core.errors import AppError


@asynccontextmanager
async def lifespan(app):
    yield
    deps.engines.dispose_all()


def create_app():
    app = FastAPI(title="QueryHub 通用问数平台", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(api_router)
    app.mount(
        "/static",
        StaticFiles(directory=config.FRONTEND_DIST, check_dir=False),
        name="static",
    )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    return app


app = create_app()
