"""Shared test fixtures: a fake app-server backend, app/client factories, and
SSE helpers.

All tests are deterministic and fully offline: no subprocesses, no sockets, no
live codex binary, and no wall-clock dependence beyond asyncio's monotonic
loop clock.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from chatgpt_api.backend import BACKEND_DEAD
from chatgpt_api.config import Config
from chatgpt_api.errors import BackendNotReady, UntestedBackendVersion

from chatgpt_chat_webui.app import create_app


def make_config(**overrides: Any) -> Config:
    """Build a Config directly (no environment), with test-friendly limits."""
    params: dict[str, Any] = dict(
        host="127.0.0.1",
        port=8317,
        codex_binary="/nonexistent/codex",  # never executed in tests
        tested_codex_version="0.151.0-alpha.7.2",
        allow_untested_backend=False,
        max_body_bytes=1 << 20,
        max_messages=8,
        max_prompt_chars=10_000,
        max_concurrent_turns=4,
        max_queued_requests=16,
        turn_timeout_seconds=30,
        backend_request_timeout=30,
        backend_start_timeout=30,
        restart_backoff_initial=0.01,
        restart_backoff_max=0.05,
        subscriber_queue_size=16,
    )
    params.update(overrides)
    return Config(**params)


# ------------------------------------------------------------ notification helpers


def delta(text: str) -> dict:
    return {"method": "item/agentMessage/delta", "params": {"delta": text}}


def completed(usage: dict | None = None) -> dict:
    return {
        "method": "turn/completed",
        "params": {"usage": usage or {"input_tokens": 3, "output_tokens": 5}},
    }


def failed() -> dict:
    return {"method": "turn/failed", "params": {}}


def parse_sse(body: str) -> tuple[list[dict], bool]:
    """Parse an SSE body into (events, saw_done). Asserts framing is well-formed."""
    events: list[dict] = []
    saw_done = False
    blocks = [b for b in body.split("\n\n") if b.strip()]
    assert blocks, "SSE body contained no events"
    for block in blocks:
        assert block.startswith("data: "), f"malformed SSE block: {block!r}"
        payload = block[len("data: "):]
        if payload == "[DONE]":
            saw_done = True
            assert block == blocks[-1], "[DONE] must be the final SSE block"
        else:
            assert not saw_done, "event received after [DONE]"
            events.append(json.loads(payload))
    return events, saw_done


async def wait_until(predicate, steps: int = 1000) -> None:
    """Deterministically yield the loop until predicate() is true."""
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not met after yielding the event loop")


# ------------------------------------------------------------ fake backend


class FakeBackend:
    """In-memory stand-in for AppServerBackend (same interface, no subprocess).

    - request() records every call and answers model/list, thread/start,
      turn/start, turn/interrupt.
    - turn/start consumes a per-turn notification script (turn_scripts) or, with
      no script queued, leaves the turn hanging until the test broadcasts a
      completion manually.
    - kill()/revive() simulate backend death and restart.
    """

    def __init__(self, config: Config, *, ready: bool = True,
                 not_ready_reason: str = "binary_unavailable",
                 models_payload: Any = None) -> None:
        self._config = config
        self._ready = ready
        self.not_ready_reason = "starting" if ready else not_ready_reason
        self.codex_version = config.tested_codex_version if ready else None
        self.version_ok = ready
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, Any]] = []
        self.models_payload = models_payload if models_payload is not None else {
            "data": [
                {"id": "gpt-5.2-codex", "created": 1730000000, "ownedBy": "openai"},
                {"model": "gpt-5.1"},                      # 'model' key variant
                {"created": 5},                            # dropped: no id
                "garbage",                                 # dropped: not a dict
            ]
        }
        self.turn_scripts: list[list[dict]] = []
        self.turn_start_count = 0
        self._thread_count = 0
        self._subscribers: set[asyncio.Queue] = set()

    # -- state -------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready

    def status(self) -> dict:
        return {
            "ready": self._ready,
            "reason": None if self._ready else self.not_ready_reason,
            "codexVersion": self.codex_version,
            "testedCodexVersion": self._config.tested_codex_version,
            "versionPinned": self.version_ok,
            "untestedBackendAllowed": self._config.allow_untested_backend,
            "pid": None,
        }

    def require_ready(self) -> None:
        if self._ready:
            return
        if self.not_ready_reason == "untested_backend_version":
            raise UntestedBackendVersion(
                "codex backend version is not the pinned tested version; "
                "set CHATGPT_API_ALLOW_UNTESTED_BACKEND=1 to override")
        raise BackendNotReady(f"backend not ready ({self.not_ready_reason})")

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def kill(self) -> None:
        """Simulate the child process exiting: drop readiness and broadcast
        the backend-dead sentinel to all subscribers."""
        self._ready = False
        self.not_ready_reason = "restarting"
        self.broadcast({"method": BACKEND_DEAD, "params": {}})

    def revive(self) -> None:
        self._ready = True
        self.not_ready_reason = "starting"
        self.codex_version = self._config.tested_codex_version
        self.version_ok = True

    # -- JSON-RPC ----------------------------------------------------------

    async def request(self, method: str, params: Any,
                      timeout: float | None = None) -> Any:
        self.requests.append((method, params))
        if method == "model/list":
            return self.models_payload
        if method == "thread/start":
            self._thread_count += 1
            return {"thread": {"id": f"thread-{self._thread_count}"}}
        if method == "turn/start":
            self.turn_start_count += 1
            turn_id = f"turn-{self.turn_start_count}"
            thread_id = (params or {}).get("threadId")
            if self.turn_scripts:
                script = self.turn_scripts.pop(0)
                asyncio.get_running_loop().create_task(
                    self._deliver(thread_id, turn_id, script))
            return {"turn": {"id": turn_id}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"unexpected backend request: {method}")

    async def _deliver(self, thread_id: str, turn_id: str,
                       script: list[dict]) -> None:
        for msg in script:
            await asyncio.sleep(0)
            params = {"threadId": thread_id, "turnId": turn_id,
                      **(msg.get("params") or {})}
            self.broadcast({"method": msg["method"], "params": params})

    # -- subscriptions -----------------------------------------------------

    def broadcast(self, msg: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(msg)

    @asynccontextmanager
    async def subscribe(self):
        q: asyncio.Queue = asyncio.Queue(
            maxsize=self._config.subscriber_queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)


# ------------------------------------------------------------ factories


@pytest.fixture
def make_client(monkeypatch):
    """Factory: build the webui-wrapped FastAPI app over a FakeBackend and
    yield an httpx client wired through ASGITransport with the lifespan
    running."""
    @asynccontextmanager
    async def factory(config: Config | None = None,
                      backend: FakeBackend | None = None,
                      **config_overrides):
        config = config or make_config(**config_overrides)
        backend = backend or FakeBackend(config)
        monkeypatch.setattr(
            "chatgpt_api.app.AppServerBackend", lambda cfg: backend)
        app = create_app(config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver") as client:
                yield client, backend
    return factory


CHAT_URL = "/v1/chat/completions"


def chat_payload(**overrides: Any) -> dict:
    body: dict[str, Any] = {
        "model": "gpt-5.2-codex",
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(overrides)
    return body
