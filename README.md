# chatgpt-chat-webui

An **unofficial**, no-build vanilla HTML/CSS/JS web console styled as a
replica of the ChatGPT Linux desktop app's chat shell. It **wraps** the
[`chatgpt-chat-api`](https://github.com/QuixThe2nd/chatgpt-chat-api) server: the webui process imports
and starts the API's FastAPI app and mounts the console at `/` with its
assets under `/static` — **same-origin, no CDN, no external runtime calls**.

This project is not affiliated with or endorsed by OpenAI. It reproduces
only the local chat surface; it does not implement upstream ChatGPT
features (history sync, accounts, plugins, voice, attachments).

## Requirements

- Linux with the official **ChatGPT desktop app installed and signed in**
  (the API backend is the app's bundled `codex app-server`).
- The sibling **chatgpt-chat-api** package (see install below) — it is not
  on PyPI; install it from a local path or git URL.
- Python 3.11+.

## Install and run

```bash
# 1. install the API package first (git URL or local path; not on PyPI)
pip install "chatgpt-chat-api @ git+https://github.com/QuixThe2nd/chatgpt-chat-api.git"

# 2. install this package
pip install -e .                               # add [test] for the test deps

# 3. run (binds 127.0.0.1:8317 by default, like the API)
chatgpt-chat-webui
```

Then open `http://127.0.0.1:8317/`. The page talks only to the server that
served it, same-origin. There is **no authentication layer** — only bind to
interfaces you trust (`CHATGPT_API_HOST` / `CHATGPT_API_PORT` override the
default loopback bind).

## What the console does

- **New chat** (sidebar) — clears the transcript and the history sent with
  later requests.
- **Model menu** — the composer's effort pill opens a nested menu. Its root
  card is a five-step **power slider** (Instant, Medium, High, Extra High,
  Pro — click the track/ticks, drag, or use Left/Right arrows) over an
  **Advanced** row; **Advanced** → **Model** opens a listbox of the
  account-visible models from `GET /v1/models`, and **Advanced** →
  **Effort** opens the same five effort choices as a list. The underlying
  model ID is never altered — only the display label is derived
  (`gpt-5.6-sol` → `GPT-5.6 Sol`). The pill's visible label is the current
  effort name (default **Pro**), and every request carries it as the
  `effort` field (the backend may ignore it). The selection lives only in
  page memory.
- **Settings** (chat header) — backend health indicator (from `/health`,
  polled every 10 s), **Reload models**, and the **Stream replies (SSE)**
  toggle (on by default).
- **Message box** — **Enter** sends, **Shift+Enter** adds a newline.
- **Send / Stop** — Send posts the whole visible transcript to
  `POST /v1/chat/completions`. While a reply is in flight Send becomes
  **Stop**, which aborts the fetch (AbortController) and cancels the
  server-side turn; partial replies are kept.
- **Mobile (≤ 700 px)** — the sidebar collapses into an overlay drawer.

Replica-only affordances the local console cannot honor (attach, voice,
share, and the response action row) are honest inert controls rendered
disabled with "(unavailable)" hints — never fake-wired.

## Limitations and safety contract

- Plain text only: replies render as text (whitespace preserved) via
  `textContent` — **no `innerHTML` of model text**, no Markdown/HTML
  rendering, by design.
- One conversation at a time, held in the page only — nothing is persisted;
  reloading clears it. The frontend reads/writes **no cookies, tokens, or
  browser storage**.
- All assets (`/static/app.js`, `/static/style.css`, `/static/favicon.svg`)
  are served by the same process — **no CDN** and no external runtime
  calls.
- Text input only: no file uploads, tools, or approval flows.
- The server-side logging contract comes from chatgpt-chat-api: metadata
  only, never prompts, model output, headers, bodies, or IPC frames.

## Layout

```
chatgpt_chat_webui/
  app.py            create_app(): chatgpt_api.app.create_app() + register_webui
  webui.py          register_webui(): mounts / and /static
  webui/            index.html, style.css, app.js, favicon.svg (no build step)
docs/visual-reference/implementation-contract.md
                    text-only replica contract (no screenshots ship here)
tests/test_webui.py routes, asset-locality, frontend safety invariants,
                    replica DOM/CSS contracts, end-to-end request shape
```

## Tests

Tests install the local API tree editable — no network, no desktop app, no
account needed at test time:

```bash
python3 -m venv .venv
.venv/bin/pip install -e /path/to/chatgpt-chat-api
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -q
```

The suite checks the console's routes/assets, that every asset reference is
local, that the frontend renders text only and touches no cookies/storage,
and that the exact request the console sends streams correctly end to end
(against an in-memory fake backend). If `node` is on `PATH`, it also runs
`node --check` on `app.js`.
