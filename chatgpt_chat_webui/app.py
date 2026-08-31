"""Application factory: the chatgpt-chat-api app with the WebUI mounted.

Security/logging contract: this module never logs headers, bodies, prompts,
model output, IPC frames, or secrets.
"""
from __future__ import annotations

from chatgpt_api.app import create_app as create_api_app
from chatgpt_api.config import Config

from .webui import register_webui


def create_app(config: Config | None = None):
    """Build the API app and mount the console at / + /static, same-origin."""
    app = create_api_app(config)
    register_webui(app)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    config = Config.from_env()
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
