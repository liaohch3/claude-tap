---
date: 2026-08-18
area: dashboard
status: resolved
---

# Dashboard trace records showed the first historical user message

## What broke

The dashboard Trace tab displayed the first user message from an OpenAI Chat Completions request instead of the
message that triggered the current API call. Multi-turn clients such as Pi resend the full conversation in each
request, so later records appeared to repeat the initial prompt.

## Diagnosis

The stored trace contained both user messages in the correct order, and the full viewer already selected the latest
one. The dashboard's compact record renderer used `Array.find()`, which always selected the first matching user role.

## Fix

Scan the request messages from newest to oldest and return the first non-empty user content. Cover the behavior with
a JavaScript unit test and a Chromium integration test against the live dashboard session route.

## Lesson

Request previews must treat Chat Completions payloads as cumulative conversation history, not as single-turn inputs.
