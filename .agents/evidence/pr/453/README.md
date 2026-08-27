# Issue #453 Evidence: dashboard lazy migration keeps cache-read buckets

## Provenance

Real trace-backed dashboard evidence produced with a real `claude-tap dashboard`
server (not a hand-written page or terminal capture):

1. Seeded an isolated trace database with a realistic Anthropic-shaped session
   (`POST /v1/messages`, 3 turns, `claude-sonnet-4-6`) whose only separate
   cache read (5,000 tokens) sits in the middle turn. The stored summary was
   rewritten into the pre-v4 legacy shape (`summary_version: 3`, no
   `cache_read_in_input_tokens` split), exactly reproducing issue #453.
2. Started the real server:
   `CLOUDTAP_DB=<isolated.sqlite3> uv run claude-tap dashboard --tap-live-port 19599 --tap-no-open`
3. Opened `http://127.0.0.1:19599/dashboard`, which performed the v3 -> v4
   lazy migration on first list.

## Result

`migration-cache-read-dashboard.png` shows the dashboard session list with
**5,480 total tokens** (middle-turn cache read preserved). Under the pre-fix
boundary heuristic the same migration reported 480 because the 5,000-token
cache bucket was re-bucketed as embedded from zero-usage boundary samples.

Post-screenshot database inspection confirmed the migration persisted
`summary_version: 4`, `cache_read_tokens: 5000`,
`cache_read_in_input_tokens: 0`, `total_tokens: 5480` (values 450/30/5000/0
input/output/cache-read/embedded, matching append-time aggregation).

Reproduction scripts used to seed the isolated database and capture the
screenshot are kept out of the evidence tree on purpose; the exact steps above
regenerate the image deterministically.
