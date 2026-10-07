import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from addex_api import __version__, routes_api, routes_stremio
from addex_core.db import make_engine, make_sessionmaker


def create_app(sessionmaker: async_sessionmaker | None = None) -> FastAPI:
    """Pass `sessionmaker` to reuse an existing engine (tests); otherwise one is created
    from ADDEX_DATABASE_URL for the app's lifetime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if sessionmaker is not None:
            app.state.sessionmaker = sessionmaker
            yield
            return
        engine = make_engine()
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = Redis.from_url(
            os.environ.get("ADDEX_REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True, socket_timeout=2,
        )
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Addex", version=__version__, lifespan=lifespan)
    # Stremio clients (including web.stremio.com) call addons cross-origin. Private
    # network access lets Chromium-based clients reach an instance on localhost/LAN:
    # without it their preflight is rejected and installing fails with "Failed to fetch".
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_private_network=True
    )
    app.include_router(routes_api.router)
    app.include_router(routes_stremio.router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app
