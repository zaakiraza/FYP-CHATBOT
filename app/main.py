import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.cache import close_redis, ping_redis
from app.config import get_settings
from app.db import close_database
from app.limiter import limiter
from app.routes.chat import router as chat_router
from app.watcher import start_watchers, stop_watchers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping_redis()
    await start_watchers()
    yield
    await stop_watchers()
    close_database()
    logger.info("MongoDB connection closed")
    await close_redis()
    logger.info("Redis connection closed")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Therapist Recommendation Assistant",
        description=(
            "Clinical recommendation chatbot for therapists. "
            "Embeds the question, retrieves semantically similar SOAP case chunks from MongoDB, "
            "and returns an anonymised recommendation via Gemini. "
            "Configure collections and RAG settings via `.env`."
        ),
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "therapist-chat",
                "description": "Send a clinical question; receive an anonymised recommendation grounded in documented SOAP cases.",
            },
            {
                "name": "default",
                "description": "Liveness check.",
            },
        ],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.middleware("http")
    async def add_response_time(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.0f}ms"
        if request.url.path.startswith("/api/"):
            logger.info("response_time path=%s status=%s time=%dms",
                        request.url.path, response.status_code, elapsed_ms)
        return response
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_router)

    @app.get(
        "/health",
        tags=["default"],
        summary="Health check",
        description="Returns 200 when the process is up.",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
