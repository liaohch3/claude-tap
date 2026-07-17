# SQLite log lock retry storm

Date: 2026-07-17

## What broke

A long-running Talon Codex proxy eventually returned `request timed out` for every turn. The proxy log contained a sustained stream of `sqlite3.OperationalError: database is locked` errors from `SQLiteLogHandler` while the shared trace database was under write contention.

The process had loaded claude-tap 0.1.107 before a newer executable was installed. Updating the executable on disk did not replace the already imported code in that process, so the failures continued until Talon restarted the proxy.

## Investigation

Broad journal searches produced too much notification traffic to identify the transition. Grouping terminal turn states and error notifications by UTC hour showed a clean boundary: successful turns fell to zero while `request timed out` failures became continuous. The claude-tap log then tied that boundary to repeated SQLite lock exceptions in the request logging path.

Comparing 0.1.107 with current `main` confirmed that the main branch already bounded SQLite lock waits and prevented storage errors from aborting proxy requests. A focused regression test exposed one remaining problem: the synchronous logging handler retried every log record immediately after a failure. Concurrent requests could therefore serialize repeated one-second waits on the event loop even though each individual failure was caught.

## Fix

After a SQLite logging failure, the handler skips auxiliary log persistence for one second before retrying. Trace record persistence keeps its existing behavior, and successful logging resumes automatically after the cooldown.

## Lesson

Catching an ancillary storage exception is not sufficient when the failed operation has a bounded but non-trivial wait. Hot synchronous paths also need retry pacing so concurrent failures cannot recreate an outage through accumulated latency.
