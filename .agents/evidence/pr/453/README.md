# Issue #453 Evidence: dashboard lazy migration keeps cache-read buckets

Real-trace evidence only. No synthetic seeding is involved anywhere in this
chain (the previously committed `migration-cache-read-dashboard.png` was a
seeded mock and has been removed per the repository evidence policy).

## Provenance

1. Source database: the maintainer's real claude-tap store produced by actual
   Claude Code usage (`~/.local/share/claude-tap/traces.sqlite3`, last
   modified 2026-06-25, untouched by this PR's work). SHA256 of the source
   and every derived artifact is recorded in
   `.traces/evidence-453/provenance.txt`.
2. A verbatim copy of that database was placed at
   `.traces/evidence-453/claude-real-traces.sqlite3` and served by the real
   server via an isolated DB path:
   `CLOUDTAP_DB=.traces/evidence-453/claude-real-traces.sqlite3 uv run claude-tap dashboard --tap-live-port 19599 --tap-no-open`
3. The key session is `26a7fe68-83d7-435d-a014-e7b813bb49d4` (19 real records,
   one Claude Code task with JSON parser output; cache reads ranging from 0 to
   55,296 tokens per turn, including zero-usage boundary turns on both sides
   of non-zero ones - exactly the shape issue #453 corrupted).

## BEFORE state

As persisted at real usage time under the pre-fix code (stored v2 summary,
which itself had been written while the boundary heuristic was active):
`summary_version=2`, `cache_read_tokens=437504`, `total_tokens=553303`.

## AFTER state

Opening the dashboard performed the version-5 lazy migration and re-persisted:

- `summary_version=5`
- `cache_read_tokens=437504` (unchanged - nothing collapsed into embedded)
- `total_tokens=553303` (unchanged)
- viewer stats bar: Cache R 437,504 / In 71,937 / Out 43,862 / hit rate 86%

Both screenshots were captured against `http://127.0.0.1:19599/dashboard`
with Playwright; text assertions (`553,303`, `JSON parser`, `437,504`) passed
before each capture.

- `real-trace-dashboard.png`: session list showing 553,303 total tokens.
- `real-trace-viewer-cache-read.png`: full viewer for `26a7fe68...` showing
  the preserved cache-read bucket in the stats bar.

The screenshot capture script is intentionally kept out of the evidence tree;
`provenance.txt` plus the steps above regenerate everything deterministically.

## Why the numbers cannot be a coincidence

Under the pre-fix boundary heuristic this exact session shape lost its entire
separate cache-read bucket (zero-usage first turn re-bucketed it as embedded),
so a healthy migration here must reproduce 437,504 exactly. The provenance
file additionally records the SQLite WAL checkpoint hash transition of the
copy (a17818f4... -> b4e9c99e...) proving the only mutation was the summary
rewrite performed by the migration itself.
