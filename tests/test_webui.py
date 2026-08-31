"""WebUI: routes and assets served by the same process, frontend safety
invariants, and the exact request shape the console sends — all offline,
against the same ASGI app over the FakeBackend."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from chatgpt_chat_webui.webui import WEBUI_DIR

from .conftest import CHAT_URL, FakeBackend, completed, delta, make_config, parse_sse

ASSETS = {p.name: p.read_text() for p in sorted(WEBUI_DIR.iterdir()) if p.is_file()}

# The SVG favicon's mandatory xmlns is an XML namespace identifier, never a
# fetched URL — the one http string permitted inside an asset.
SVG_XMLNS = "http://www.w3.org/2000/svg"

# Console element ids that must exist in the HTML and be wired in the JS.
WIRED_IDS = (
    # health + models
    "health",
    "health-text",
    "model-select",
    "refresh-models",
    "stream-toggle",
    # sidebar / menu / recents
    "sidebar",
    "sidebar-toggle",
    "menu-btn",
    "clear-btn",
    "chat-title",
    # settings popover
    "settings-btn",
    "settings-popover",
    # nested model menu (trigger -> slider/advanced -> listboxes)
    "model-trigger",
    "model-trigger-label",
    "model-popover",
    "effort-slider",
    "advanced-trigger",
    "advanced-popover",
    "model-list-trigger",
    "model-list-current",
    "model-list",
    "effort-list-trigger",
    "effort-list-current",
    "effort-list",
    # transcript + composer
    "transcript",
    "transcript-empty",
    "request-status",
    "error",
    "composer",
    "composer-input",
    "send-btn",
    "stop-btn",
)

# Rendering/credential safety: text-only output, no browser storage or
# credential access, no dynamic code evaluation.
FORBIDDEN_PATTERNS = (
    r"innerHTML",
    r"outerHTML",
    r"insertAdjacentHTML",
    r"document\.write",
    r"document\.cookie",
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
    r"\bindexedDB\b",
    r"\beval\s*\(",
    r"new\s+Function",
    r"document\.createElement\(\s*[\"']script",
)


# ------------------------------------------------------------ routes


async def test_index_served(make_client):
    async with make_client() as (client, _):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        for marker in ('lang="en"', 'id="transcript"', 'id="model-select"',
                       'id="stream-toggle"', 'id="stop-btn"', 'id="composer"'):
            assert marker in resp.text, marker


async def test_index_served_even_when_backend_not_ready(make_client):
    config = make_config()
    backend = FakeBackend(config, ready=False)
    async with make_client(config=config, backend=backend) as (client, _):
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/health")).status_code == 503


async def test_referenced_assets_are_local_and_served(make_client):
    """Every src/href in the page is a same-origin path that this process
    actually serves — no CDN or external runtime calls. Same-document
    fragment links (e.g. the skip link's "#composer-input") carry no URL;
    they are checked for schemes but never fetched."""
    async with make_client() as (client, _):
        html = (await client.get("/")).text
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        assert refs, "index references no assets"
        fragments = [r for r in refs if r.startswith("#")]
        assets = [r for r in refs if not r.startswith("#")]
        assert fragments, "expected a same-document fragment link (skip link)"
        for frag in fragments:
            assert "://" not in frag and not frag.startswith("//"), frag
        assert assets, "index references no fetched assets"
        for ref in assets:
            assert ref.startswith("/"), f"non-local asset reference: {ref}"
            assert "://" not in ref and not ref.startswith("//"), ref
            resp = await client.get(ref)
            assert resp.status_code == 200, ref


async def test_favicon_is_local_and_served(make_client):
    """The page declares a same-origin icon so the browser never falls back
    to its implicit /favicon.ico request, which this app does not serve."""
    async with make_client() as (client, _):
        html = (await client.get("/")).text
        m = re.search(r'rel="icon"[^>]*href="(/static/[^"]+)"', html)
        assert m, "index declares no same-origin favicon"
        icon = await client.get(m.group(1))
        assert icon.status_code == 200
        assert "svg" in icon.headers["content-type"]
        assert icon.text.lstrip().startswith("<svg")


async def test_api_routes_unaffected_by_webui(make_client):
    async with make_client() as (client, _):
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/static/app.js")).status_code == 200
        assert (await client.get("/static/style.css")).status_code == 200
        assert (await client.get("/health")).status_code == 200
        models = await client.get("/v1/models")
        assert models.status_code == 200
        assert [m["id"] for m in models.json()["data"]] == [
            "gpt-5.2-codex", "gpt-5.1"]


async def test_asset_content_types(make_client):
    async with make_client() as (client, _):
        js = await client.get("/static/app.js")
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]
        css = await client.get("/static/style.css")
        assert css.status_code == 200
        assert css.headers["content-type"].startswith("text/css")


# ------------------------------------------------------------ safety invariants


def test_frontend_renders_text_and_touches_no_credentials():
    for name, text in ASSETS.items():
        for pattern in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, text), (name, pattern)


def test_no_external_urls_in_assets():
    for name, text in ASSETS.items():
        assert not re.search(r"https?://", text.replace(SVG_XMLNS, "")), name
        assert not re.search(r"(?:src|href)=\"\s*(?![/\"#])", text), name


def test_console_controls_are_wired():
    js = ASSETS["app.js"]
    html = ASSETS["index.html"]
    for element_id in WIRED_IDS:
        assert f'id="{element_id}"' in html, element_id
        assert f'"{element_id}"' in js, element_id
    # Streaming, stopping, and clearing behavior is present.
    assert "new AbortController" in js
    assert ".abort()" in js
    assert "getReader" in js
    assert "[DONE]" in js
    assert "delta.content" in js


def test_app_js_is_valid_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for a JS syntax check")
    proc = subprocess.run([node, "--check", str(WEBUI_DIR / "app.js")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------ replica DOM/CSS contracts


def test_replica_dom_structure():
    html = ASSETS["index.html"]
    # Exact empty-state heading.
    assert '<h1 class="empty-heading">Ready when you are.</h1>' in html
    # No Chat / Work segmented control in the empty state.
    assert 'aria-label="Chat mode"' not in html
    assert 'class="segmented"' not in html
    # "New chat" lives in the sidebar and is the wired clear control.
    assert re.search(r'<button id="clear-btn"[^>]*>.*?<span>New chat</span>',
                     html, re.S)
    # Hidden native <select> fallback mirrors the listbox selection.
    fallback = re.search(r'<div class="model-select-fallback" hidden>.*?'
                         r'</div>', html, re.S).group(0)
    assert 'id="model-select"' in fallback


def test_replica_layout_and_responsive_css():
    css = ASSETS["style.css"]
    # Fixed 276px desktop sidebar on a light surface; white main surface.
    sidebar = re.search(r"\.sidebar\s*\{[^}]*\}", css).group(0)
    assert "width: 276px" in sidebar
    assert "background: #f7f7f8" in sidebar
    main = re.search(r"\.main\s*\{[^}]*\}", css).group(0)
    assert "background: #fff" in main
    # Populated state is driven by the body.has-messages class.
    assert "body.has-messages .empty-state" in css
    assert "body.has-messages .transcript" in css
    # Responsive markers: media query, off-canvas sidebar, open state,
    # overlay, and the hamburger trigger hidden on desktop.
    assert "@media (max-width: 700px)" in css
    assert "transform: translateX(-100%)" in css
    assert "body.sidebar-open .sidebar" in css
    assert "body.sidebar-open::after" in css
    menu = re.search(r"\.menu-trigger\s*\{[^}]*\}", css).group(0)
    assert "display: none" in menu


def test_replica_message_and_stop_css():
    css = ASSETS["style.css"]
    # User/assistant turn styling (user bubble right, assistant unboxed).
    assert re.search(r"\.message-user\b", css)
    assert re.search(r"\.message-assistant\b", css)
    # Round Send/Stop controls.
    send_stop = re.search(r"\.send-btn,\s*\n\.stop-btn\s*\{[^}]*\}",
                          css).group(0)
    assert "border-radius: 50%" in send_stop


def test_replica_popover_and_listbox_semantics():
    html = ASSETS["index.html"]
    # Settings + nested model menus are dialog popovers.
    for pop in ("settings-popover", "model-popover", "advanced-popover",
                "effort-list"):
        assert re.search(rf'id="{pop}"[^>]*role="dialog"', html), pop
    # Model choices are a real listbox with options.
    assert re.search(r'id="model-list"[^>]*role="listbox"', html)
    assert 'role="option"' in html
    # Each trigger declares haspopup/expanded/controls wiring.
    for trigger, controls in (("settings-btn", "settings-popover"),
                              ("model-trigger", "model-popover"),
                              ("advanced-trigger", "advanced-popover"),
                              ("model-list-trigger", "model-list"),
                              ("effort-list-trigger", "effort-list")):
        btn = re.search(rf'<button id="{trigger}"[^>]*>', html).group(0)
        assert f'aria-controls="{controls}"' in btn, trigger
        assert "aria-haspopup=" in btn, trigger
        assert 'aria-expanded="false"' in btn, trigger
    assert 'aria-haspopup="listbox"' in html
    # The effort choices are a real listbox with static options.
    assert re.search(r'id="effort-listbox"[^>]*role="listbox"', html)


def test_replica_unavailable_controls_are_disabled():
    html = ASSETS["index.html"]
    css = ASSETS["style.css"]
    # Replica-only affordances are inert: rendered disabled with a
    # "(not available in local console)" hint rather than hidden or
    # half-wired. The survivors are share, attach, and voice — the titlebar
    # and the extra sidebar rows were removed entirely, not disabled.
    disabled_unavailable = re.findall(
        r'<button[^>]*disabled[^>]*'
        r'aria-label="[^"]*\(not available in local console\)"', html)
    assert len(disabled_unavailable) >= 3
    # Disabled styling exists for every inert-control family.
    assert "button:disabled" in css
    assert ".icon-btn:disabled" in css
    assert '.model-option[aria-disabled="true"]' in css
    # The removed Chat / Work segmented control has no leftover CSS.
    assert ".segment" not in css


def test_app_js_replica_contracts():
    js = ASSETS["app.js"]
    # Populated/empty state is driven by the body class.
    assert 'classList.add("has-messages")' in js
    assert 'classList.remove("has-messages")' in js
    # Safe recent-title derivation: whitespace collapsed, length-capped,
    # assigned via textContent only.
    assert "function chatLabel(" in js
    assert "TITLE_MAX" in js
    assert "currentRecentTitle.textContent = text" in js
    assert "chatTitle.textContent" in js
    # Mobile sidebar + popover/model-listbox wiring.
    assert '"sidebar-open"' in js
    assert "function togglePopover(" in js
    assert "function closePopovers(" in js
    assert '"model-list"' in js
    assert '"model-list-trigger"' in js
    assert "aria-expanded" in js
    assert "aria-selected" in js
    # displayModelName mapping logic: "gpt-5.6-sol" -> "GPT-5.6 Sol".
    assert "function displayModelName(" in js
    assert 'indexOf("gpt-")' in js
    assert '"GPT-"' in js
    assert "charAt(0).toUpperCase()" in js
    # Streaming + abort contracts, text-only rendering.
    assert "new AbortController" in js
    assert ".abort()" in js
    assert '"[DONE]"' in js
    assert "textContent" in js


# ------------------------------------------------------------ assistant action row
#
# Boundary contracts for the official-style muted action-icon row: it is
# appended under an assistant turn ONLY after a successful (streamed or
# non-streamed) completion with content — never while streaming, never
# after Stop/abort or an error, never on user bubbles — and Clear removes
# it with the transcript. All five upstream actions are unavailable in
# this replica, so the controls are honest disabled buttons, built with
# createElementNS/setAttribute only.


def send_fn(js):
    return re.search(r"async function send\(\)\s*\{.*?\n  \}\n", js, re.S).group(0)


def test_action_row_built_with_safe_dom_only():
    """Icons and buttons are assembled via createElementNS/createElement +
    setAttribute/appendChild; no HTML strings anywhere in the builder."""
    js = ASSETS["app.js"]
    assert "function makeActionIcon(" in js
    assert 'document.createElementNS(SVG_NS, "svg")' in js
    assert 'document.createElementNS(SVG_NS, shape[0])' in js
    assert "el.setAttribute(name, attrs[name])" in js
    builder = re.search(r"function addActionRow\(message\)\s*\{.*?\n  \}",
                        js, re.S).group(0)
    assert 'document.createElement("button")' in builder
    assert "row.appendChild(btn)" in builder
    assert "message.appendChild(row)" in builder
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert forbidden not in builder
    # Icons are presentational; the accessible name lives on the button.
    icon = re.search(r"function makeActionIcon\(shapes\)\s*\{.*?\n  \}",
                     js, re.S).group(0)
    assert 'svg.setAttribute("aria-hidden", "true")' in icon
    assert 'svg.setAttribute("focusable", "false")' in icon


def test_action_row_has_exact_five_labels():
    """Read aloud, Copy response, Regenerate response, Good response,
    Bad response — in the official order, and no others."""
    js = ASSETS["app.js"]
    spec = re.search(r"var RESPONSE_ACTIONS = \[.*?\n  \];", js, re.S).group(0)
    labels = re.findall(r'label: "([^"]+)"', spec)
    assert labels == ["Read aloud", "Copy response", "Regenerate response",
                      "Good response", "Bad response"]
    # Every action carries at least one SVG shape.
    assert spec.count("shapes: [") == 5


def test_action_row_controls_are_disabled_and_unavailable():
    """Honest inert controls: disabled buttons whose accessible label AND
    title say (unavailable); no click handler, no faked success."""
    js = ASSETS["app.js"]
    builder = re.search(r"function addActionRow\(message\)\s*\{.*?\n  \}",
                        js, re.S).group(0)
    assert "btn.disabled = true" in builder
    assert 'action.label + " (unavailable)"' in builder
    assert 'btn.setAttribute("aria-label", label)' in builder
    assert "btn.title = label" in builder
    assert "addEventListener" not in builder
    # Disabled treatment stays legible, not faded to invisible.
    css = ASSETS["style.css"]
    disabled = css_rule(css, ".action-btn:disabled")
    assert "color: #8e8e93" in disabled


def test_action_row_only_after_successful_completion():
    """The row is appended inside send()'s success path, after the awaited
    completion resolves and only when text was received; the catch and
    finally blocks never add it, so streaming, Stop/abort, and error
    states never show it. Stream helpers and addMessage never touch it,
    so user bubbles and in-flight bodies stay clean."""
    js = ASSETS["app.js"]
    send = send_fn(js)
    success = re.search(r"try\s*\{(.*?)\}\s*catch", send, re.S).group(1)
    assert "received = await requestCompletion(body)" in success
    assert 'if (received) addActionRow(body.closest(".message"));' in success
    assert success.index("requestCompletion(body)") < success.index("addActionRow")
    catch = re.search(r"catch\s*\(err\)\s*\{(.*?)\}\s*finally", send, re.S).group(1)
    finally_ = send.split("finally", 1)[1]
    assert "addActionRow" not in catch
    assert "addActionRow" not in finally_
    # Nothing else in the app inserts the row.
    assert js.count("addActionRow(") == 2  # definition + the one call
    for fn in ("consumeStream", "appendText", "addMessage", "dropEmptyMessage"):
        body = re.search(rf"function {fn}\(.*?\n  \}}", js, re.S).group(0)
        assert "addActionRow" not in body, fn
    # Clear removes rows with the transcript; abort path exists and is
    # distinct from the success path.
    clear = re.search(r"function clearConversation\(\)\s*\{.*?\n  \}",
                      js, re.S).group(0)
    assert "els.transcript.replaceChildren(els.transcriptEmpty)" in clear


def test_action_row_css_treatment():
    """Compact left-aligned muted row: 24px controls, 4px gap, small top
    margin, no box/border background, 16px stroke icons. It lives inside
    the assistant message, so the ~736px transcript width bounds it and
    nothing user-bubble-specific changes."""
    css = ASSETS["style.css"]
    row = css_rule(css, ".message-actions")
    assert "display: flex" in row
    assert "gap: 4px" in row
    assert re.search(r"margin-top:\s*[4-8]px", row)
    assert "background" not in row and "border" not in row
    btn = css_rule(css, ".action-btn")
    assert "width: 24px" in btn
    assert "height: 24px" in btn
    assert "background: none" in btn
    assert "border: none" in btn
    assert "color: #8e8e93" in btn
    icon = css_rule(css, ".action-btn svg")
    assert "width: 16px" in icon
    assert "height: 16px" in icon


# ------------------------------------------------------------ model menu
#
# Deterministic contracts for the pinned replica model selector: Pro pill,
# 1280×820 geometry, three-level hierarchy, keyboard listbox, and
# checkmark-only selection. Geometry declarations live in style.css; the
# behavior tokens live in app.js.


def css_rule(css, selector):
    """One declaration block for an exact selector (no pseudo/compound).
    The selector must be the rule's ENTIRE selector: preceded only by the
    start of the sheet or the previous rule's closing brace (comments
    stripped), so one branch of an earlier comma-separated group (e.g.
    `.advanced-popover` inside `.model-popover,\\n.advanced-popover`) can
    never satisfy a lookup for the standalone rule."""
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(rf"(?:^|\}})\s*{re.escape(selector)}\s*\{{([^}}]*)\}}",
                  stripped)
    assert m, f"missing CSS rule: {selector}"
    return m.group(0)


def test_model_pill_shows_effort_name():
    """The composer pill is labelled with the current EFFORT name (default
    `Pro`), synced by app.js whenever the slider or Effort list changes it.
    The selected model ID never overwrites the visible label; it lives in
    the hidden <select>, the Advanced row, and the pill's tooltip."""
    html, js, css = ASSETS["index.html"], ASSETS["app.js"], ASSETS["style.css"]
    assert re.search(r'id="model-trigger-label">Pro<', html)
    assert 'getElementById("model-trigger-label")' in js
    assert "els.modelTriggerLabel.textContent = name" in js
    assert "els.modelTrigger.title = displayModelName(id)" in js
    assert "els.modelSelect.value" in js  # requests keep the exact ID
    # Pill box pinned at 226×30 in the reference capture.
    trigger = css_rule(css, ".model-trigger")
    assert "width: 226px" in trigger
    assert "height: 30px" in trigger
    # The effort name is centered in the pill; the chevron is pinned right.
    assert "position: relative" in trigger
    assert "justify-content: center" in trigger
    chevron = css_rule(css, ".model-trigger > svg")
    assert "position: absolute" in chevron
    assert "right: 10px" in chevron
    # The composer padding that lands the pill at x≈801–1027.
    composer = css_rule(css, ".composer")
    assert re.search(r"padding:\s*6px 3px 6px 6px", composer)


def test_model_menu_geometry_pinned_to_reference():
    """Exact geometry declarations for the 1280×820, DPR 1 reference:
    root/Advanced cards 226px wide at x≈801–1027 starting y≈472 (80px and
    106px tall, 14px radius); listbox 282px wide, right edge x≈802, bottom
    aligned with the Advanced card at y≈578."""
    css = ASSETS["style.css"]
    pair = css_rule(css, ".model-popover,\n.advanced-popover")
    assert "width: 226px" in pair
    assert "top: calc(100% - 2px)" in pair  # card top y≈472 under composer
    assert "right: 71px" in pair  # right edge x≈1027 off the padding box
    root = css_rule(css, ".model-popover")
    assert "height: 80px" in root
    # Slider pinned to the top half, "Advanced ›" row to the bottom half.
    assert "justify-content: space-between" in root
    advanced = css_rule(css, ".advanced-popover")
    assert "height: 106px" in advanced
    assert re.search(r"padding:\s*0 6px", advanced)  # 6px inner inset
    # The standalone Advanced rule, not its branch of the shared group.
    assert "width: 226px" not in advanced
    pop = css_rule(css, ".popover")
    assert "border-radius: 14px" in pop
    assert "background: #fff" in pop
    assert "border: 1px solid #e5e5e5" in pop  # thin border, soft shadow
    assert ("box-shadow: 0 4px 14px rgba(0, 0, 0, 0.10), "
            "0 1px 3px rgba(0, 0, 0, 0.06)") in pop
    lst = css_rule(css, ".model-list")
    assert "width: 282px" in lst
    assert "right: 296px" in lst  # right edge x≈802, left of Advanced
    assert "bottom: -104px" in lst  # bottom edge y≈578, aligned w/ Advanced


def test_advanced_card_header_and_rows():
    """Advanced header is normal-case `Advanced` in muted 13px normal
    weight; rows are Model + current value + chevron and an ENABLED Effort
    row carrying the current effort name (default Pro) + chevron."""
    html, css = ASSETS["index.html"], ASSETS["style.css"]
    title = css_rule(css, ".popover-title")
    assert "font-size: 13px" in title
    assert "font-weight: 400" in title
    assert "text-transform" not in title  # not uppercase
    assert "color: #8e8e93" in title
    # 39px header row, left-aligned, hairline separator below it.
    assert "height: 39px" in title
    assert "flex-shrink: 0" in title
    assert re.search(r"padding:\s*0 6px", title)
    assert "justify-content: flex-start" in title
    assert "gap: 4px" in title
    assert "border-bottom: 1px solid #ededed" in title
    # The root card's "Advanced ›" row: label starts left, chevron follows
    # it (not pushed to the far edge), muted normal-weight text.
    row = css_rule(css, ".popover-row-advanced")
    assert "justify-content: flex-start" in row
    assert "gap: 4px" in row
    assert re.search(r"padding:\s*0 6px", row)
    assert "font-weight: 400" in row
    assert "color: #8e8e93" in row
    # Inside the Advanced card, rows are 33px with the 6px inner inset.
    adv_row = css_rule(css, ".advanced-popover .popover-row")
    assert "height: 33px" in adv_row
    assert re.search(r"padding:\s*0 6px", adv_row)
    assert '<span class="row-label">Model</span>' in html
    assert '<span class="row-label">Effort</span>' in html
    assert '<span id="effort-list-current" class="row-value">Pro</span>' in html
    assert re.search(r'<h3 class="popover-title">\s*<span>Advanced</span>', html)
    # The Effort row is a working listbox trigger — never a disabled row
    # hardcoded with "(not available in local console)".
    effort_trigger = re.search(
        r'<button id="effort-list-trigger"[^>]*>', html).group(0)
    assert "disabled" not in effort_trigger
    assert 'aria-haspopup="listbox"' in effort_trigger
    assert 'aria-expanded="false"' in effort_trigger
    assert 'aria-controls="effort-list"' in effort_trigger
    advanced_card = re.search(
        r'id="advanced-popover".*?<ul id="model-list"', html, re.S).group(0)
    assert "(not available in local console)" not in advanced_card


def test_model_rows_checkmark_only_selection():
    """Rows are 32px on white; the selected row is marked by a right
    checkmark only — no permanent gray selected fill. Hover/focus may be
    light gray."""
    css, js = ASSETS["style.css"], ASSETS["app.js"]
    option = css_rule(css, ".model-option")
    assert "height: 32px" in option
    assert "background: #fff" in option
    # Inside the listbox card, rows align to its 6px inner padding; the
    # 32px row height is preserved.
    listed = css_rule(css, ".model-list .model-option")
    assert "padding-left: 6px" in listed
    assert "padding-right: 6px" in listed
    assert "height" not in listed
    hover = css_rule(css, ".model-option:hover,\n.model-option:focus-visible")
    assert "background: rgba(0, 0, 0, 0.05)" in hover
    assert not re.search(
        r'\.model-option\[aria-selected="true"\]\s*\{[^}]*background', css)
    # app.js adds/removes the right checkmark per selection.
    assert "function makeCheckmark(" in js
    assert "row.appendChild(makeCheckmark())" in js
    assert 'row.setAttribute("aria-selected", selected ? "true" : "false")' in js
    assert 'svg.style.marginLeft = "auto"' in js  # pushed to the right edge


def test_model_list_dynamic_population_and_compact_value():
    """Every /v1/models ID becomes a row (full mapping, gpt-5.6-sol ->
    GPT-5.6 Sol); the compact Advanced value omits the leading GPT-."""
    js = ASSETS["app.js"]
    assert "fetch(\"/v1/models\")" in js
    assert 'li.setAttribute("data-model-id", id)' in js
    assert "label.textContent = displayModelName(id)" in js
    assert "function compactModelName(" in js
    assert 'name.indexOf("GPT-") === 0 ? name.slice(4) : name' in js
    assert "els.modelListCurrent.textContent = compactModelName(id)" in js


def test_model_menu_hierarchy_behavior():
    """Trigger toggles root; Advanced replaces root; Model opens/closes
    the list WITHOUT closing Advanced (its row stays highlighted);
    clicking outside closes all; selecting commits and closes all."""
    js, css = ASSETS["app.js"], ASSETS["style.css"]
    for fn in ("function closeModelList(", "function closeAdvanced(",
               "function closeRoot(", "function closePopovers(",
               "function openRoot(", "function openAdvanced(",
               "function openModelList(", "function selectModelAndClose("):
        assert fn in js, fn
    # Advanced replaces the root card at the same anchor.
    open_advanced = re.search(r"function openAdvanced\(\)\s*\{.*?\n  \}",
                              js, re.S).group(0)
    assert "els.modelPopover.hidden = true" in open_advanced
    # Opening the list keeps Advanced visible and highlights its row.
    open_list = re.search(r"function openModelList\(\)\s*\{.*?\n  \}",
                          js, re.S).group(0)
    for absent in ("closeAdvanced", "closePopovers", "els.advancedPopover.hidden = true"):
        assert absent not in open_list, absent
    assert 'classList.add("is-active")' in open_list
    assert ".popover-row.is-active" in css
    assert 'classList.remove("is-active")' in js
    # Closing the list restores focus to the Advanced Model row.
    assert "els.modelListTrigger.focus()" in js
    # Selecting from the list commits, closes every level, refocuses pill.
    pick = re.search(r"function selectModelAndClose\(id\)\s*\{.*?\n  \}",
                     js, re.S).group(0)
    assert "selectModel(id)" in pick
    assert "closePopovers()" in pick
    assert "els.modelTrigger.focus()" in pick
    # closeRoot ALWAYS hides the root card and resets the pill's expanded
    # state — no early return, even when Advanced already replaced (hid)
    # the root card. Selection or an outside click must leave every model
    # surface hidden and every model trigger aria-expanded="false".
    close_root = re.search(r"function closeRoot\(\)\s*\{.*?\n  \}",
                           js, re.S).group(0)
    assert "closeAdvanced()" in close_root
    assert "els.modelPopover.hidden = true" in close_root
    assert "setExpanded(els.modelTrigger, false)" in close_root
    assert "return" not in close_root  # unconditional, even if already hidden
    close_adv = re.search(r"function closeAdvanced\(\)\s*\{.*?\n  \}",
                          js, re.S).group(0)
    assert "closeModelList(false)" in close_adv
    assert "els.advancedPopover.hidden = true" in close_adv
    assert "setExpanded(els.advancedTrigger, false)" in close_adv
    # Closing the list also deactivates the Advanced Model row highlight.
    close_list = re.search(r"function closeModelList\(restoreFocus\)\s*\{.*?"
                           r"\n  \}", js, re.S).group(0)
    assert "els.modelList.hidden = true" in close_list
    assert "setExpanded(els.modelListTrigger, false)" in close_list
    assert 'classList.remove("is-active")' in close_list
    # Outside click closes all open surfaces, not just the top one.
    assert "function insideOpenSurface(" in js
    outside = re.search(
        r"document\.addEventListener\(\"click\".*?\n  \}\);", js, re.S).group(0)
    assert "closePopovers()" in outside


def test_model_menu_escape_unwinds_one_level_per_press():
    """Escape: an open list closes ALONE with focus back on the Advanced
    Model row; from Advanced — or the bare root — the whole model
    hierarchy closes in one press with focus back on the composer pill.
    No dead third press; then settings, then the sidebar."""
    js = ASSETS["app.js"]
    block = re.search(r"function handleEscapeKey\(\)\s*\{.*?\n  \}",
                      js, re.S).group(0)
    # List first, alone, restoring focus to the Advanced Model row.
    assert "closeModelList(true)" in block
    # Advanced or bare root: one press closes the whole hierarchy and
    # refocuses the pill — keyed on modelMenuOpen(), so a replaced
    # (already hidden) root card can't produce a dead Escape.
    assert "modelMenuOpen()" in block
    assert "closeRoot()" in block
    assert "els.modelTrigger.focus()" in block
    assert block.index("closeModelList(true)") < block.index("closeRoot()")
    # Then settings, then the sidebar.
    assert "closeSettings()" in block
    assert "sidebar-open" in block
    assert 'document.addEventListener("keydown"' in js
    # The focus restore on list close is conditional on the flag, so the
    # Advanced/root branch stays the only path that refocuses the pill.
    close_list = re.search(r"function closeModelList\(restoreFocus\)\s*\{.*?"
                           r"\n  \}", js, re.S).group(0)
    assert "if (restoreFocus) els.modelListTrigger.focus();" in close_list


def test_model_listbox_keyboard_contract():
    """ArrowDown/ArrowUp (wrapping), Home/End, Enter/Space select, Escape
    hierarchical — with aria state and roving focus on the rows."""
    js = ASSETS["app.js"]
    handler = re.search(
        r'els\.modelList\.addEventListener\("keydown".*?\n  \}\);',
        js, re.S).group(0)
    for key in ('"ArrowDown"', '"ArrowUp"', '"Home"', '"End"',
                '"Enter"', '" "'):
        assert key in handler, key
    assert handler.count("event.preventDefault()") >= 4
    assert ".focus()" in handler
    assert 'rows[(index + step + rows.length) % rows.length].focus()' in handler
    assert "selectModelAndClose(" in handler
    # Roving focus targets + aria state on generated rows.
    assert 'li.setAttribute("tabindex", "-1")' in js
    assert 'li.setAttribute("role", "option")' in js
    assert 'setAttribute("aria-expanded", open ? "true" : "false")' in js


def test_model_menu_cards_stay_visible_low_and_narrow():
    """When the composer sits low (populated chats) or the viewport is
    390px wide, the hierarchy flips above and re-anchors inside the
    viewport — no card is clipped and every model stays reachable."""
    css, js = ASSETS["style.css"], ASSETS["app.js"]
    assert "function updateMenuDirection(" in js
    assert 'classList.toggle("menu-up"' in js
    flip = css_rule(css, ".composer.menu-up .model-popover,\n.composer.menu-up .advanced-popover")
    assert "top: auto" in flip
    assert "bottom: calc(100% + 6px)" in flip
    assert "bottom: calc(100% + 6px)" in css_rule(
        css, ".composer.menu-up .model-list")
    media = css.split("@media (max-width: 700px)", 1)[1].split("@media", 1)[0]
    assert "width: auto" in media  # pill shrinks instead of starving input
    assert "width: min(282px, calc(100vw - 24px))" in media
    assert "max-width: calc(100vw - 24px)" in css_rule(css, ".popover")
    assert "max-height: 300px" in css_rule(css, ".model-list")


def test_mobile_model_list_never_shares_advanced_bottom_edge():
    """On <=700px viewports the listbox re-anchors off the desktop bottom
    edge (composer_bottom + 104px, aligned with the Advanced card), which
    would overlay the parent entirely on a narrow viewport. Below the
    composer its bottom sits 4px above the composer's bottom edge — a 2px
    gap over the Advanced card's top at 100% - 2px. Flipped upward it
    clears the 106px Advanced card: 6px gap + 106px card + 2px gap gives
    bottom: calc(100% + 114px). Desktop geometry stays pinned."""
    css = ASSETS["style.css"]
    media = css.split("@media (max-width: 700px)", 1)[1].split("@media", 1)[0]
    # Both mobile rules exist inside the media block with the exact
    # re-anchored bottom edges.
    lst = css_rule(media, ".model-list")
    assert "right: 8px" in lst
    assert "width: min(282px, calc(100vw - 24px))" in lst
    assert "bottom: 4px" in lst
    # The desktop anchor that shares the Advanced card's bottom edge must
    # not survive into the mobile below-composer rule.
    assert "bottom: -104px" not in lst
    up = css_rule(media, ".composer.menu-up .model-list")
    assert "bottom: calc(100% + 114px)" in up
    # Nor may the flipped list inherit the desktop flip anchor, which
    # coincides with the 106px Advanced card's own bottom edge.
    assert "bottom: calc(100% + 6px)" not in up
    # Desktop coordinates are untouched by the mobile overrides: the
    # sheet-level rules still pin the reference-capture geometry.
    assert "bottom: -104px" in css_rule(css, ".model-list")
    assert "bottom: calc(100% + 6px)" in css_rule(
        css, ".composer.menu-up .model-list")


def test_mobile_open_list_forces_upward_flip():
    """Regression for the 390×844 empty-state overlap: the downward mobile
    listbox stacks ABOVE the Advanced card (its bottom edge is anchored 4px
    above the composer's bottom edge), so a 7-row list always climbs back
    over the composer — in the live capture it covered y535–580. CSS alone
    cannot fix this: the full stack needs ~348px below the composer but
    only ~260px exist there, and CSS cannot measure the remaining room.
    Opening the listbox on a <=700px viewport must therefore force the
    menu-up flip regardless of the room-below heuristic, while root-only
    and Advanced-only opens (and every desktop open) keep it."""
    js, css = ASSETS["app.js"], ASSETS["style.css"]
    upd = re.search(r"function updateMenuDirection\(forList\)\s*\{.*?\n  \}",
                    js, re.S).group(0)
    # The populated/low-composer heuristic is preserved.
    assert "rect.bottom + 130 <= window.innerHeight" in upd
    # ...but an open listbox on a narrow viewport always flips upward.
    assert re.search(r"forList && window\.innerWidth <= 700", upd)
    assert 'classList.toggle("menu-up", flip)' in upd
    # The JS breakpoint must match the CSS media query exactly — a wider JS
    # cutoff would flip where the desktop side-by-side geometry applies,
    # a narrower one would leave some narrow viewports overlapping.
    assert "@media (max-width: 700px)" in css
    # Only opening the listbox forces the flip; root and Advanced keep the
    # below-composer behavior (empty-mobile root-only stays usable, the
    # desktop contract is untouched).
    open_root = re.search(r"function openRoot\(\)\s*\{.*?\n  \}",
                          js, re.S).group(0)
    assert "updateMenuDirection(false)" in open_root
    open_adv = re.search(r"function openAdvanced\(\)\s*\{.*?\n  \}",
                         js, re.S).group(0)
    assert "updateMenuDirection(false)" in open_adv
    open_list = re.search(r"function openModelList\(\)\s*\{.*?\n  \}",
                          js, re.S).group(0)
    assert "updateMenuDirection(true)" in open_list


def test_flipped_mobile_stack_clears_composer_by_construction():
    """With menu-up every card edge is pinned ABOVE the composer top, so
    the gaps hold at any composer position — non-overlap is structural,
    not captured-luck. Numbers parsed from the sheet: Advanced bottom =
    composer top - 6; list bottom = composer top - 114 = Advanced top - 2.
    Replayed over the confirmed 390×844 empty capture (composer top y535)
    both cards sit fully in viewport, and the seven 32px rows fit under
    the list's max-height without scrolling."""
    css = ASSETS["style.css"]
    media = css.split("@media (max-width: 700px)", 1)[1].split("@media", 1)[0]
    adv_h = int(re.search(r"height:\s*(\d+)px",
                          css_rule(css, ".advanced-popover")).group(1))
    flip_cards = css_rule(
        css, ".composer.menu-up .model-popover,\n.composer.menu-up .advanced-popover")
    gap_composer = int(re.search(r"bottom:\s*calc\(100% \+ (\d+)px\)",
                                 flip_cards).group(1))
    list_off = int(re.search(
        r"bottom:\s*calc\(100% \+ (\d+)px\)",
        css_rule(media, ".composer.menu-up .model-list")).group(1))
    row_h = int(re.search(r"height:\s*(\d+)px",
                          css_rule(css, ".model-option")).group(1))
    max_h = int(re.search(r"max-height:\s*(\d+)px",
                          css_rule(css, ".model-list")).group(1))
    pad = int(re.search(r"padding:\s*(\d+)px",
                        css_rule(css, ".popover")).group(1))
    # Fixed offsets from the composer top: >= 1px gaps unconditionally.
    assert gap_composer >= 1
    gap_cards = list_off - gap_composer - adv_h
    assert gap_cards >= 1
    # All seven rows visible without scrolling: rows + card padding.
    list_h = 7 * row_h + 2 * pad
    assert list_h <= max_h
    # Replay the confirmed 390×844 empty-state capture (composer top y535):
    # Advanced y423–529, list y185–421 — both in viewport, 6px above the
    # composer, 2px between the cards.
    composer_top, viewport_h = 535, 844
    adv_top = composer_top - gap_composer - adv_h
    adv_bottom = composer_top - gap_composer
    list_bottom = composer_top - list_off
    list_top = list_bottom - list_h
    assert list_top >= 0 and adv_bottom <= viewport_h
    assert list_bottom + gap_cards == adv_top
    assert adv_bottom <= composer_top - 1  # >= 1px above composer/send


# ------------------------------------------------------------ chrome removal
#
# The simulated native titlebar (File/Edit/View/Help, back/forward,
# min/max/close) and the four inert sidebar rows (Scheduled, Plugins,
# Explore, Work) are removed entirely — not disabled, gone.


def test_no_native_titlebar_markup_or_css():
    html, css = ASSETS["index.html"], ASSETS["style.css"]
    assert 'class="titlebar"' not in html
    assert "titlebar-menu" not in html
    assert "window-controls" not in html
    for label in (">File<", ">Edit<", ">View<", ">Help<"):
        assert label not in html, label
    for action in ("Minimize", "Maximize", "Close", "Back", "Forward"):
        assert f'aria-label="{action}' not in html, action
    # The shell fills the viewport from y=0; no 35px native strip remains
    # anywhere in the sheet (layout, mobile sidebar, overlay, skip link).
    shell = css_rule(css, ".shell")
    assert "height: 100dvh" in shell
    assert "35px" not in css
    # The sidebar toggle survives — moved into the sidebar brand row, not
    # left as a dead button.
    brand = re.search(r'<div class="sidebar-brand">.*?</div>\s*<nav',
                      html, re.S).group(0)
    assert 'id="sidebar-toggle"' in brand


def test_no_extra_sidebar_buttons():
    html = ASSETS["index.html"]
    # Scheduled / Plugins / Explore / Work rows are gone from the sidebar.
    for label in ("Scheduled", "Plugins", "Explore", "Work"):
        assert f"<span>{label}</span>" not in html, label
    # The sidebar nav keeps exactly one row: New chat (the wired clear
    # control). Recents and the account footer stay.
    nav = re.search(r'<nav class="sidebar-nav".*?</nav>', html, re.S).group(0)
    assert nav.count("<button") == 1
    assert 'id="clear-btn"' in nav
    assert 'class="sidebar-recents"' in html
    assert 'class="sidebar-account"' in html
    # The empty-state Chat / Work segmented control is gone too — like the
    # sidebar Work button, it's removed entirely, not disabled.
    assert 'aria-label="Chat mode"' not in html
    assert 'class="segmented"' not in html
    assert 'class="segment' not in html


# ------------------------------------------------------------ effort slider
#
# The root model popover's top half is the official 5-step power slider:
# Instant / Medium / High / Extra High / Pro, 200×24 track with five tick
# dots, a 28×28 white thumb, purple→blue gradient fill, arrow-key control,
# and a screen-reader value like "Pro, 5 of 5. Use Left and Right arrow
# keys to adjust power". Changing the step syncs the thumb, the composer
# pill label, the Advanced Effort row value, and the Effort list
# checkmark; the request payload carries "effort".


def test_effort_slider_five_steps_and_labels():
    html, js, css = ASSETS["index.html"], ASSETS["app.js"], ASSETS["style.css"]
    slider = re.search(r'<div id="effort-slider"[^>]*>', html).group(0)
    assert 'role="slider"' in slider
    assert 'tabindex="0"' in slider
    assert 'aria-valuemin="0"' in slider
    assert 'aria-valuemax="4"' in slider
    assert 'aria-valuenow="4"' in slider  # default: Pro
    assert ("Pro, 5 of 5. Use Left and Right arrow keys to adjust power"
            in slider)
    # Five tick dots on the track.
    assert html.count('class="effort-tick"') == 5
    # The five official labels, in order.
    levels = re.search(r"var EFFORT_LEVELS = \[[^\]]*\]", js).group(0)
    assert re.findall(r'"([^"]+)"', levels) == [
        "Instant", "Medium", "High", "Extra High", "Pro"]
    # Keyboard: Left/Right (and Up/Down/Home/End) adjust when focused.
    handler = re.search(
        r'els\.effortSlider\.addEventListener\("keydown".*?\n  \}\);',
        js, re.S).group(0)
    assert '"ArrowLeft"' in handler and '"ArrowRight"' in handler
    assert "setEffort(effortStep - 1)" in handler
    assert "setEffort(effortStep + 1)" in handler
    # Clicking a tick or the track selects the nearest step; drag scrubs.
    assert "function effortStepFromClientX(" in js
    assert 'addEventListener("pointerdown"' in js
    assert 'addEventListener("pointermove"' in js
    # Track 200×24, thumb 28×28 white, purple→blue gradient fill.
    track = css_rule(css, ".effort-track")
    assert "width: 200px" in track
    assert "height: 24px" in track
    thumb = css_rule(css, ".effort-thumb")
    assert "width: 28px" in thumb
    assert "height: 28px" in thumb
    assert "background: #fff" in thumb
    fill = css_rule(css, ".effort-fill")
    assert "linear-gradient" in fill
    # The slider sits in the root card's top half, above "Advanced ›".
    popover = re.search(r'id="model-popover".*?</div>\s*<button '
                        r'id="advanced-trigger"', html, re.S).group(0)
    assert 'id="effort-slider"' in popover


def test_effort_change_syncs_everywhere():
    js = ASSETS["app.js"]
    sync = re.search(r"function syncEffortUI\(\)\s*\{.*?\n  \}",
                     js, re.S).group(0)
    # Slider aria + thumb/fill positions.
    assert "aria-valuenow" in sync
    assert ". Use Left and Right arrow keys to adjust power" in sync
    assert "effortThumb.style.left" in sync
    assert "effortFill.style.width" in sync
    # Composer pill label and Advanced Effort row value.
    assert "els.modelTriggerLabel.textContent = name" in sync
    assert "els.effortListCurrent.textContent = name" in sync
    # Effort list checkmark + aria-selected.
    assert "row.appendChild(makeCheckmark())" in sync
    assert 'row.setAttribute("aria-selected", selected ? "true" : "false")' \
        in sync
    # The request payload carries the effort label; the backend may ignore
    # it. Memory-only persistence: no storage APIs anywhere (enforced
    # globally by the safety-invariant tests above).
    assert re.search(r"effort:\s*EFFORT_LEVELS\[effortStep\]", js)
    assert "syncEffortUI()" in js  # applied at startup (default Pro)


# ------------------------------------------------------------ effort list
#
# The Advanced card's enabled Effort row opens a list to the RIGHT of the
# card (official 1027,544, 180×177): header "Effort", five 29px rows,
# checkmark-only selection, model-list keyboard/dismiss behavior, one
# sibling list at a time.


def test_effort_list_five_names_checkmark_not_disabled():
    html, js, css = ASSETS["index.html"], ASSETS["app.js"], ASSETS["style.css"]
    # Header + five rows in the official order.
    assert re.search(r'<h3 class="effort-list-title">\s*<span>Effort</span>',
                     html)
    names = re.findall(
        r'<li class="effort-option"[^>]*data-effort="([^"]+)"', html)
    assert names == ["Instant", "Medium", "High", "Extra High", "Pro"]
    rows = re.findall(r'<li class="effort-option"[^>]*>', html)
    assert len(rows) == 5
    for row in rows:
        assert 'role="option"' in row
        assert 'tabindex="-1"' in row  # roving focus, driven by keys
        assert "disabled" not in row and "aria-disabled" not in row
    # Pro is the default selection, exactly one selected row.
    selected = [r for r in rows if 'aria-selected="true"' in r]
    assert len(selected) == 1
    assert 'data-effort="Pro"' in selected[0]
    # 29px rows on white; selection is a right checkmark only (added in
    # app.js) — no permanent selected fill; hover/focus may be light gray.
    option = css_rule(css, ".effort-option")
    assert "height: 29px" in option
    assert "background: #fff" in option
    hover = css_rule(css, ".effort-option:hover,\n.effort-option:focus-visible")
    assert "background: rgba(0, 0, 0, 0.05)" in hover
    assert not re.search(
        r'\.effort-option\[aria-selected="true"\]\s*\{[^}]*background', css)
    # app.js moves the same right-edge checkmark it uses for model rows.
    sync = re.search(r"function syncEffortUI\(\)\s*\{.*?\n  \}",
                     js, re.S).group(0)
    assert "makeCheckmark()" in sync
    assert 'svg.style.marginLeft = "auto"' in js


def test_effort_list_opens_right_and_mirrors_model_list_behavior():
    html, js, css = ASSETS["index.html"], ASSETS["app.js"], ASSETS["style.css"]
    # Geometry: right of the Advanced card (left edge x≈1027), 180px wide.
    lst = css_rule(css, ".effort-list")
    assert "left: calc(100% - 71px)" in lst
    assert "width: 180px" in lst
    assert "top: calc(100% + 70px)" in lst
    flip = css_rule(css, ".composer.menu-up .effort-list")
    assert "bottom: calc(100% + 78px)" in flip
    # Mobile: shifted back inside the viewport, clearing the Advanced card
    # above composer/send — the same rule as the model list.
    media = css.split("@media (max-width: 700px)", 1)[1].split("@media", 1)[0]
    m_lst = css_rule(media, ".effort-list")
    assert "right: 8px" in m_lst
    assert "width: min(180px, calc(100vw - 24px))" in m_lst
    m_up = css_rule(media, ".composer.menu-up .effort-list")
    assert "bottom: calc(100% + 114px)" in m_up
    # Open/close mirrors the model list: Advanced stays visible, its
    # Effort row highlighted while the list is open, focus restored on
    # close.
    open_fn = re.search(r"function openEffortList\(\)\s*\{.*?\n  \}",
                        js, re.S).group(0)
    assert "updateMenuDirection(true)" in open_fn
    assert "els.effortList.hidden = false" in open_fn
    assert 'classList.add("is-active")' in open_fn
    assert ".focus()" in open_fn
    close_fn = re.search(r"function closeEffortList\(restoreFocus\)\s*\{.*?"
                         r"\n  \}", js, re.S).group(0)
    assert "els.effortList.hidden = true" in close_fn
    assert "setExpanded(els.effortListTrigger, false)" in close_fn
    assert 'classList.remove("is-active")' in close_fn
    assert "if (restoreFocus) els.effortListTrigger.focus();" in close_fn
    assert ".popover-row.is-active" in css
    # Selecting commits and closes the whole menu back to the pill.
    pick = re.search(r"function selectEffortAndClose\(step\)\s*\{.*?\n  \}",
                     js, re.S).group(0)
    assert "setEffort(step)" in pick
    assert "closePopovers()" in pick
    assert "els.modelTrigger.focus()" in pick
    # Keyboard contract: arrows/Home/End/Enter/Space on the listbox.
    handler = re.search(
        r'els\.effortList\.addEventListener\("keydown".*?\n  \}\);',
        js, re.S).group(0)
    for key in ('"ArrowDown"', '"ArrowUp"', '"Home"', '"End"',
                '"Enter"', '" "'):
        assert key in handler, key
    assert handler.count("event.preventDefault()") >= 4
    assert "selectEffortAndClose(" in handler
    # Escape unwinds one level at a time: an open effort list closes
    # alone, before the model-list and whole-hierarchy branches.
    esc = re.search(r"function handleEscapeKey\(\)\s*\{.*?\n  \}",
                    js, re.S).group(0)
    assert "closeEffortList(true)" in esc
    assert esc.index("closeEffortList(true)") < esc.index("closeModelList(true)")
    assert esc.index("closeModelList(true)") < esc.index("closeRoot()")
    # One sibling list at a time: opening either closes the other, and
    # closing Advanced closes both.
    open_model = re.search(r"function openModelList\(\)\s*\{.*?\n  \}",
                           js, re.S).group(0)
    assert "closeEffortList(false)" in open_model
    assert "closeModelList(false)" in open_fn
    close_adv = re.search(r"function closeAdvanced\(\)\s*\{.*?\n  \}",
                          js, re.S).group(0)
    assert "closeEffortList(false)" in close_adv
    # Outside clicks reach the effort list as an open surface.
    surface = re.search(r"function insideOpenSurface\(target\)\s*\{.*?\n  \}",
                        js, re.S).group(0)
    assert "els.effortList.contains(target)" in surface


# ------------------------------------------------------------ integration


async def test_console_request_shape_streams(make_client):
    """The exact payload the console sends — full multi-turn history with
    stream on — yields ordered deltas over SSE and a rendered multi-turn
    prompt on the backend side."""
    async with make_client() as (client, backend):
        backend.turn_scripts.append(
            [delta("Hel"), delta("lo!"), completed({"input_tokens": 1,
                                                    "output_tokens": 2})])
        resp = await client.post(CHAT_URL, json={
            "model": "gpt-5.2-codex",
            "stream": True,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "wave"},
            ],
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events, saw_done = parse_sse(resp.text)
        assert saw_done
        contents = [e["choices"][0]["delta"].get("content", "") for e in events]
        assert "".join(contents) == "Hello!"
        turn = next(p for m, p in backend.requests if m == "turn/start")
        assert turn["input"] == [{"type": "text", "text":
                                  "user: hi\nassistant: hello\nuser: wave"}]


async def test_console_error_surfaces_as_structured_body(make_client):
    """A not-ready backend produces the structured error the console parses
    into its error region."""
    config = make_config()
    backend = FakeBackend(config, ready=False)
    async with make_client(config=config, backend=backend) as (client, _):
        resp = await client.post(CHAT_URL, json={
            "model": "gpt-5.2-codex", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 503
        err = resp.json()["error"]
        assert set(err) == {"message", "type", "code"}
