/*
 * Local console for chatgpt-app-api, served by the same FastAPI process
 * that serves /health, /v1/models and /v1/chat/completions.
 *
 * Safety contract:
 *  - every user/model string is rendered with textContent; no HTML is ever
 *    built from conversation content;
 *  - no cookie, credential, token, or browser-storage access of any kind;
 *  - all requests are same-origin relative URLs against the loopback server.
 */
"use strict";

(function () {
  var HEALTH_POLL_MS = 10000;
  var MAX_INPUT_HEIGHT = 200;
  var TITLE_MAX = 40;
  var SVG_NS = "http://www.w3.org/2000/svg";

  var els = {
    health: document.getElementById("health"),
    healthText: document.getElementById("health-text"),
    modelSelect: document.getElementById("model-select"),
    refreshModels: document.getElementById("refresh-models"),
    streamToggle: document.getElementById("stream-toggle"),
    clearBtn: document.getElementById("clear-btn"),
    transcript: document.getElementById("transcript"),
    transcriptEmpty: document.getElementById("transcript-empty"),
    requestStatus: document.getElementById("request-status"),
    error: document.getElementById("error"),
    composer: document.getElementById("composer"),
    input: document.getElementById("composer-input"),
    sendBtn: document.getElementById("send-btn"),
    stopBtn: document.getElementById("stop-btn"),
    sidebar: document.getElementById("sidebar"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    menuBtn: document.getElementById("menu-btn"),
    chatTitle: document.getElementById("chat-title"),
    settingsBtn: document.getElementById("settings-btn"),
    settingsPopover: document.getElementById("settings-popover"),
    modelTrigger: document.getElementById("model-trigger"),
    modelTriggerLabel: document.getElementById("model-trigger-label"),
    modelPopover: document.getElementById("model-popover"),
    effortSlider: document.getElementById("effort-slider"),
    advancedTrigger: document.getElementById("advanced-trigger"),
    advancedPopover: document.getElementById("advanced-popover"),
    modelListTrigger: document.getElementById("model-list-trigger"),
    modelListCurrent: document.getElementById("model-list-current"),
    modelList: document.getElementById("model-list"),
    effortListTrigger: document.getElementById("effort-list-trigger"),
    effortListCurrent: document.getElementById("effort-list-current"),
    effortList: document.getElementById("effort-list"),
    currentRecentTitle: document.querySelector(
      ".recent-row.is-current .recent-title"
    ),
  };

  /** Conversation sent with every request: [{role: "user"|"assistant", content}]. */
  var conversation = [];
  var activeController = null; // AbortController for the in-flight request
  var modelsLoaded = false;
  var polling = false;

  // ------------------------------------------------------------ errors

  function ApiError(payload) {
    this.name = "ApiError";
    this.message = (payload && payload.message) || "unknown API error";
    this.type = payload && payload.type;
    this.code = payload && payload.code;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function describeError(err) {
    if (err instanceof ApiError) {
      var detail = [err.type, err.code].filter(Boolean).join("/");
      return detail ? err.message + " (" + detail + ")" : err.message;
    }
    if (err instanceof SyntaxError) {
      return "Malformed response from the server.";
    }
    if (err instanceof TypeError) {
      return "Network error: could not reach the local API.";
    }
    return err && err.message ? err.message : String(err);
  }

  function showError(text) {
    els.error.textContent = text;
    els.error.hidden = false;
  }

  function hideError() {
    els.error.textContent = "";
    els.error.hidden = true;
  }

  function setStatus(text) {
    els.requestStatus.textContent = text;
  }

  // ------------------------------------------------------------ health

  function setHealth(state, text) {
    els.health.className = "health " + state;
    els.healthText.textContent = text;
  }

  async function refreshHealth() {
    try {
      var resp = await fetch("/health");
      var data = await resp.json();
      if (resp.ok && data.ready) {
        var version = data.codexVersion ? " · codex " + data.codexVersion : "";
        setHealth("ok", "Backend ready" + version);
        return true;
      }
      setHealth("down", "Backend unavailable: " + (data.reason || "HTTP " + resp.status));
      return false;
    } catch (err) {
      setHealth("down", "Backend unreachable");
      return false;
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      var ready = await refreshHealth();
      if (ready && !modelsLoaded && !activeController) await loadModels();
    } finally {
      polling = false;
    }
  }

  // ------------------------------------------------------------ models

  /**
   * Display name for a model ID: strip the "gpt-" prefix, re-apply it
   * uppercased, and title-case dash-separated suffixes. The underlying
   * ID is never altered. "gpt-5.6-sol" -> "GPT-5.6 Sol".
   */
  function displayModelName(id) {
    if (id.indexOf("gpt-") !== 0) return id;
    var parts = id.slice(4).split("-");
    var out = parts.map(function (part, i) {
      if (i === 0) return part; // version segment, e.g. "5.6"
      return part.charAt(0).toUpperCase() + part.slice(1);
    });
    return "GPT-" + out.join(" ");
  }

  /**
   * Compact value shown inside the Advanced card: drop the leading "GPT-"
   * ("GPT-5.6 Sol" -> "5.6 Sol"). Listbox rows keep the full name.
   */
  function compactModelName(id) {
    var name = displayModelName(id);
    return name.indexOf("GPT-") === 0 ? name.slice(4) : name;
  }

  function makeCheckmark() {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "14");
    svg.setAttribute("height", "14");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.style.marginLeft = "auto";
    svg.style.flexShrink = "0";
    var mark = document.createElementNS(SVG_NS, "polyline");
    mark.setAttribute("points", "4 12.5 9.5 18 20 6.5");
    svg.appendChild(mark);
    return svg;
  }

  // ------------------------------------------------------------ effort
  //
  // Five-step power slider, 1:1 with the official desktop app. The
  // selection lives only in page memory (no cookies/storage) and is sent
  // as the "effort" field on each completion request.

  var EFFORT_LEVELS = ["Instant", "Medium", "High", "Extra High", "Pro"];
  var effortStep = EFFORT_LEVELS.length - 1; // default: Pro

  var effortTrack = els.effortSlider.querySelector(".effort-track");
  var effortFill = els.effortSlider.querySelector(".effort-fill");
  var effortThumb = els.effortSlider.querySelector(".effort-thumb");

  // The fill spans the full 200px track (left 0 → both rounded ends), not
  // the 14px-inset thumb range, so Pro covers the entire capsule and
  // Instant leaves it bare. Reparent it ahead of the range; ticks/thumb
  // stay in the range and paint above it.
  effortTrack.insertBefore(effortFill, effortTrack.firstChild);

  function effortRows() {
    return els.effortList.querySelectorAll(".effort-option[data-effort]");
  }

  /**
   * Reflect the current effort everywhere: slider thumb/fill + aria, the
   * composer pill label, the Advanced Effort row value, and the Effort
   * list checkmark/aria-selected.
   */
  function syncEffortUI() {
    var name = EFFORT_LEVELS[effortStep];
    var pct = (effortStep / (EFFORT_LEVELS.length - 1)) * 100 + "%";
    els.effortSlider.setAttribute("aria-valuenow", String(effortStep));
    els.effortSlider.setAttribute(
      "aria-valuetext",
      name + ", " + (effortStep + 1) + " of " + EFFORT_LEVELS.length +
        ". Use Left and Right arrow keys to adjust power"
    );
    effortThumb.style.left = pct;
    effortFill.style.width = pct;
    els.modelTriggerLabel.textContent = name;
    els.effortListCurrent.textContent = name;
    effortRows().forEach(function (row) {
      var selected = row.getAttribute("data-effort") === name;
      row.setAttribute("aria-selected", selected ? "true" : "false");
      var check = row.querySelector("svg");
      if (selected && !check) row.appendChild(makeCheckmark());
      if (!selected && check) check.remove();
    });
  }

  function setEffort(step) {
    step = Math.max(0, Math.min(EFFORT_LEVELS.length - 1, step));
    if (step === effortStep) return;
    effortStep = step;
    syncEffortUI();
  }

  /** Pick an effort from the list: commit it, close the whole menu. */
  function selectEffortAndClose(step) {
    setEffort(step);
    closePopovers();
    els.modelTrigger.focus();
  }

  /** Map a pointer x to the nearest step along the thumb's travel range. */
  function effortStepFromClientX(clientX) {
    var rect = effortTrack.getBoundingClientRect();
    var inset = 14; // thumb half-width: travel range is inset from the track
    var ratio = (clientX - rect.left - inset) / (rect.width - inset * 2);
    ratio = Math.max(0, Math.min(1, ratio));
    return Math.round(ratio * (EFFORT_LEVELS.length - 1));
  }

  els.effortSlider.addEventListener("keydown", function (event) {
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      setEffort(effortStep - 1);
    } else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      setEffort(effortStep + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      setEffort(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setEffort(EFFORT_LEVELS.length - 1);
    }
  });

  // Clicking a tick or the track selects that step; dragging scrubs it.
  var effortDragging = false;
  effortTrack.addEventListener("pointerdown", function (event) {
    effortDragging = true;
    try {
      effortTrack.setPointerCapture(event.pointerId);
    } catch (err) {
      /* capture unsupported: click still lands, drag degrades to click */
    }
    setEffort(effortStepFromClientX(event.clientX));
    els.effortSlider.focus();
  });
  effortTrack.addEventListener("pointermove", function (event) {
    if (effortDragging) setEffort(effortStepFromClientX(event.clientX));
  });
  effortTrack.addEventListener("pointerup", function () {
    effortDragging = false;
  });
  effortTrack.addEventListener("pointercancel", function () {
    effortDragging = false;
  });

  effortRows().forEach(function (row) {
    row.addEventListener("click", function () {
      selectEffortAndClose(EFFORT_LEVELS.indexOf(row.getAttribute("data-effort")));
    });
  });


  // Official-style response action row. The upstream endpoints behind
  // read-aloud / copy / regenerate / feedback do not exist in this local
  // replica, so every control is honestly rendered disabled and labelled
  // "(unavailable)" — never faked as working. Shapes are plain
  // [tagName, attributes] pairs built with createElementNS only.
  var RESPONSE_ACTIONS = [
    { label: "Read aloud", shapes: [
      ["polygon", { points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5" }],
      ["path", { d: "M15.5 8.5a5 5 0 0 1 0 7" }],
      ["path", { d: "M18.6 5.4a9 9 0 0 1 0 13.2" }],
    ] },
    { label: "Copy response", shapes: [
      ["rect", { x: "9", y: "9", width: "13", height: "13", rx: "2" }],
      ["path", { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" }],
    ] },
    { label: "Regenerate response", shapes: [
      ["polyline", { points: "23 4 23 10 17 10" }],
      ["path", { d: "M20.49 15a9 9 0 1 1-2.12-9.36L23 10" }],
    ] },
    { label: "Good response", shapes: [
      ["path", { d: "M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z" }],
      ["path", { d: "M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" }],
    ] },
    { label: "Bad response", shapes: [
      ["path", { d: "M10 15v4a3 3 0 0 0 3 3l4-9V2H6.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z" }],
      ["path", { d: "M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" }],
    ] },
  ];

  /** Build one 24px-stroke action icon from its shape spec. */
  function makeActionIcon(shapes) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    shapes.forEach(function (shape) {
      var el = document.createElementNS(SVG_NS, shape[0]);
      var attrs = shape[1];
      for (var name in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, name)) {
          el.setAttribute(name, attrs[name]);
        }
      }
      svg.appendChild(el);
    });
    return svg;
  }

  /**
   * Append the muted action row under a COMPLETED assistant message. Only
   * called after a successful (streamed or not) completion with content —
   * never while streaming, never after Stop or an error, never on user
   * turns. Clear removes it with the rest of the transcript.
   */
  function addActionRow(message) {
    var row = document.createElement("div");
    row.className = "message-actions";
    RESPONSE_ACTIONS.forEach(function (action) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "action-btn";
      btn.disabled = true;
      var label = action.label + " (unavailable)";
      btn.setAttribute("aria-label", label);
      btn.title = label;
      btn.appendChild(makeActionIcon(action.shapes));
      row.appendChild(btn);
    });
    message.appendChild(row);
  }

  /**
   * Reflect the selected model in the listbox rows and the Advanced value.
   * The composer pill's visible label stays "Pro" — the exact ID lives in
   * the hidden <select>, the Advanced row, and the pill's tooltip.
   */
  function syncModelUI() {
    var id = els.modelSelect.value;
    var rows = els.modelList.querySelectorAll(".model-option[data-model-id]");
    rows.forEach(function (row) {
      var selected = row.getAttribute("data-model-id") === id;
      row.setAttribute("aria-selected", selected ? "true" : "false");
      var check = row.querySelector("svg");
      if (selected && !check) row.appendChild(makeCheckmark());
      if (!selected && check) check.remove();
    });
    if (id) {
      els.modelListCurrent.textContent = compactModelName(id);
      els.modelTrigger.title = displayModelName(id);
    }
  }

  function selectModel(id) {
    if (els.modelSelect.value !== id) els.modelSelect.value = id;
    syncModelUI();
  }

  /** Pick a row: commit it, close the whole menu, refocus the composer pill. */
  function selectModelAndClose(id) {
    selectModel(id);
    closePopovers();
    els.modelTrigger.focus();
  }

  function setModelListPlaceholder(text) {
    var li = document.createElement("li");
    li.className = "model-option";
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", "false");
    li.setAttribute("aria-disabled", "true");
    li.textContent = text;
    els.modelList.replaceChildren(li);
  }

  async function loadModels() {
    var previous = els.modelSelect.value;
    els.refreshModels.disabled = true;
    try {
      var resp = await fetch("/v1/models");
      var data = await resp.json();
      if (!resp.ok) throw new ApiError(data && data.error);
      var ids = (Array.isArray(data.data) ? data.data : [])
        .map(function (m) { return m && typeof m.id === "string" ? m.id : ""; })
        .filter(Boolean);

      if (!ids.length) {
        var empty = new Option("No models available", "");
        empty.disabled = true;
        els.modelSelect.replaceChildren(empty);
        setModelListPlaceholder("No models available");
        els.modelListCurrent.textContent = "None available";
        modelsLoaded = false;
        showError("The account exposes no models.");
        return;
      }

      els.modelSelect.replaceChildren();
      var rows = ids.map(function (id) {
        els.modelSelect.append(new Option(id, id));
        var li = document.createElement("li");
        li.className = "model-option";
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        li.setAttribute("tabindex", "-1"); // roving focus, driven by keys
        li.setAttribute("data-model-id", id);
        var label = document.createElement("span");
        label.textContent = displayModelName(id);
        li.appendChild(label);
        li.addEventListener("click", function () {
          selectModelAndClose(id);
        });
        return li;
      });
      els.modelList.replaceChildren.apply(els.modelList, rows);

      if (ids.indexOf(previous) !== -1) els.modelSelect.value = previous;
      // With no prior selection the select defaults to the first option.
      syncModelUI();
      modelsLoaded = true;
      hideError();
    } catch (err) {
      var unavailable = new Option("Model list unavailable", "");
      unavailable.disabled = true;
      els.modelSelect.replaceChildren(unavailable);
      setModelListPlaceholder("Model list unavailable");
      els.modelListCurrent.textContent = "Unavailable";
      modelsLoaded = false;
      showError("Could not load models: " + describeError(err));
    } finally {
      els.refreshModels.disabled = false;
    }
  }

  // ------------------------------------------------------------ popovers
  //
  // Three-level hierarchy pinned to the reference capture:
  //   composer pill -> root card ("Advanced ›")
  //   root card     -> Advanced card (replaces the root card in place)
  //   Advanced card -> model listbox (opens beside it, never replaces it)

  var popoverTriggers = [
    els.settingsBtn,
    els.modelTrigger,
    els.advancedTrigger,
    els.modelListTrigger,
    els.effortListTrigger,
  ];

  function setExpanded(btn, open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function modelMenuOpen() {
    return (
      !els.modelPopover.hidden ||
      !els.advancedPopover.hidden ||
      !els.modelList.hidden ||
      !els.effortList.hidden
    );
  }

  function modelRows() {
    return els.modelList.querySelectorAll(".model-option[data-model-id]");
  }

  function selectedRow(rows) {
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("aria-selected") === "true") return rows[i];
    }
    return null;
  }

  /** Innermost surface closes first; Escape unwinds one level per press. */
  function closeModelList(restoreFocus) {
    if (els.modelList.hidden) return;
    els.modelList.hidden = true;
    setExpanded(els.modelListTrigger, false);
    els.modelListTrigger.classList.remove("is-active");
    if (restoreFocus) els.modelListTrigger.focus();
  }

  function closeEffortList(restoreFocus) {
    if (els.effortList.hidden) return;
    els.effortList.hidden = true;
    setExpanded(els.effortListTrigger, false);
    els.effortListTrigger.classList.remove("is-active");
    if (restoreFocus) els.effortListTrigger.focus();
  }

  function closeAdvanced() {
    closeModelList(false);
    closeEffortList(false);
    if (els.advancedPopover.hidden) return;
    els.advancedPopover.hidden = true;
    setExpanded(els.advancedTrigger, false);
  }

  function closeRoot() {
    closeAdvanced();
    // Unconditional: Advanced may already have replaced (hidden) the root
    // card — the pill's expanded state must still reset, and every close
    // path (selection, outside click, Escape) leaves both surfaces hidden.
    els.modelPopover.hidden = true;
    setExpanded(els.modelTrigger, false);
  }

  function closeSettings() {
    if (els.settingsPopover.hidden) return;
    els.settingsPopover.hidden = true;
    setExpanded(els.settingsBtn, false);
  }

  function closePopovers() {
    closeSettings();
    closeRoot();
  }

  /** Toggle a standalone popover (settings); the model menu closes first. */
  function togglePopover(popover, trigger) {
    var wasOpen = !popover.hidden;
    closePopovers();
    if (!wasOpen) {
      popover.hidden = false;
      setExpanded(trigger, true);
    }
  }

  /** Cards hang below the composer unless that would run off the viewport
   *  (populated chats, small screens) — then the hierarchy flips above.
   *  On <=700px viewports the open listbox stacks ABOVE the Advanced card
   *  (mobile .model-list rule), so hanging it downward always climbs back
   *  over the composer — forList forces the flip whatever room remains
   *  below. The 700 breakpoint must match the CSS media query. */
  function updateMenuDirection(forList) {
    var rect = els.composer.getBoundingClientRect();
    var roomBelow = rect.bottom + 130 <= window.innerHeight;
    var flip = !roomBelow || (forList && window.innerWidth <= 700);
    els.composer.classList.toggle("menu-up", flip);
  }

  function openRoot() {
    closePopovers();
    updateMenuDirection(false);
    els.modelPopover.hidden = false;
    setExpanded(els.modelTrigger, true);
    els.advancedTrigger.focus();
  }

  /** The Advanced card replaces the root card at the same anchor. */
  function openAdvanced() {
    closeModelList(false);
    closeEffortList(false);
    updateMenuDirection(false);
    els.modelPopover.hidden = true;
    els.advancedPopover.hidden = false;
    setExpanded(els.advancedTrigger, true);
    els.modelListTrigger.focus();
  }

  /** Open the listbox beside Advanced — Advanced stays visible and its
   *  Model row stays highlighted while the submenu is open. */
  function openModelList() {
    closeEffortList(false);
    updateMenuDirection(true);
    els.modelList.hidden = false;
    setExpanded(els.modelListTrigger, true);
    els.modelListTrigger.classList.add("is-active");
    var rows = modelRows();
    var row = selectedRow(rows);
    (row || rows[0] || els.modelListTrigger).focus();
  }

  /** Open the effort listbox RIGHT of Advanced — Advanced stays visible
   *  and its Effort row stays highlighted while the submenu is open. */
  function openEffortList() {
    closeModelList(false);
    updateMenuDirection(true);
    els.effortList.hidden = false;
    setExpanded(els.effortListTrigger, true);
    els.effortListTrigger.classList.add("is-active");
    var rows = effortRows();
    var row = selectedRow(rows);
    (row || rows[0] || els.effortListTrigger).focus();
  }

  function insideAnyTrigger(target) {
    return popoverTriggers.some(function (btn) {
      return btn.contains(target);
    });
  }

  function insideOpenSurface(target) {
    return (
      (!els.settingsPopover.hidden && els.settingsPopover.contains(target)) ||
      (!els.modelPopover.hidden && els.modelPopover.contains(target)) ||
      (!els.advancedPopover.hidden && els.advancedPopover.contains(target)) ||
      (!els.modelList.hidden && els.modelList.contains(target)) ||
      (!els.effortList.hidden && els.effortList.contains(target))
    );
  }

  els.settingsBtn.addEventListener("click", function () {
    togglePopover(els.settingsPopover, els.settingsBtn);
  });

  // The pill toggles the whole hierarchy: open the root card, or close
  // every level if any of it is showing.
  els.modelTrigger.addEventListener("click", function () {
    if (modelMenuOpen()) closeRoot();
    else openRoot();
  });

  els.advancedTrigger.addEventListener("click", openAdvanced);

  els.modelListTrigger.addEventListener("click", function () {
    if (els.modelList.hidden) openModelList();
    else closeModelList(true);
  });

  els.effortListTrigger.addEventListener("click", function () {
    if (els.effortList.hidden) openEffortList();
    else closeEffortList(true);
  });

  // Listbox keys: arrows/Home/End move focus (wrapping), Enter/Space pick
  // the focused row. Escape is unwound one level by handleEscapeKey below.
  els.modelList.addEventListener("keydown", function (event) {
    var rows = modelRows();
    if (!rows.length) return;
    var index = -1;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] === document.activeElement) {
        index = i;
        break;
      }
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      var step = event.key === "ArrowDown" ? 1 : -1;
      rows[(index + step + rows.length) % rows.length].focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      rows[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      rows[rows.length - 1].focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      var row = index >= 0 ? rows[index] : selectedRow(rows) || rows[0];
      selectModelAndClose(row.getAttribute("data-model-id"));
    }
  });

  // Effort listbox keys mirror the model listbox: arrows/Home/End move
  // focus (wrapping), Enter/Space pick the focused row. Escape is unwound
  // one level by handleEscapeKey below.
  els.effortList.addEventListener("keydown", function (event) {
    var rows = effortRows();
    if (!rows.length) return;
    var index = -1;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] === document.activeElement) {
        index = i;
        break;
      }
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      var step = event.key === "ArrowDown" ? 1 : -1;
      rows[(index + step + rows.length) % rows.length].focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      rows[0].focus();
    } else if (event.key === "End") {
      event.preventDefault();
      rows[rows.length - 1].focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      var row = index >= 0 ? rows[index] : selectedRow(rows) || rows[0];
      selectEffortAndClose(EFFORT_LEVELS.indexOf(row.getAttribute("data-effort")));
    }
  });

  // ------------------------------------------------------------ sidebar

  function setSidebar(open) {
    document.body.classList.toggle("sidebar-open", open);
    els.menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    els.sidebarToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function toggleSidebar() {
    setSidebar(!document.body.classList.contains("sidebar-open"));
  }

  els.menuBtn.addEventListener("click", toggleSidebar);
  els.sidebarToggle.addEventListener("click", toggleSidebar);

  // Dismiss popovers and the mobile sidebar on outside clicks; the mobile
  // overlay is a body::after layer, so its clicks land here too.
  document.addEventListener("click", function (event) {
    if (
      (modelMenuOpen() || !els.settingsPopover.hidden) &&
      !insideOpenSurface(event.target) &&
      !insideAnyTrigger(event.target)
    ) {
      closePopovers();
    }
    if (
      document.body.classList.contains("sidebar-open") &&
      !els.sidebar.contains(event.target) &&
      !els.menuBtn.contains(event.target) &&
      !els.sidebarToggle.contains(event.target)
    ) {
      setSidebar(false);
    }
  });

  /** Escape unwinds the model hierarchy: an open list (effort or model)
   *  closes alone with focus back on its Advanced row; from Advanced — or
   *  the bare root — the whole hierarchy closes at once (focus back on
   *  the composer pill), so there is no dead third press. Then settings,
   *  then the sidebar. */
  function handleEscapeKey() {
    if (!els.effortList.hidden) closeEffortList(true);
    else if (!els.modelList.hidden) closeModelList(true);
    else if (modelMenuOpen()) {
      closeRoot();
      els.modelTrigger.focus();
    } else if (!els.settingsPopover.hidden) closeSettings();
    else if (document.body.classList.contains("sidebar-open")) setSidebar(false);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") handleEscapeKey();
  });

  // ------------------------------------------------------------ transcript

  function nearBottom() {
    var t = els.transcript;
    return t.scrollHeight - t.scrollTop - t.clientHeight < 96;
  }

  function scrollTranscript() {
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  /** Safe single-line chat label derived from the first user prompt. */
  function chatLabel(text) {
    var flat = text.replace(/\s+/g, " ").trim();
    return flat.length > TITLE_MAX ? flat.slice(0, TITLE_MAX - 1) + "…" : flat;
  }

  function setChatTitle(text) {
    els.chatTitle.textContent = text;
    if (els.currentRecentTitle) els.currentRecentTitle.textContent = text;
  }

  /** Append a message bubble; returns its text node container. */
  function addMessage(role, text) {
    els.transcriptEmpty.hidden = true;
    document.body.classList.add("has-messages");
    var li = document.createElement("li");
    li.className = "message " + role;
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = role === "user" ? "You" : "Assistant";
    var body = document.createElement("div");
    body.className = "body";
    body.textContent = text;
    li.append(who, body);
    var stick = nearBottom();
    els.transcript.append(li);
    if (stick) scrollTranscript();
    return body;
  }

  function appendText(body, chunk) {
    var stick = nearBottom();
    body.textContent += chunk;
    if (stick) scrollTranscript();
  }

  function dropEmptyMessage(body) {
    if (body.textContent) return;
    var message = body.closest(".message");
    if (message) message.remove();
    if (!els.transcript.querySelector(".message")) {
      els.transcriptEmpty.hidden = false;
      document.body.classList.remove("has-messages");
    }
  }

  function clearConversation() {
    conversation = [];
    els.transcript.replaceChildren(els.transcriptEmpty);
    els.transcriptEmpty.hidden = false;
    document.body.classList.remove("has-messages");
    setChatTitle("New chat");
    hideError();
    setStatus("Conversation cleared.");
  }

  // ------------------------------------------------------------ requests

  function setActive(active) {
    // Idle: round send control visible; active: send hidden, Stop shown
    // (CSS hides .stop-btn while disabled).
    els.sendBtn.disabled = active;
    els.sendBtn.hidden = active;
    els.stopBtn.disabled = !active;
    els.clearBtn.disabled = active;
    els.modelSelect.disabled = active;
    els.refreshModels.disabled = active;
    els.streamToggle.disabled = active;
    els.composer.classList.toggle("busy", active);
  }

  /** Apply one SSE block; returns true after [DONE]. Throws on error events. */
  function applyEventBlock(block, onDelta) {
    var lines = block.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].replace(/\r$/, "");
      if (line.indexOf("data:") !== 0) continue; // comments / blank lines
      var payload = line.slice(5).trim();
      if (payload === "[DONE]") return true;
      var event;
      try {
        event = JSON.parse(payload);
      } catch (err) {
        continue; // tolerate non-JSON keep-alive noise
      }
      if (event.error) throw new ApiError(event.error);
      var choice = event.choices && event.choices[0];
      var delta = choice && choice.delta;
      if (delta && typeof delta.content === "string" && delta.content) {
        onDelta(delta.content);
      }
    }
    return false;
  }

  /** Read an SSE body, forwarding content deltas; returns the full text. */
  async function consumeStream(resp, body) {
    var received = "";
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    for (;;) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        var block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        var finished = applyEventBlock(block, function (text) {
          received += text;
          appendText(body, text);
        });
        if (finished) return received;
        sep = buffer.indexOf("\n\n");
      }
    }
    return received;
  }

  async function requestCompletion(body) {
    var payload = {
      model: els.modelSelect.value,
      effort: EFFORT_LEVELS[effortStep],
      messages: conversation.map(function (m) {
        return { role: m.role, content: m.content };
      }),
    };
    if (els.streamToggle.checked) payload.stream = true;

    var resp = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: activeController.signal,
    });
    if (!resp.ok) {
      var errBody = null;
      try {
        errBody = await resp.json();
      } catch (err) {
        /* empty or non-JSON error body */
      }
      throw new ApiError(errBody && errBody.error);
    }
    if (payload.stream) return consumeStream(resp, body);

    var data = await resp.json();
    var choice = data.choices && data.choices[0];
    var text =
      choice && choice.message && typeof choice.message.content === "string"
        ? choice.message.content
        : "";
    if (text) appendText(body, text);
    return text;
  }

  async function send() {
    var text = els.input.value.trim();
    if (!text) {
      els.input.focus();
      return;
    }
    if (!els.modelSelect.value) {
      showError("Pick a model first — try “Reload models”.");
      return;
    }

    hideError();
    closePopovers();
    if (!conversation.length) setChatTitle(chatLabel(text));
    conversation.push({ role: "user", content: text });
    addMessage("user", text);
    els.input.value = "";
    resizeInput();

    setActive(true);
    setStatus(els.streamToggle.checked ? "Streaming reply…" : "Waiting for reply…");
    activeController = new AbortController();
    var body = addMessage("assistant", "");
    body.classList.add("pending");

    var received = "";
    try {
      received = await requestCompletion(body);
      setStatus("Done.");
      // Successful completion only: Stop/abort and error paths skip this.
      if (received) addActionRow(body.closest(".message"));
    } catch (err) {
      if (err && err.name === "AbortError") {
        setStatus("Stopped.");
      } else {
        showError(describeError(err));
        setStatus("Request failed.");
      }
    } finally {
      body.classList.remove("pending");
      dropEmptyMessage(body);
      if (received) conversation.push({ role: "assistant", content: received });
      setActive(false);
      activeController = null;
      els.input.focus();
      refreshHealth();
    }
  }

  // ------------------------------------------------------------ composer

  function resizeInput() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, MAX_INPUT_HEIGHT) + "px";
  }

  els.composer.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!els.sendBtn.disabled) send();
  });

  els.input.addEventListener("keydown", function (event) {
    // Enter sends, Shift+Enter inserts a newline; leave IME composition alone.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!els.sendBtn.disabled) send();
    }
  });

  els.input.addEventListener("input", resizeInput);

  els.stopBtn.addEventListener("click", function () {
    if (activeController) activeController.abort();
  });

  els.clearBtn.addEventListener("click", clearConversation);
  els.refreshModels.addEventListener("click", loadModels);
  els.modelSelect.addEventListener("change", syncModelUI);

  // ------------------------------------------------------------ start

  syncEffortUI();
  poll();
  setInterval(poll, HEALTH_POLL_MS);
  resizeInput();
  els.input.focus();
})();
