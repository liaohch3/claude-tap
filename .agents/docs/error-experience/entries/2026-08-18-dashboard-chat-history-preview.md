---
date: 2026-08-18
area: dashboard
status: resolved
---

# Dashboard trace records showed the first historical user message

## What broke

The dashboard Trace tab displayed the first user message from a cumulative request instead of the message that
triggered the current API call. Chat Completions, Responses, and Gemini clients can resend conversation history, so
later records appeared to repeat the initial prompt or display an entire history.

## Diagnosis

The stored trace contained the history in the correct order, and the full viewer already selected the latest human
message. The dashboard renderer selected the first matching Gemini user content and flattened Responses input arrays.

## Fix

Scan Chat Completions messages, Responses input items, and Gemini contents on the server from newest to oldest,
returning the first non-empty human text after auxiliary-block filtering. Attach that derived text to each redacted
dashboard record so the Trace tab only renders the field instead of maintaining another prompt cleaner in the page.
The dashboard and full-viewer Python paths share the same cleaner for injected and Codex-specific wrappers. Cover the
behavior with Python extraction and payload tests plus a Chromium rendering test.

## Captured evidence

- [Dashboard before the fix](../../../evidence/pr/pi-latest-user-message/dashboard-pi-latest-message-before.png)
  shows later Pi turns repeating the initial `你好` request.
- [Pi terminal source conversation](../assets/pi-latest-user-message/pi-terminal-multiturn-source.png)
  confirms that the CLI had advanced to a later request; it is diagnostic context, not PR viewer evidence.
- [Dashboard after the fix](../../../evidence/pr/pi-latest-user-message/dashboard-pi-latest-message-after.png)
  shows the same later turns selecting `当前目录有几个文件夹?`.

## Lesson

Request previews must treat every supported request format as cumulative conversation history, not as a single-turn
input.
