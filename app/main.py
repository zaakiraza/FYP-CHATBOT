import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.db import close_database
from app.limiter import limiter
from app.routes.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Project root = parent of `app/`
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CHAT_HTML = _PROJECT_ROOT / "frontend" / "therapist-chat.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_database()
    logger.info("MongoDB connection closed")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Therapist SOAP Assistant",
        description=(
            "RAG chatbot: embeds the question, retrieves similar SOAP note chunks from MongoDB, "
            "answers with Gemini. Configure `RAG_THERAPIST_SCOPED`, `SKIP_THERAPIST_JWT`, and collections via `.env` — see README."
        ),
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "therapist-chat",
                "description": (
                    "Chat endpoints. Use **Authorize** with `Bearer <JWT>` unless the server runs with `SKIP_THERAPIST_JWT=true`. "
                    "Request JSON: required `message`; optional `patient_id`, `session_id`."
                ),
            },
            {
                "name": "default",
                "description": "Liveness, landing page, chat demo HTML.",
            },
        ],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
        description="Public endpoint; returns 200 when the process is up (does not verify MongoDB or Gemini).",
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, tags=["default"], summary="Service home")
    async def root() -> str:
        return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Therapist SOAP Assistant</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.5}
a{color:#1d9bf0}code{background:#eee;padding:0 .2rem}</style></head><body>
<h1>Therapist SOAP Assistant API</h1>
<p>Use:</p>
<ul>
<li><a href="/docs"><strong>Swagger UI</strong></a> — <code>POST /api/therapist-chat</code></li>
<li><a href="/redoc">ReDoc</a></li>
<li><a href="/chat"><strong>Chat demo</strong></a> (same as <code>/ui/therapist-chat.html</code>)</li>
<li><a href="/health">Health</a></li>
</ul>
</body></html>"""

    def _chat_file() -> FileResponse:
        if not _CHAT_HTML.is_file():
            logger.error("Chat demo missing at %s", _CHAT_HTML)
            raise HTTPException(
                status_code=404,
                detail=f"frontend/therapist-chat.html not found (expected {_CHAT_HTML})",
            )
        return FileResponse(_CHAT_HTML, media_type="text/html; charset=utf-8")

    @app.get("/chat", include_in_schema=False)
    async def chat_demo_shortcut() -> FileResponse:
        """Short URL for the HTML chat tester."""
        return _chat_file()

    @app.get("/ui/therapist-chat.html", include_in_schema=False)
    async def chat_demo_path() -> FileResponse:
        """Same page as /chat (for older links)."""
        return _chat_file()

    if _CHAT_HTML.is_file():
        logger.info("Chat demo available at /chat and /ui/therapist-chat.html")
    else:
        logger.warning("Chat demo HTML not found at %s — create frontend/therapist-chat.html", _CHAT_HTML)

    return app


app = create_app()
