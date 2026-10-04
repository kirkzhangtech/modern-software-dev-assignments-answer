"""Application entry point.

Startup and shutdown are managed by a FastAPI ``lifespan`` handler rather than
module-level side effects, so importing this module has no side effects. That
matters for tests: importing ``week2.app.main`` no longer touches the database.

Domain exceptions raised by the service and data layers are translated into HTTP
responses by the handlers registered here, so routers never import
``HTTPException`` for expected failures.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, settings
from .db import init_db
from .errors import AppError
from .routers import action_items, notes

logger = logging.getLogger(__name__)

INDEX_PATH = FRONTEND_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database schema on startup."""
    init_db()
    logger.info("Database ready at %s", settings.db_path)
    yield


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Translate domain exceptions into structured HTTP responses."""
    logger.warning("%s on %s: %s", type(exc).__name__, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    """Serve the single-page frontend."""
    return INDEX_PATH.read_text(encoding="utf-8")


app.include_router(notes.router)
app.include_router(action_items.router)

app.mount("/static", StaticFiles(directory=str(Path(FRONTEND_DIR))), name="static")