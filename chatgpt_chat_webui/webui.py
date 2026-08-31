"""WebUI registration: a no-build vanilla HTML/CSS/JS console served by the
same FastAPI process as the API.

Assets ship inside the package (chatgpt_chat_webui/webui/) and are served
from the same loopback origin — no CDN, no external runtime calls, no build
step.

Security/logging contract: this module never logs headers, bodies, prompts,
model output, or secrets.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEBUI_DIR = Path(__file__).resolve().parent / "webui"
_INDEX = WEBUI_DIR / "index.html"


def register_webui(app: FastAPI) -> None:
    """Serve the console at / with its package-local assets under /static."""
    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="webui")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_INDEX, media_type="text/html")
