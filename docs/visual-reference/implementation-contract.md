# Official ChatGPT replica implementation contract

Captured from the installed `chatgpt 26.825.51511` desktop app at a 1280×820 window on 2026-08-31. The PNGs beside this file are immutable visual evidence; implementation agents should use this text contract and must not ingest the PNG bytes.

## Reference hashes

- Empty Chat: `chatgpt-official-1280x820.png`, SHA-256 `22ce209602690dc086fa5fd8c986757d20c3050ab54e0eb44bce4d1ac7a38216`
- Finished Chat turn: `chatgpt-official-chat-populated-1280x820.png`, `a0e5ca16f96c55a96f52e136a75d7ac9929441731c51404bf7a90199b2e4cac4`
- Active stream: `chatgpt-official-chat-streaming-1280x820.png`, `a1b11e7cc214020fa5e9f57f9c5ccc0f5e20c3e6a7b86d45f1f45d42a9d83b7a`
- Model root: `chatgpt-official-model-menu-1280x820.png`, `ac4520ff5c243e69ae49b2bae037c2bb867b7d1102cba189bc315a5ca2b81e98`
- Advanced menu: `chatgpt-official-model-advanced-1280x820.png`, `e8689e1d891562bcd0ac2542acf6a7b89b98978b728c28f4fe29a8e2a80b9910`
- Model list: `chatgpt-official-model-list-1280x820.png`, `4b13cb4f678cb168b2c10014b38959ca531b97a2fa76e4e5e5b629d5713a2f9a`

## Desktop geometry and style

- Canvas: 1280×820, DPR 1, system UI font (`Arial`/`Segoe UI`/system sans), base text 14px, main text `#202123`, muted text near `#8e8e93`.
- Simulated native menu strip: y=0–35, white/near-white, in the official capture. The local replica removes this strip entirely (no File/Edit/View/Help, back/forward, or window controls); the shell fills the viewport from y=0 and the sidebar toggle lives in the sidebar brand row. The official sidebar rows Scheduled/Plugins/Explore/Work are likewise removed, not disabled.
- Sidebar: x=0–275 (target CSS width 276px), y=35–820, fill around `#f7f7f8`. Main starts x=276, white. Sidebar padding 8–16px. Brand row ~48px high; nav rows 31px high; section labels 14px muted; selected recent row fill near `#e9e9ea`, 10px radius. Bottom account row fixed near y=775, 45px high.
- Empty main: top-right utility icons at y≈60. The official Chat/Work segmented control is not reproduced in the local replica — Work is not available in a local console, so the toggle is removed entirely rather than rendered half-disabled.
- Empty heading: centered around x≈777, baseline y≈385, 28px normal weight. Exact pinned text: `Ready when you are.`
- Empty composer: x≈457–1099, y≈428–475, width≈642, height≈47, white, 1px `#e5e5e5`, 24px radius, subtle shadow. Plus at left, placeholder `Message ChatGPT`, model pill/control around x≈802–1027, mic and round black voice/send control at right.
- Optional bottom voice promo in the official capture is not part of core chat behavior and need not be reproduced.

## Popovers

- Root model popover: x≈801–1027, y≈473–555, 226×82 (CSS 226×80), white, 14px radius, thin border, soft shadow. Top half is a 5-step power slider (`role="slider"`, min 0 / max 4): track ≈814,486, 200×24 with five tick dots, 28×28 white thumb at x 814/857/900/943/986 for Instant/Medium/High/Extra High/Pro. Instant leaves a light-gray track; higher steps fill it with a purple→blue gradient. Left/Right arrows adjust when focused; screen-reader text like `Pro, 5 of 5. Use Left and Right arrow keys to adjust power`. Clicking a tick or the track selects that step. Bottom half is the `Advanced ›` row.
- Advanced popover: x≈801–1027, y≈474–580, about 226×106. Header `Advanced⌄`; rows `Model` with current display name and chevron, and an enabled `Effort` row with the current effort name (default `Pro`) and chevron. While its submenu is open the row stays highlighted, matching the Model-row treatment.
- Effort list submenu: opens RIGHT of the Advanced card at x≈1027–1207, y≈544–721, 180×177. Header `Effort`; five 29px rows in order Instant, Medium, High, Extra High, Pro; selected row has a right checkmark only (no permanent fill). Pointer, keyboard (Arrow/Home/End/Enter/Escape), focus restore, and outside-click/Escape dismissal mirror the model list. Changing effort syncs the slider thumb, the composer pill label, the Advanced Effort row value, and this checkmark; requests carry `"effort": "<label>"` and the selection lives only in page memory.
- Model list submenu: x≈520–802, y≈515–581, 282×66, two 33px rows in reference, selected row has right checkmark. Populate rows dynamically from all `/v1/models` IDs. Display mapping: strip `gpt-`, uppercase `GPT-`, title-case suffixes (`gpt-5.6-sol` → `GPT-5.6 Sol`); keep underlying ID unchanged.
- Settings utility popover should reuse this surface style and contain model reload, streaming toggle (default on), and backend health text.

## Populated and streaming states

- Main header y≈35–82 with 1px bottom rule. Finished Chat capture has Share/utility controls right. Current local chat title may derive from the first user prompt; do not copy screenshot history or private account labels.
- Conversation width≈736px, x≈409–1146. User bubble aligns right, black fill, white 14px text, 18px radius, 12×16px padding. Assistant response is unboxed left-aligned text at x≈410; action icon row below in muted gray. Status text such as `Worked for 26s` is muted.
- Populated composer fixed near x≈409–1146, y≈759–809, width≈737, height≈50, 24px radius. Bottom controls: plus left, placeholder, `Pro⌄`, mic, round 30px send control right.
- Active stream: conversation scroll follows newest content. Assistant starts with a small dark activity dot. Send control becomes a 30px black circle with a centered white square Stop icon. Stop uses AbortController and preserves partial text.

## Responsive contract

At 390×844: hide/collapse the persistent sidebar and native menu labels, retain a compact top row, keep transcript and composer within viewport, no horizontal overflow, all real controls reachable. A menu button may open the sidebar as an overlay.

## Visual acceptance gate

A 1:1 or pixel-parity claim requires a same-viewport candidate capture and explicit verdict for **every** pinned state above: empty Chat, finished Chat, active stream/Stop, root model menu, Advanced menu, and model list. Empty-state or static-test success does not cover interactive states. Model-menu acceptance must also exercise real account-model selection, parent/submenu persistence, selected checkmark treatment, dismissal, keyboard navigation, and narrow-screen bounds. Any uncaptured or failed state blocks the claim.

## Functional and security contract

Preserve same-origin `/health`, `/v1/models`, `/v1/chat/completions`; streaming default; model selection; reload; New chat/Clear; Enter send; Shift+Enter newline; Stop; visible structured errors; health polling; plain-text rendering via `textContent`; no `innerHTML`, cookies, browser storage, credentials, external URLs, dynamic code, or external runtime assets. Unsupported decorative ChatGPT features must be marked unavailable rather than fake success.
