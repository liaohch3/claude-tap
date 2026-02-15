#!/bin/bash
# Run E2E test with fake upstream, then regenerate HTML from real trace and open it.
set -e

cd "$(dirname "$0")"

echo "=== Running E2E test ==="
uv run python test_e2e.py

# Regenerate HTML from real trace data (if exists)
JSONL=$(ls -t traces/*.jsonl 2>/dev/null | head -1)
if [ -n "$JSONL" ]; then
  echo ""
  echo "=== Regenerating HTML from $JSONL ==="
  uv run python -c "
from claude_tap import _generate_html_viewer
from pathlib import Path
t = Path('$JSONL')
h = t.with_suffix('.html')
_generate_html_viewer(t, h)
print(f'Generated: {h}')
"
  HTML="${JSONL%.jsonl}.html"
  echo "=== Opening $HTML ==="
  open "$HTML"
else
  echo "No real trace JSONL found in traces/. Skipping HTML regeneration."
fi
