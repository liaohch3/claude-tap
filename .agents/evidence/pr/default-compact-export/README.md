# Default Compact Export Evidence

Source: a real local claude-tap SQLite session was copied from the read-only source database into
`/tmp/claude-tap-compact-check/subset.sqlite3` before export and dashboard validation. The live local
claude-tap database was not used as the writable test database.

Validation:

- Exported session `77736052-6c05-4b3a-a836-ca4235717a3f` from the temporary subset database.
- Raw JSONL export from SQLite: `/tmp/claude-tap-compact-check/raw-compare/trace.raw.jsonl`, 363.18 MiB.
- Compact export: `/tmp/claude-tap-compact-check/raw-compare/trace.raw.ctap.json`, 67.71 MiB.
- HTML export: `/tmp/claude-tap-compact-check/trace.html`, 68.00 MiB, contains `EMBEDDED_TRACE_COMPACT_DATA`
  and no full `const EMBEDDED_TRACE_DATA =` injection.
- Normalized JSON view comparison: `/tmp/claude-tap-compact-check/trace.full.json`, 92.39 MiB. This is not
  the raw JSONL baseline; it is the explicit `--format json` viewer/export representation.
- Localhost dashboard screenshot: `dashboard-compact-export.png`.
