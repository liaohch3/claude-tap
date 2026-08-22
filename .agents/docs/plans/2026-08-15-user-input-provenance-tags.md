---
status: completed
---

# User Input Provenance Tags Plan

Date: 2026-08-15

## Problem

Sidebar QUERY groups were named after text nobody typed. Real titles from a
local session included `THE USER STEPPED AWAY AND IS COMING BACK. REC...` and
`DIFF --GIT A/.AGENTS/DOCS/PLANS/...`.

The parsing was not wrong. `firstUserInputInfo` correctly returned the first
natural text on a `role: "user"` message. The problem is upstream of the viewer:
the Anthropic and OpenAI wire formats carry only `user` and `assistant` roles,
so a harness injection and a typed question are the same shape on the wire.
Everything the CLI injects on the user's behalf arrives as `role: "user"`:

- recap requests after the user steps away
- suggestion-mode prompts
- web search dispatches
- subagent briefs and result-summary asks
- context-compaction handoffs
- system reminders and local-command caveats
- interrupt notices
- image attachment metadata

Pasted payloads are a second, separate case: the human did send them, but a diff
header or a file body is not the sentence that names the turn.

## Approach

Infer authorship from the verbatim opener each template emits.

`classifyUserInputOrigin(text)` returns `{ origin, kind }` where origin is
`harness`, `payload`, or `human`. Every pattern is anchored at the start of the
text, so prose that merely mentions a diff or a function stays `human` — the
classifier must never reclassify someone asking "why does `function getPath`
return undefined here?".

Two orthogonal dimensions fall out of this: who authored the text (human vs
harness) and what form it takes (prose vs payload). A group title wants exactly
the human-and-prose cell.

## Changes

1. `sidebar.js`: `HARNESS_INPUT_PATTERNS`, `PAYLOAD_INPUT_PATTERNS`, and
   `classifyUserInputOrigin`.
2. `sidebar.js`: `firstUserInputInfo` and `latestUserInputInfo` take two passes
   and prefer human prose. Injected text still names the group when the turn has
   nothing else — an untitled group is worse than one named by its only content —
   and that case carries a badge saying so.
3. `renderers.js`: each non-human user message in the detail pane gets a badge.
   The probe falls back to `contentTextForSession` because
   `cleanUserPromptText` deliberately blanks reminder and suggestion text, which
   is right for titles but would hide the badge.
4. `viewer.css`: muted badge styles, plus `overflow-wrap: anywhere` on
   `.group-name`. A pasted path is one unbroken token that used to overflow its
   flex item and run underneath the badges beside it.
5. `viewer_i18n.json`: four keys across all eight languages.

## Validation

- `tests/test_viewer_js_units.py`: 10 harness samples with their kinds, 8 payload
  samples, 8 human samples including prose about code, null and empty input, plus
  the mixed-message and injected-only title cases. This test also now loads
  `sidebar.js` into its `vm` context, which it had been missing.
- `scripts/check_coverage.py`: the CSS collector now visits session sidebar order.
  Group-header selectors only exist in that mode, so they were unreachable and
  every one of them counted as a miss.
- `tests/test_viewer_contracts.py`: browser coverage for the human-preferring
  title, the badge on an injected-only title, the per-message badges, and the
  title-overflow geometry. The overflow assertion was checked against a reverted
  stylesheet and fails by 53px without the fix.
- Real session evidence in `.agents/evidence/pr/user-input-provenance/`, exported
  from session `d4944d46-8513-4f0e-b4a2-6171addf0e96` — the session whose titles
  prompted the report. 4 groups, 1 still tagged (that turn genuinely has no human
  prose), and the detail pane labels an interrupt notice and a recap request.

## Known limits

The classifier recognizes templates, so a harness that changes its wording ships
text this will call `human` until the pattern is added. That failure mode is the
old behavior, not a new one, and it is why nothing is ever discarded on the basis
of origin — a misread only costs a badge, never content.
